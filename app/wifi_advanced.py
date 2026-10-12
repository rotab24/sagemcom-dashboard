"""
Bloque 2: Wi-Fi avanzada.

Replica scripts.js:
  - Seguridad: AccessPoint/Security/ModeEnabled (NONE, WPA2_PERSONAL, WPA_WPA2_PERSONAL).
  - WPS: AccessPoint/WPS/Enable y remoteCall startWPS sobre el AccessPoint.
  - Filtro MAC (WiFiMacfilter): AccessPoint/MACFiltering/Mode (DISABLED, WHITELIST,
    BLACKLIST) y la colección MACFiltering/MACAddresses ({"MACAddress": {"MACAddress": mac}}).
    Si el filtro se activa, el GUI apaga el WPS.
  - Aislamiento y límite de clientes: AccessPoint/IsolationEnable, MaxAssociatedDevices.
  - Radio: OperatingChannelBandwidth, TransmitPower.
  - Band steering (una sola red para 2,4 y 5 GHz): Device/WiFi/BandSteering/Enable.
  - Redes vecinas (NeighborAPS.startSearch): SelectedRadio + DiagnosticsState=REQUESTED,
    esperar COMPLETED y leer Result.
  - Horario de Wi-Fi: este firmware NO tiene Device/WiFi/Availability ni Schedulers, así
    que lo aplica el propio dashboard encendiendo/apagando el punto de acceso cada hora.
"""

import re
import time
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import history
from .router import (DAYS, INTERNAL_SSIDS, _ref_alias, as_list, field, get_client, host_name,
                     set_ssid_enabled, to_bool, to_num)
from .router import wifi as wifi_state

SECURITY_MODES = {"NONE": "Abierta (sin contraseña)", "WPA2_PERSONAL": "WPA2 (recomendado)",
                  "WPA_WPA2_PERSONAL": "WPA/WPA2 (aparatos antiguos)"}
MAC_MODES = {"DISABLED", "WHITELIST", "BLACKLIST"}
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", re.I)
NEIGHBOR = "Device/WiFi/NeighboringWiFiDiagnostic"
SCAN_POLL_SECONDS = 2


def _ap_path(ap_alias: str) -> str:
    return f'Device/WiFi/AccessPoints/AccessPoint[Alias="{ap_alias}"]'


def _radio_path(radio: str) -> str:
    return f'Device/WiFi/Radios/Radio[Alias="{radio}"]'


def _aps() -> dict[str, dict]:
    """AccessPoints indexados por el alias de su SSID (sin las redes internas)."""
    out = {}
    for a in as_list(get_client().get_value("Device/WiFi/AccessPoints")):
        if isinstance(a, dict):
            ssid = _ref_alias(field(a, "SSIDReference", default=""))
            if ssid and ssid not in INTERNAL_SSIDS:
                out[ssid] = a
    return out


def _ap_alias(ssid_alias: str) -> str:
    if ssid_alias in INTERNAL_SSIDS:
        raise ValueError(f"{ssid_alias} es una red interna del mesh")
    ap = _aps().get(ssid_alias)
    if ap is None:
        raise LookupError(f"No existe la red {ssid_alias}")
    return field(ap, "Alias")


def overview() -> dict:
    c = get_client()
    names = {}
    for h in as_list(c.get_value("Device/Hosts/Hosts")):
        if isinstance(h, dict):
            mac = (field(h, "PhysAddress", default="") or "").lower()
            names[mac] = host_name(h, mac)
    ssids = {field(s, "Alias"): s for s in as_list(c.get_value("Device/WiFi/SSIDs")) if isinstance(s, dict)}
    schedules = {s["alias"]: s for s in get_schedules()}
    networks = []
    for ssid_alias, a in _aps().items():
        sec = field(a, "Security", default={}) or {}
        wps = field(a, "WPS", default={}) or {}
        mf = field(a, "MACFiltering", default={}) or {}
        s = ssids.get(ssid_alias, {})
        networks.append({
            "alias": ssid_alias,
            "ssid": field(s, "SSID", default=""),
            "radio": _ref_alias(field(s, "LowerLayers", default="")),
            "access_point": field(a, "Alias"),
            "security": field(sec, "ModeEnabled", default=""),
            "wps": {"enabled": to_bool(field(wps, "Enable", default=False)),
                    "status": field(wps, "SessionStatus", default="")},
            "mac_filter": {
                "mode": field(mf, "Mode", default="DISABLED"),
                "list": [{"uid": field(m, "uid"), "mac": (field(m, "MACAddress", default="") or "").lower(),
                          "name": names.get((field(m, "MACAddress", default="") or "").lower(), "")}
                         for m in as_list(field(mf, "MACAddresses", default=[])) if isinstance(m, dict)],
            },
            "isolation": to_bool(field(a, "IsolationEnable", default=False)),
            "max_clients": int(to_num(field(a, "MaxAssociatedDevices")) or 0),
            "schedule": schedules.get(ssid_alias),
        })
    radios = []
    for r in as_list(c.get_value("Device/WiFi/Radios")):
        if not isinstance(r, dict):
            continue
        st = field(r, "Stats", default={}) or {}
        radios.append({
            "alias": field(r, "Alias"),
            "bandwidth": field(r, "OperatingChannelBandwidth", default=""),
            "bandwidths": [b.strip() for b in str(field(r, "SupportedChannelBandwidth", default="")).split(",") if b.strip()],
            "tx_power": int(to_num(field(r, "TransmitPower")) or -1),
            "tx_power_options": [int(p) for p in str(field(r, "TransmitPowerSupported", default="")).split(",")
                                 if p.strip().lstrip("-").isdigit()],
            "standards": field(r, "OperatingStandards", default=""),
            "noise_dbm": to_num(field(st, "Noise")),
            "bytes_sent": to_num(field(st, "BytesSent")),
            "bytes_received": to_num(field(st, "BytesReceived")),
            "errors": int((to_num(field(st, "ErrorsSent")) or 0) + (to_num(field(st, "ErrorsReceived")) or 0)),
        })
    bs = c.get_values({"enabled": "Device/WiFi/BandSteering/Enable"})
    return {"networks": networks, "radios": radios, "band_steering": to_bool(bs["enabled"]),
            "security_modes": SECURITY_MODES}


# ---- escritura ------------------------------------------------------------------------------

def update_ap(ssid_alias: str, security: str | None = None, isolation: bool | None = None,
              max_clients: int | None = None, wps_enabled: bool | None = None):
    ap = _ap_path(_ap_alias(ssid_alias))
    values = {}
    if security is not None:
        if security not in SECURITY_MODES:
            raise ValueError(f"Seguridad no válida: {security}")
        values[f"{ap}/Security/ModeEnabled"] = security
    if isolation is not None:
        values[f"{ap}/IsolationEnable"] = isolation
    if max_clients is not None:
        if not 0 <= max_clients <= 128:
            raise ValueError("El límite de clientes debe estar entre 0 (sin límite) y 128")
        values[f"{ap}/MaxAssociatedDevices"] = int(max_clients)
    if wps_enabled is not None:
        values[f"{ap}/WPS/Enable"] = wps_enabled
    if values:
        get_client().set_values(values)
        history.add_event("wifi", f"{ssid_alias}: " + ", ".join(f"{k.split('/')[-1]}={v}" for k, v in values.items()))


def start_wps(ssid_alias: str):
    ap = _ap_path(_ap_alias(ssid_alias))
    get_client().remote_call(ap, "startWPS", {})
    history.add_event("wifi", f"WPS iniciado en {ssid_alias} (2 minutos para pulsar el botón WPS del aparato)")


def set_mac_filter(ssid_alias: str, mode: str, macs: list[str]):
    if mode not in MAC_MODES:
        raise ValueError(f"Modo no válido: {mode}")
    wanted = []
    for m in macs:
        m = m.strip().lower().replace("-", ":")
        if not MAC_RE.match(m):
            raise ValueError(f"MAC no válida: {m}")
        if m not in wanted:
            wanted.append(m)
    if mode == "WHITELIST" and not wanted:
        raise ValueError("En modo 'Solo permitir' la lista no puede estar vacía: nadie podría conectarse")
    if len(wanted) > 256:
        raise ValueError("Máximo 256 direcciones")
    ap = _ap_path(_ap_alias(ssid_alias))
    c = get_client()
    current = [x for x in as_list(c.get_value(f"{ap}/MACFiltering/MACAddresses")) if isinstance(x, dict)]
    have = {(field(x, "MACAddress", default="") or "").lower(): field(x, "uid") for x in current}
    for mac, uid in have.items():
        if mac not in wanted:
            c.delete_child(f'{ap}/MACFiltering/MACAddresses/MACAddress[@uid="{uid}"]')
    for mac in wanted:
        if mac not in have:
            c.add_child(f"{ap}/MACFiltering/MACAddresses", {"MACAddress": {"MACAddress": mac.upper()}})
    values = {f"{ap}/MACFiltering/Mode": mode}
    if mode != "DISABLED":
        values[f"{ap}/WPS/Enable"] = False  # igual que la web: filtro MAC y WPS son incompatibles
    c.set_values(values)
    history.add_event("wifi", f"Filtro MAC de {ssid_alias}: {mode} ({len(wanted)} direcciones)")


def update_radio(radio: str, bandwidth: str | None = None, tx_power: int | None = None):
    r = next((x for x in overview()["radios"] if x["alias"] == radio), None)
    if r is None:
        raise LookupError(f"No existe la radio {radio}")
    values = {}
    if bandwidth is not None:
        allowed = {b.upper() if b.upper() == "AUTO" else f"{b}MHZ" for b in r["bandwidths"]}
        if bandwidth.upper() not in allowed:
            raise ValueError(f"Ancho no válido: {bandwidth} (posibles: {', '.join(sorted(allowed))})")
        values[f"{_radio_path(radio)}/OperatingChannelBandwidth"] = bandwidth.upper()
    if tx_power is not None:
        if r["tx_power_options"] and tx_power not in r["tx_power_options"]:
            raise ValueError(f"Potencia no válida: {tx_power}")
        values[f"{_radio_path(radio)}/TransmitPower"] = int(tx_power)
    if values:
        get_client().set_values(values)
        history.add_event("wifi", f"Radio {radio}: " + ", ".join(f"{k.split('/')[-1]}={v}" for k, v in values.items()))


def set_band_steering(enabled: bool):
    get_client().set_value("Device/WiFi/BandSteering/Enable", enabled)
    history.add_event("wifi", f"Band steering {'activado' if enabled else 'desactivado'}")


# ---- redes vecinas ------------------------------------------------------------------------

def scan_neighbors(radio: str, timeout: float = 40) -> list[dict]:
    if radio not in ("RADIO2G4", "RADIO5G"):
        raise ValueError("Radio no válida")
    c = get_client()
    # Es un disparador: DiagnosticsState pasa enseguida a COMPLETED, no se puede releer
    c.set_values({f"{NEIGHBOR}/SelectedRadio": f"Device/WiFi/Radios/Radio[{radio}]",
                  f"{NEIGHBOR}/DiagnosticsState": "REQUESTED"}, verify=False)
    deadline = time.monotonic() + timeout
    state = "REQUESTED"
    while time.monotonic() < deadline:
        time.sleep(SCAN_POLL_SECONDS)
        state = str(c.get_value(f"{NEIGHBOR}/DiagnosticsState") or "")
        if state.upper() in ("COMPLETED", "COMPLETE"):
            break
        if state.upper().startswith("ERROR"):
            raise RuntimeError(f"El router no pudo escanear ({state})")
    else:
        raise TimeoutError(f"El escaneo no terminó a tiempo (estado {state})")
    out = []
    for r in as_list(c.get_value(f"{NEIGHBOR}/Result")):
        if not isinstance(r, dict):
            continue
        out.append({
            "ssid": field(r, "SSID", default="") or "(oculta)",
            "bssid": field(r, "BSSID", default=""),
            "channel": int(to_num(field(r, "Channel")) or 0),
            "signal_dbm": to_num(field(r, "SignalStrength")),
            "bandwidth": field(r, "OperatingChannelBandwidth", default=""),
            "security": field(r, "SecurityModeEnabled", "EncryptionMode", default=""),
            "band": field(r, "OperatingFrequencyBand", default=""),
        })
    out.sort(key=lambda n: -(n["signal_dbm"] or -120))
    return out


def channel_advice(networks: list[dict], radio: str) -> dict:
    """Puntúa cada canal según cuántas redes (y cómo de fuertes) lo usan."""
    if radio == "RADIO2G4":
        candidates = [1, 6, 11]
        overlap = lambda a, b: abs(a - b) < 5  # noqa: E731 - en 2,4 GHz los canales se solapan
    else:
        candidates = sorted({n["channel"] for n in networks} | {36, 40, 44, 48, 100, 104, 108, 112})
        overlap = lambda a, b: a == b  # noqa: E731
    scores = {}
    for ch in candidates:
        scores[ch] = round(sum(max(0.0, (n["signal_dbm"] or -100) + 100) for n in networks
                               if n["channel"] and overlap(ch, n["channel"])), 1)
    best = min(scores, key=scores.get) if scores else None
    return {"scores": [{"channel": ch, "score": sc} for ch, sc in scores.items()], "best": best}


# ---- horario de Wi-Fi (lo aplica el dashboard) ----------------------------------------------------------

def get_schedules() -> list[dict]:
    return [{"alias": a, "enabled": e, "grid": g} for a, e, g in history.wifi_schedules()]


def set_schedule(ssid_alias: str, enabled: bool, grid: dict[str, list[bool]]):
    _ap_alias(ssid_alias)  # valida que existe y no es interna
    for d in DAYS:
        if d not in grid or len(grid[d]) != 24:
            raise ValueError(f"Horario incompleto: falta {d} o no tiene 24 horas")
    history.save_wifi_schedule(ssid_alias, enabled, grid)
    hours = sum(sum(v) for v in grid.values())
    history.add_event("wifi", f"Horario de {ssid_alias} {'activado' if enabled else 'desactivado'} ({hours} h/semana apagada)")
    apply_schedules()


def apply_schedules(now: datetime | None = None):
    """Enciende/apaga cada red según su horario (True = apagada esa hora)."""
    now = now or datetime.now()
    day, hour = DAYS[now.weekday()], now.hour
    state = {s["alias"]: s["enabled"] for s in wifi_state()["ssids"]}
    for s in get_schedules():
        if not s["enabled"] or s["alias"] not in state:
            continue
        want_on = not s["grid"][day][hour]
        if state[s["alias"]] != want_on:
            set_ssid_enabled(s["alias"], want_on)
            history.add_event("wifi", f"Horario: Wi-Fi {s['alias']} {'encendida' if want_on else 'apagada'}")


# ---- API -------------------------------------------------------------------------------------

api = APIRouter(prefix="/api/wifi/advanced", tags=["wifi-advanced"])


@api.get("")
def api_overview():
    return overview()


class ApBody(BaseModel):
    security: str | None = None
    isolation: bool | None = None
    max_clients: int | None = None
    wps_enabled: bool | None = None


@api.put("/ap/{ssid_alias}")
def api_update_ap(ssid_alias: str, body: ApBody):
    update_ap(ssid_alias, **body.model_dump())
    return {"ok": True}


@api.post("/ap/{ssid_alias}/wps")
def api_wps(ssid_alias: str):
    start_wps(ssid_alias)
    return {"ok": True}


class MacFilterBody(BaseModel):
    mode: str
    macs: list[str] = []


@api.put("/ap/{ssid_alias}/macfilter")
def api_macfilter(ssid_alias: str, body: MacFilterBody):
    set_mac_filter(ssid_alias, body.mode, body.macs)
    return {"ok": True}


class RadioBody(BaseModel):
    bandwidth: str | None = None
    tx_power: int | None = None


@api.put("/radio/{radio}")
def api_radio(radio: str, body: RadioBody):
    update_radio(radio, body.bandwidth, body.tx_power)
    return {"ok": True}


class BandSteeringBody(BaseModel):
    enabled: bool


@api.put("/bandsteering")
def api_band_steering(body: BandSteeringBody):
    set_band_steering(body.enabled)
    return {"ok": True}


@api.post("/scan/{radio}")
def api_scan(radio: str):
    nets = scan_neighbors(radio)
    return {"networks": nets, "advice": channel_advice(nets, radio)}


class WifiScheduleBody(BaseModel):
    enabled: bool
    grid: dict[str, list[bool]] = Field(default_factory=dict)


@api.put("/schedule/{ssid_alias}")
def api_schedule(ssid_alias: str, body: WifiScheduleBody):
    set_schedule(ssid_alias, body.enabled, body.grid)
    return {"ok": True}
