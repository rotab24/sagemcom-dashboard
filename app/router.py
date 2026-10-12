"""
Capa de alto nivel: traduce los xpaths del router a datos limpios para el
dashboard. Los xpaths salen de $.xpaths en scripts.js (perfil F5657).
"""

import contextvars
import copy
import json
import re
import threading
import time
from typing import Any

from sagemcom import SagemcomClient, SagemcomError
from sagemcom.mock import MockClient

from .config import settings

IP_DATA = 'Device/IP/Interfaces/Interface[Alias="IP_DATA"]'
OPTICAL = 'Device/Optical/Interfaces/Interface[@uid="1"]'


def make_client():
    if settings.mock:
        return MockClient()
    if not settings.password:
        raise RuntimeError("Falta SAGEMCOM_PASS en .env (o pon SAGEMCOM_MOCK=1 para probar sin router)")
    return SagemcomClient(settings.host, settings.user, settings.password, scheme=settings.scheme)


_client = None


def get_client():
    """El cliente se crea al primer uso, así la app arranca aunque falte la contraseña."""
    global _client
    if _client is None:
        _client = _invalidate_on_write(make_client())
    return _client


# ---- caché de lecturas ------------------------------------------------------
# El servidor web del router es lento y atiende una petición cada vez: si el
# navegador (cada 15 s, una vez por pestaña abierta) y el recolector leen lo
# mismo por separado, el router se satura y acaba cortando conexiones.

_cache: dict[str, tuple[float, int, Any]] = {}
_fetch_locks: dict[str, threading.Lock] = {}
_generation = 0  # cambia con cada escritura: descarta lecturas que empezaron antes


def invalidate_cache():
    global _generation
    _generation += 1
    _cache.clear()


def cached(key: str, ttl: float, fn):
    """Devuelve el resultado de fn() reutilizándolo durante ttl segundos. Si
    varias peticiones llegan a la vez, solo una lee del router."""
    if ttl <= 0:
        return fn()
    with _fetch_locks.setdefault(key, threading.Lock()):
        hit = _cache.get(key)
        if hit and hit[1] == _generation and time.monotonic() - hit[0] < ttl:
            return copy.deepcopy(hit[2])
        gen = _generation
        value = fn()
        if gen == _generation:
            _cache[key] = (time.monotonic(), gen, value)
        return copy.deepcopy(value)


_WRITE_METHODS = ("remote_call", "set_password")


# ---- comprobación de escrituras -------------------------------------------------
# Que el router responda "OK" a un setValue no garantiza que el valor haya
# cambiado: tras cada escritura se vuelve a leer y se compara. Si no coincide,
# la acción falla con un error claro en vez de dar un falso "hecho".

WRITE_ONLY = {"_XMO_WRITE_ONLY_", "_XMO_UNDEFINED_WRITE_ONLY_"}
VERIFY_RETRIES = (0.0, 0.7, 1.5)  # el router a veces tarda un instante en reflejarlo
XMO_UNKNOWN_PATH_ERR = 16777242
verified_writes: contextvars.ContextVar[list | None] = contextvars.ContextVar("verified_writes", default=None)


class NotApplied(SagemcomError):
    """El router aceptó la escritura pero al releer no tiene el valor pedido."""


def _norm(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v))
    if isinstance(v, str):
        s = v.strip().replace(", ", ",")
        if s.lower() in ("true", "false"):
            return s.lower()
        try:
            return repr(float(s))
        except ValueError:
            return s.lower()  # MACs, alias… el router puede cambiar mayúsculas
    return json.dumps(v, sort_keys=True, default=str)


def same_value(written, read) -> bool:
    if isinstance(read, str) and read in WRITE_ONLY:
        return True  # no se puede releer (contraseñas): se da por buena
    if read is None:
        return written is None or written == ""
    if isinstance(written, bool) and _norm(read) in ("1.0", "0.0"):
        return written == (_norm(read) == "1.0")
    return _norm(written) == _norm(read)


def _short(xpath: str) -> str:
    return "/".join(xpath.split("/")[-2:])


def _note_verified(n: int):
    lst = verified_writes.get()
    if lst is not None:
        lst.append(n)


def _verify_values(client, values: dict):
    names = {f"v{i}": xp for i, xp in enumerate(values)}
    bad = []
    for delay in VERIFY_RETRIES:
        time.sleep(delay)
        read = client.get_values(names)
        bad = [(xp, values[xp], read[k]) for k, xp in names.items() if not same_value(values[xp], read[k])]
        if not bad:
            _note_verified(len(values))
            return
    detail = "; ".join(f"{_short(xp)} sigue en {r!r} (pedido {w!r})" for xp, w, r in bad[:4])
    raise NotApplied(f"El router aceptó el cambio pero no se ha aplicado: {detail}")


def _verify_added(client, parent: str, value: dict):
    """Busca en la colección un elemento con los valores que se acaban de añadir."""
    (item,) = value.values()
    scalars = {k: v for k, v in item.items() if not isinstance(v, (dict, list))}
    for delay in VERIFY_RETRIES:
        time.sleep(delay)
        children = [c for c in as_list(client.get_value(parent)) if isinstance(c, dict)]
        if any(all(same_value(v, c.get(k)) for k, v in scalars.items()) for c in children):
            _note_verified(1)
            return
    raise NotApplied(f"El router aceptó el alta en {_short(parent)} pero no aparece al releer")


def _verify_deleted(client, xpath: str):
    for delay in VERIFY_RETRIES:
        time.sleep(delay)
        try:
            if client.get_value(xpath) in (None, [], {}):
                break
        except SagemcomError as e:
            if e.code == XMO_UNKNOWN_PATH_ERR:
                break
            return  # no se puede comprobar: no se inventa un fallo
    else:
        raise NotApplied(f"El router aceptó el borrado pero {_short(xpath)} sigue existiendo")
    _note_verified(1)


def _invalidate_on_write(client):
    """Tras cualquier escritura (desde cualquier módulo) se olvida la caché y,
    salvo verify=False (disparadores como lanzar un escaneo), se comprueba."""
    for name in _WRITE_METHODS:
        orig = getattr(client, name, None)
        if orig is None:
            continue

        def wrapper(*args, _orig=orig, **kwargs):
            try:
                return _orig(*args, **kwargs)
            finally:
                invalidate_cache()
        setattr(client, name, wrapper)

    orig_set_values, orig_add, orig_delete = client.set_values, client.add_child, client.delete_child

    def set_values(values: dict, verify: bool = True):
        try:
            orig_set_values(values)
        finally:
            invalidate_cache()
        if verify:
            _verify_values(client, values)

    def set_value(xpath: str, value, verify: bool = True):
        set_values({xpath: value}, verify=verify)

    def add_child(parent_xpath: str, value: dict, verify: bool = True):
        try:
            result = orig_add(parent_xpath, value)
        finally:
            invalidate_cache()
        if verify:
            _verify_added(client, parent_xpath, value)
        return result

    def delete_child(xpath: str, verify: bool = True):
        try:
            orig_delete(xpath)
        finally:
            invalidate_cache()
        if verify:
            _verify_deleted(client, xpath)

    client.set_values, client.set_value = set_values, set_value
    client.add_child, client.delete_child = add_child, delete_child

    orig_request = client.request

    def request(actions, *args, **kwargs):
        try:
            return orig_request(actions, *args, **kwargs)
        finally:
            if any(a.get("method") != "getValue" for a in actions):
                invalidate_cache()
    client.request = request
    return client


# ---- helpers para la forma de los datos de XMO ------------------------------

def as_list(value) -> list:
    """Las colecciones (Device/Hosts/Hosts, ...) pueden venir como lista o
    envueltas en un dict de un único elemento, p. ej. {"Host": [...]}."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        inner = [v for v in value.values() if isinstance(v, list)]
        if len(value) == 1 and inner:
            return inner[0]
        return [value]
    return [value]


def field(obj: dict, *names: str, default=None) -> Any:
    """Busca un campo sin distinguir mayúsculas ni guiones bajos
    (PhysAddress / physAddress / phys_address)."""
    norm = {k.replace("_", "").lower(): v for k, v in obj.items()}
    for n in names:
        v = norm.get(n.replace("_", "").lower())
        if v not in (None, ""):
            return v
    return default


def to_bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "up", "enabled")
    return bool(v)


def to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- lecturas ----------------------------------------------------------------

def device_info() -> dict:
    v = get_client().get_values({
        "model": "Device/DeviceInfo/ModelName",
        "product_class": "Device/DeviceInfo/ProductClass",
        "software": "Device/DeviceInfo/SoftwareVersion",
        "hardware": "Device/DeviceInfo/HardwareVersion",
        "uptime": "Device/DeviceInfo/UpTime",
        "mem_total": "Device/DeviceInfo/MemoryStatus/Total",
        "mem_free": "Device/DeviceInfo/MemoryStatus/Free",
    })
    total, free = to_num(v["mem_total"]), to_num(v["mem_free"])
    v["mem_used_pct"] = round(100 * (1 - free / total), 1) if total and free is not None else None
    v["uptime"] = to_num(v["uptime"])
    return v


def wan() -> dict:
    v = get_client().get_values({
        "status": f"{IP_DATA}/Status",
        "ipv4": f'{IP_DATA}/IPv4Addresses/IPv4Address[Alias="IP_DATA_ADDRESS"]/IPAddress',
        "bytes_rx": f"{IP_DATA}/Stats/BytesReceived",
        "bytes_tx": f"{IP_DATA}/Stats/BytesSent",
    })
    v["bytes_rx"] = to_num(v["bytes_rx"])
    v["bytes_tx"] = to_num(v["bytes_tx"])
    v["up"] = to_bool(v["status"])
    return v


def gpon() -> dict:
    v = get_client().get_values({
        "status": f"{OPTICAL}/Status",
        "state": "Device/Optical/G988/GponState",
        "rx_dbm": f"{OPTICAL}/OpticalSignalLevel",
        "tx_dbm": f"{OPTICAL}/TransmitOpticalLevel",
        "temperature": f"{OPTICAL}/Temperature",
        "voltage": f"{OPTICAL}/Voltage",
        "bias": f"{OPTICAL}/BIASCurrent",
    })
    # El GUI divide entre 1000 los niveles ópticos (factory "Gpon" en scripts.js)
    for k in ("rx_dbm", "tx_dbm"):
        n = to_num(v[k])
        v[k] = round(n / 1000, 2) if n is not None else None
    # Este firmware da la temperatura en milésimas de °C, el voltaje en µV y la
    # corriente de polarización en µA (p. ej. 38183, 3184800, 12992)
    for k, div, nd in (("temperature", 1000, 1), ("voltage", 1_000_000, 2), ("bias", 1000, 1)):
        n = to_num(v[k])
        v[k] = round(n / div, nd) if n is not None else None
    return v


MAC_RE = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.I)


def host_name(h: dict, mac: str) -> str:
    """UserFriendlyName vale la MAC mientras nadie le pone nombre en el router,
    así que se salta si parece una MAC."""
    for n in ("UserHostName", "UserFriendlyName", "HostName"):
        v = field(h, n, default="")
        if v and not MAC_RE.match(v):
            return v
    return mac


DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def empty_grid() -> dict[str, list[bool]]:
    return {d: [False] * 24 for d in DAYS}


def grid_from_schedule(schedule) -> dict[str, list[bool]]:
    """BlacklistedSchedule -> {"MONDAY": [24 bool], ...}; True = acceso denegado."""
    grid = empty_grid()
    for day in as_list(schedule):
        if isinstance(day, dict) and field(day, "DayIndex") in grid:
            grid[field(day, "DayIndex")] = [to_bool(field(day, f"Hours{h}", default=False)) for h in range(24)]
    return grid


def block_state(grid: dict[str, list[bool]]) -> str:
    """'always' = bloqueado todas las horas, 'schedule' = algunas, 'none' = nunca."""
    n = sum(sum(hours) for hours in grid.values())
    return "always" if n == 7 * 24 else "schedule" if n else "none"


def hosts() -> list[dict]:
    return cached("hosts", settings.cache_ttl, _read_hosts)


def _read_hosts() -> list[dict]:
    out = []
    for h in as_list(get_client().get_value("Device/Hosts/Hosts")):
        if not isinstance(h, dict):
            continue
        mac = (field(h, "PhysAddress", default="") or "").lower()
        out.append({
            "uid": field(h, "uid"),
            "mac": mac,
            "ip": field(h, "IPAddress", default=""),
            "name": host_name(h, mac),
            "active": to_bool(field(h, "Active", default=False)),
            "interface": field(h, "InterfaceType", default=""),
            "source": field(h, "AddressSource", default=""),
            "type": field(h, "DetectedDeviceType", default=""),
            "custom_name": bool(field(h, "UserFriendlyName", default="")) and not MAC_RE.match(field(h, "UserFriendlyName", default="")),
            "block": block_state(grid_from_schedule(field(h, "BlacklistedSchedule", default=[]))),
        })
    out.sort(key=lambda h: (not h["active"], h["name"].lower()))
    return out


# Redes internas del mesh de MásMóvil: apagarlas puede romper los extensores
INTERNAL_SSIDS = {"WL_BACKHAUL", "WL_LIGHTHOUSE"}


def _ref_alias(ref: str) -> str:
    """'Device/WiFi/SSIDs/SSID[WL_PRIV]' -> 'WL_PRIV'"""
    m = re.search(r"\[([^\]]+)\]$", ref or "")
    return m.group(1).strip("'\"") if m else ""


def access_points() -> dict[str, dict]:
    """Puntos de acceso indexados por el alias del SSID al que sirven."""
    out = {}
    for a in as_list(get_client().get_value("Device/WiFi/AccessPoints")):
        if isinstance(a, dict):
            out[_ref_alias(field(a, "SSIDReference", default=""))] = {
                "alias": field(a, "Alias"),
                "enabled": to_bool(field(a, "Enable", default=False)),
            }
    return out


def wifi() -> dict:
    aps = access_points()
    ssids = []
    for s in as_list(get_client().get_value("Device/WiFi/SSIDs")):
        if not isinstance(s, dict):
            continue
        alias = field(s, "Alias")
        ap = aps.get(alias, {})
        ssid_on = to_bool(field(s, "Enable", default=False))
        ssids.append({
            "alias": alias,
            "ssid": field(s, "SSID", default=""),
            # La red solo emite si están activos el SSID y su punto de acceso
            "enabled": ssid_on and ap.get("enabled", True),
            "status": field(s, "Status", default=""),
            "radio": _ref_alias(field(s, "LowerLayers", default="")),
            "access_point": ap.get("alias"),
            "internal": alias in INTERNAL_SSIDS,
        })
    radios = [{
        "alias": field(r, "Alias"),
        "enabled": to_bool(field(r, "Enable", default=False)),
        "status": field(r, "Status", default=""),
        "channel": field(r, "Channel"),
        "band": field(r, "OperatingFrequencyBand", default=""),
    } for r in as_list(get_client().get_value("Device/WiFi/Radios")) if isinstance(r, dict)]
    return {"ssids": ssids, "radios": radios}


# ---- acciones ----------------------------------------------------------------

def set_ssid_enabled(alias: str, enabled: bool):
    """Enciende/apaga una red. El interruptor real es AccessPoint/Enable (así
    viene apagada la de invitados); al encender se activa también el SSID."""
    if alias in INTERNAL_SSIDS:
        raise ValueError(f"{alias} es una red interna del mesh y no se toca desde el dashboard")
    ap = access_points().get(alias, {}).get("alias")
    values = {}
    if ap:
        values[f'Device/WiFi/AccessPoints/AccessPoint[Alias="{ap}"]/Enable'] = enabled
    if enabled or not ap:
        values[f'Device/WiFi/SSIDs/SSID[Alias="{alias}"]/Enable'] = enabled
    get_client().set_values(values)


def ping(host: str, count: int = 4) -> dict:
    params = {"Host": host, "Count": count, "Protocol": "AUTO"}
    # El GUI pasa la interfaz WAN como "Device.IP.Interface.<uid>" ($.xmo.getPingInterfaceXpath)
    uid = get_client().get_values({"uid": f"{IP_DATA}/@uid"})["uid"]
    if uid is not None:
        params["Interface"] = f"Device.IP.Interface.{uid}"
    r = get_client().remote_call("Device", "ping", params) or {}
    sent = to_num(field(r, "PacketsTransmitted")) or 0
    recv = to_num(field(r, "PacketsReceived")) or 0
    return {
        "host": host,
        "sent": int(sent),
        "received": int(recv),
        "loss_pct": round(100 * (1 - recv / sent), 1) if sent else 100.0,
        "avg_ms": to_num(field(r, "AverageResponseTime")),
        "raw": r,
    }


def reboot():
    get_client().reboot()
