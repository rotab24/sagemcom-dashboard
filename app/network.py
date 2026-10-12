"""
Bloque 1: red local y seguridad.

Replica lo que hacen las factories de scripts.js:
  - PortForwarding: addChild Device/NAT/PortMappings {"PortMapping": {...}},
    alias PORTFW_RULE_<n>, ExternalInterface IP_DATA; borrar = deleteChild por uid.
  - Dmz: la regla PortMapping[Service="DMZ"] (Enable + InternalClient).
  - Dhcp.saveIpReservation: StaticAddresses del DEFAULT_POOL {"StaticAddress":
    {Enable, Yiaddr, Chaddr (MAC en mayúsculas)}}; borrar por Alias.
  - Dhcp / DnsServer: MinAddress, MaxAddress, LeaseTime y DNSServers del DEFAULT_POOL.
  - Firewall: Device/Firewall/Config (LOW/MEDIUM/HIGH), RespondToPing,
    PortScanDetection, blockFragmentedIPPackets.
  - UPnP: Device/UPnP/Device/UPnPIGD.
"""

import ipaddress
import re

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import history
from . import router
from .config import settings
from .router import as_list, field, get_client, to_bool, to_num

POOL = 'Device/DHCPv4/Server/Pools/Pool[Alias="DEFAULT_POOL"]'
LAN_ADDR = 'Device/IP/Interfaces/Interface[Alias="IP_BR_LAN"]/IPv4Addresses/IPv4Address[Alias="IP_BR_LAN_ADDRESS"]'
PORTMAPS = "Device/NAT/PortMappings"
DMZ = 'Device/NAT/PortMappings/PortMapping[Service="DMZ"]'
PF_PREFIX = "PORTFW_RULE_"
PROTOCOLS = {"TCP", "UDP", "BOTH"}
FIREWALL_LEVELS = {"LOW", "MEDIUM", "HIGH"}
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", re.I)


# ---- lectura -----------------------------------------------------------------------

def _lan() -> dict:
    v = get_client().get_values({"ip": f"{LAN_ADDR}/IPAddress", "mask": f"{LAN_ADDR}/SubnetMask"})
    net = ipaddress.ip_network(f"{v['ip']}/{v['mask']}", strict=False) if v["ip"] and v["mask"] else None
    return {**v, "network": str(net) if net else None}


def _port_rule(r: dict) -> dict:
    return {
        "uid": field(r, "uid"),
        "alias": field(r, "Alias", default=""),
        "enabled": to_bool(field(r, "Enable", default=False)),
        "description": field(r, "Description", default=""),
        "protocol": field(r, "Protocol", default=""),
        "external_port": int(to_num(field(r, "ExternalPort")) or 0),
        "external_port_end": int(to_num(field(r, "ExternalPortEndRange")) or 0),
        "internal_client": field(r, "InternalClient", default=""),
        "internal_port": int(to_num(field(r, "InternalPort")) or 0),
        "remote_host": field(r, "RemoteHost", default=""),
        "creator": field(r, "Creator", default=""),
        "status": field(r, "Status", default=""),
    }


def overview() -> dict:
    c = get_client()
    pool = c.get_value(POOL) or {}
    rules = [r for r in as_list(c.get_value(PORTMAPS)) if isinstance(r, dict)]
    fw = c.get_values({
        "level": "Device/Firewall/Config",
        "respond_to_ping": "Device/Firewall/RespondToPing",
        "port_scan_detection": "Device/Firewall/PortScanDetection",
        "block_fragments": "Device/Firewall/blockFragmentedIPPackets",
    })
    upnp = c.get_values({"igd": "Device/UPnP/Device/UPnPIGD"})
    wan_dns = c.get_values({"dns": 'Device/IP/Interfaces/Interface[Alias="IP_DATA"]/IPv4Addresses/IPv4Address[Alias="IP_DATA_ADDRESS"]/Dns'})
    dmz = next((r for r in rules if field(r, "Service") == "DMZ"), None)
    return {
        "lan": _lan(),
        "dhcp": {
            "enabled": to_bool(field(pool, "Enable", default=True)),
            "start": field(pool, "MinAddress", default=""),
            "end": field(pool, "MaxAddress", default=""),
            "lease_time": int(to_num(field(pool, "LeaseTime")) or 0),
            "dns_servers": [s for s in str(field(pool, "DNSServers", default="")).split(",") if s],
            "wan_dns": [s for s in str(wan_dns["dns"] or "").split(",") if s],
            "domain": field(pool, "DomainName", default=""),
            "clients": len(as_list(field(pool, "Clients", default=[]))),
        },
        "reservations": [{
            "alias": field(s, "Alias", default=""),
            "enabled": to_bool(field(s, "Enable", default=True)),
            "ip": field(s, "Yiaddr", default=""),
            "mac": (field(s, "Chaddr", default="") or "").lower(),
        } for s in as_list(field(pool, "StaticAddresses", default=[])) if isinstance(s, dict)],
        "port_forwards": [_port_rule(r) for r in rules
                          if field(r, "Service") != "DMZ" and field(r, "Creator") not in ("UPNP", "HIDDEN")],
        "upnp_rules": [_port_rule(r) for r in rules if field(r, "Creator") == "UPNP"],
        "dmz": {
            "exists": dmz is not None,
            "enabled": to_bool(field(dmz, "Enable", default=False)) if dmz else False,
            "ip": field(dmz, "InternalClient", default="") if dmz else "",
        },
        "firewall": {
            "level": (fw["level"] or "").upper(),
            "respond_to_ping": to_bool(fw["respond_to_ping"]),
            "port_scan_detection": to_bool(fw["port_scan_detection"]),
            "block_fragments": to_bool(fw["block_fragments"]),
        },
        "upnp": {"enabled": to_bool(upnp["igd"])},
    }


# ---- validación ---------------------------------------------------------------------

def _lan_ip(ip: str, what: str) -> str:
    lan = _lan()
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        raise ValueError(f"{what}: '{ip}' no es una IP válida")
    if lan["network"] and addr not in ipaddress.ip_network(lan["network"]):
        raise ValueError(f"{what}: {ip} no está en tu red local ({lan['network']})")
    if str(addr) == lan["ip"]:
        raise ValueError(f"{what}: {ip} es la IP del propio router")
    return str(addr)


def _port(p: int, what: str) -> int:
    if not 1 <= int(p) <= 65535:
        raise ValueError(f"{what} debe estar entre 1 y 65535")
    return int(p)


# ---- abrir puertos -----------------------------------------------------------------------

class PortForward(BaseModel):
    description: str = Field("", max_length=64)
    protocol: str = "TCP"
    external_port: int
    external_port_end: int | None = None
    internal_client: str
    internal_port: int | None = None
    enabled: bool = True


def _check_pf(pf: PortForward, ignore_uid=None) -> dict:
    if pf.protocol not in PROTOCOLS:
        raise ValueError(f"Protocolo no válido: {pf.protocol} (usa TCP, UDP o BOTH)")
    ext = _port(pf.external_port, "El puerto externo")
    end = _port(pf.external_port_end or ext, "El final del rango")
    if end < ext:
        raise ValueError("El final del rango no puede ser menor que el puerto externo")
    internal = _port(pf.internal_port or ext, "El puerto interno")
    ip = _lan_ip(pf.internal_client, "Dispositivo destino")
    # Dos reglas activas no pueden usar el mismo puerto externo con el mismo protocolo
    ov = overview()
    for r in ov["port_forwards"] + ov["upnp_rules"]:
        if r["uid"] == ignore_uid or not r["enabled"]:
            continue
        r_end = r["external_port_end"] or r["external_port"]
        same_proto = r["protocol"] == pf.protocol or "BOTH" in (r["protocol"], pf.protocol)
        if same_proto and ext <= r_end and r["external_port"] <= end:
            raise ValueError(f"El puerto {ext} ya está abierto por la regla '{r['description'] or r['alias']}'")
    return {
        "Enable": pf.enabled, "Service": "", "Protocol": pf.protocol, "Description": pf.description.strip(),
        "RemoteHost": "", "InternalClient": ip, "ExternalPortEndRange": end if end != ext else 0,
        "InternalPort": internal, "ExternalPort": ext,
        "ExternalInterface": "Device/IP/Interfaces/Interface[IP_DATA]",
    }


def add_port_forward(pf: PortForward):
    data = _check_pf(pf)
    existing = [r["alias"] for r in overview()["port_forwards"]]
    nums = [int(a[len(PF_PREFIX):]) for a in existing if a.startswith(PF_PREFIX) and a[len(PF_PREFIX):].isdigit()]
    data["Alias"] = f"{PF_PREFIX}{max(nums, default=0) + 1}"
    get_client().add_child(PORTMAPS, {"PortMapping": data})
    history.add_event("network", f"Puerto {pf.external_port}/{pf.protocol} abierto hacia {data['InternalClient']}:{data['InternalPort']}")


def update_port_forward(uid: int, pf: PortForward):
    if uid not in {r["uid"] for r in overview()["port_forwards"]}:
        raise LookupError(f"No existe la regla {uid}")
    data = _check_pf(pf, ignore_uid=uid)
    base = f'{PORTMAPS}/PortMapping[@uid="{int(uid)}"]'
    get_client().set_values({f"{base}/{k}": v for k, v in data.items()})
    history.add_event("network", f"Regla de puertos {uid} modificada ({pf.external_port}/{pf.protocol} → {data['InternalClient']})")


def delete_port_forward(uid: int):
    rule = next((r for r in overview()["port_forwards"] if r["uid"] == uid), None)
    if rule is None:
        raise LookupError(f"No existe la regla {uid}")
    get_client().delete_child(f'{PORTMAPS}/PortMapping[@uid="{int(uid)}"]')
    history.add_event("network", f"Puerto {rule['external_port']}/{rule['protocol']} cerrado ({rule['description']})")


# ---- DMZ ----------------------------------------------------------------------------------

def set_dmz(enabled: bool, ip: str = ""):
    c = get_client()
    exists = overview()["dmz"]["exists"]
    if enabled:
        ip = _lan_ip(ip, "Dispositivo DMZ")
    if exists:
        values = {f"{DMZ}/Enable": enabled}
        if enabled:
            values[f"{DMZ}/InternalClient"] = ip
        c.set_values(values)
    elif enabled:
        c.add_child(PORTMAPS, {"PortMapping": {"Enable": True, "Alias": "DMZ", "Service": "DMZ",
                                               "InternalClient": ip, "Protocol": "ALL"}})
    history.add_event("network", f"DMZ {'activada hacia ' + ip if enabled else 'desactivada'}")


# ---- IPs fijas (reservas DHCP) ------------------------------------------------------------------

def add_reservation(mac: str, ip: str, enabled: bool = True):
    mac = mac.strip().lower().replace("-", ":")
    if not MAC_RE.match(mac):
        raise ValueError(f"MAC no válida: {mac}")
    ip = _lan_ip(ip, "IP reservada")
    for r in overview()["reservations"]:
        if r["mac"] == mac:
            raise ValueError(f"Ese dispositivo ya tiene la IP {r['ip']} reservada")
        if r["ip"] == ip:
            raise ValueError(f"La IP {ip} ya está reservada para {r['mac']}")
    get_client().add_child(f"{POOL}/StaticAddresses",
                           {"StaticAddress": {"Enable": enabled, "Yiaddr": ip, "Chaddr": mac.upper()}})
    history.add_event("network", f"IP fija {ip} reservada para {mac} (se aplica cuando el dispositivo renueve su IP)")


def _read_reservations() -> list[dict]:
    return [{
        "alias": field(s, "Alias", default=""),
        "enabled": to_bool(field(s, "Enable", default=True)),
        "ip": field(s, "Yiaddr", default=""),
        "mac": (field(s, "Chaddr", default="") or "").lower(),
    } for s in as_list(get_client().get_value(f"{POOL}/StaticAddresses")) if isinstance(s, dict)]


def reservations() -> list[dict]:
    """Solo las reservas (mucho más ligero que overview()); se reutiliza como la lista de dispositivos."""
    return router.cached("reservations", settings.cache_ttl, _read_reservations)


def set_fixed_ip(mac: str, ip: str | None) -> str:
    """Deja fija (reserva DHCP) la IP de un dispositivo, se la cambia o, con ip=None,
    se la quita. Como Dhcp.saveIpReservation: si ya tenía reserva se actualiza
    Enable/Yiaddr/Chaddr; si no, addChild. Igual que $.util.checkIpInUse, no deja
    usar una IP que tiene ahora otro aparato."""
    mac = mac.strip().lower().replace("-", ":")
    if not MAC_RE.match(mac):
        raise ValueError(f"MAC no válida: {mac}")
    res = _read_reservations()
    current = next((r for r in res if r["mac"] == mac), None)
    if not ip:
        if current:
            get_client().delete_child(f'{POOL}/StaticAddresses/StaticAddress[Alias="{current["alias"]}"]')
            history.add_event("network", f"IP fija {current['ip']} quitada a {mac}")
            return f"IP fija {current['ip']} quitada"
        return "No tenía IP fija"
    ip = _lan_ip(ip, "IP fija")
    other = next((r for r in res if r["ip"] == ip and r["mac"] != mac), None)
    if other:
        raise ValueError(f"La IP {ip} ya está reservada para otro dispositivo ({other['mac']})")
    user = next((h for h in router.hosts() if h["ip"] == ip and h["mac"] != mac and h["active"]), None)
    if user:
        raise ValueError(f"La IP {ip} la está usando ahora {user['name']}")
    if current and current["ip"] == ip and current["enabled"]:
        return f"La IP {ip} ya era fija"
    if current:
        xp = f'{POOL}/StaticAddresses/StaticAddress[Alias="{current["alias"]}"]'
        get_client().set_values({f"{xp}/Enable": True, f"{xp}/Yiaddr": ip, f"{xp}/Chaddr": mac.upper()})
    else:
        get_client().add_child(f"{POOL}/StaticAddresses",
                               {"StaticAddress": {"Enable": True, "Yiaddr": ip, "Chaddr": mac.upper()}})
    history.add_event("network", f"IP fija {ip} para {mac} (se aplica cuando el dispositivo renueve su IP)")
    return f"IP {ip} fija"


def delete_reservation(alias: str):
    r = next((r for r in overview()["reservations"] if r["alias"] == alias), None)
    if r is None:
        raise LookupError(f"No existe la reserva {alias}")
    get_client().delete_child(f'{POOL}/StaticAddresses/StaticAddress[Alias="{alias}"]')
    history.add_event("network", f"Reserva de IP {r['ip']} ({r['mac']}) eliminada")


# ---- DHCP y DNS ---------------------------------------------------------------------------------

def set_dhcp(start: str, end: str, lease_time: int):
    start, end = _lan_ip(start, "Inicio del rango"), _lan_ip(end, "Fin del rango")
    if ipaddress.ip_address(start) > ipaddress.ip_address(end):
        raise ValueError("El inicio del rango debe ser menor que el final")
    if not 120 <= lease_time <= 30 * 86400:
        raise ValueError("La duración debe estar entre 2 minutos y 30 días")
    get_client().set_values({f"{POOL}/MinAddress": start, f"{POOL}/MaxAddress": end, f"{POOL}/LeaseTime": int(lease_time)})
    history.add_event("network", f"DHCP: rango {start}–{end}, concesión {lease_time // 3600} h")


def set_dns(servers: list[str]):
    clean = []
    for s in servers:
        s = s.strip()
        if not s:
            continue
        try:
            clean.append(str(ipaddress.ip_address(s)))
        except ValueError:
            raise ValueError(f"'{s}' no es una IP de DNS válida")
    if not 1 <= len(clean) <= 2:
        raise ValueError("Indica uno o dos servidores DNS")
    before = overview()["dhcp"]["dns_servers"]
    get_client().set_value(f"{POOL}/DNSServers", ",".join(clean))
    history.add_event("network", f"DNS de la red: {', '.join(before) or '—'} → {', '.join(clean)}")


# ---- firewall y UPnP --------------------------------------------------------------------------------

def set_firewall(level: str | None = None, respond_to_ping: bool | None = None,
                 port_scan_detection: bool | None = None, block_fragments: bool | None = None):
    values = {}
    if level is not None:
        if level.upper() not in FIREWALL_LEVELS:
            raise ValueError("Nivel no válido (LOW, MEDIUM o HIGH)")
        values["Device/Firewall/Config"] = level.upper()
    if respond_to_ping is not None:
        values["Device/Firewall/RespondToPing"] = respond_to_ping
    if port_scan_detection is not None:
        values["Device/Firewall/PortScanDetection"] = port_scan_detection
    if block_fragments is not None:
        values["Device/Firewall/blockFragmentedIPPackets"] = block_fragments
    if values:
        get_client().set_values(values)
        history.add_event("network", "Firewall: " + ", ".join(f"{k.split('/')[-1]}={v}" for k, v in values.items()))


def set_upnp(enabled: bool):
    get_client().set_value("Device/UPnP/Device/UPnPIGD", enabled)
    history.add_event("network", f"UPnP {'activado' if enabled else 'desactivado'}")


# ---- API ----------------------------------------------------------------------------------------------

api = APIRouter(prefix="/api/network", tags=["network"])


@api.get("")
def api_overview():
    return overview()


@api.post("/portforwards")
def api_add_pf(body: PortForward):
    add_port_forward(body)
    return {"ok": True}


@api.put("/portforwards/{uid}")
def api_update_pf(uid: int, body: PortForward):
    update_port_forward(uid, body)
    return {"ok": True}


@api.delete("/portforwards/{uid}")
def api_delete_pf(uid: int):
    delete_port_forward(uid)
    return {"ok": True}


class DmzBody(BaseModel):
    enabled: bool
    ip: str = ""


@api.put("/dmz")
def api_dmz(body: DmzBody):
    set_dmz(body.enabled, body.ip)
    return {"ok": True}


class ReservationBody(BaseModel):
    mac: str
    ip: str


@api.post("/reservations")
def api_add_reservation(body: ReservationBody):
    add_reservation(body.mac, body.ip)
    return {"ok": True}


@api.delete("/reservations/{alias}")
def api_delete_reservation(alias: str):
    delete_reservation(alias)
    return {"ok": True}


class DhcpBody(BaseModel):
    start: str
    end: str
    lease_time: int


@api.put("/dhcp")
def api_dhcp(body: DhcpBody):
    set_dhcp(body.start, body.end, body.lease_time)
    return {"ok": True}


class DnsBody(BaseModel):
    servers: list[str]


@api.put("/dns")
def api_dns(body: DnsBody):
    set_dns(body.servers)
    return {"ok": True}


class FirewallBody(BaseModel):
    level: str | None = None
    respond_to_ping: bool | None = None
    port_scan_detection: bool | None = None
    block_fragments: bool | None = None


@api.put("/firewall")
def api_firewall(body: FirewallBody):
    set_firewall(**body.model_dump())
    return {"ok": True}


class UpnpBody(BaseModel):
    enabled: bool


@api.put("/upnp")
def api_upnp(body: UpnpBody):
    set_upnp(body.enabled)
    return {"ok": True}
