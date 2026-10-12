"""
Bloque 3: diagnóstico y mantenimiento.

Replica scripts.js / xmo.js / gui-api.js:
  - traceroute / nslookup: remoteCall sobre "Device" ($.xmo.traceroute / $.xmo.nslookup).
  - Test de velocidad: remoteCall speedTestClient en Device/IP/Diagnostics/SpeedTest y
    esperar a DiagnosticsStatus = COMPLETE (SpeedtestIPDiagnostics).
  - Registro del sistema: getVendorLogDownloadURI en VendorLogFile[@uid='1'] y descargar (SystemLog.getUriLog).
  - Copia de seguridad: getDownloadURI {FileName: "device.cfg", Type: 1} sobre
    VendorConfigFile[Alias="DEVICE_CONFIG"] ($.xmo.saveConfiguration con backupConfigurationAllBackup).
  - Hora: Device/Time (NTPServer1..5). DNS dinámico: Device/Services/DynamicDNS.
  - Acceso remoto: Device/Services/RemoteAccess. Syslog: Device/DeviceInfo/Logging/Syslog/Server.
  - Contraseña: remoteCall changeClearPassword en Device/UserAccounts/Users/User[Login='…'].
  - Restablecer de fábrica: remoteCall reinitialize sobre "Device" ($.xmo.reinitialize).
Restaurar copias y actualizar firmware se suben como multipart a /cgi/upload; no se
implementan porque no se pueden probar sin riesgo (se hacen desde 192.168.1.1).
"""

import ipaddress
import re
import time
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field

from sagemcom.client import md5

from . import history
from .config import ENV_FILE, settings
from .router import IP_DATA, as_list, field, get_client, to_bool, to_num

HOST_RE = re.compile(r"^[A-Za-z0-9.:\-]{1,253}$")
SPEEDTEST = "Device/IP/Diagnostics/SpeedTest"
DYNDNS = "Device/Services/DynamicDNS"
RESET_PHRASE = "RESTABLECER"
POLL_SECONDS = 2


def _host(host: str) -> str:
    host = host.strip()
    if not HOST_RE.match(host):
        raise ValueError("Dirección no válida")
    return host


def _wan_interface() -> str | None:
    uid = get_client().get_values({"uid": f"{IP_DATA}/@uid"})["uid"]
    return f"Device.IP.Interface.{uid}" if uid is not None else None


def _wait(xpath: str, done: tuple[str, ...], timeout: float, error_prefix: str = "ERROR") -> str:
    deadline = time.monotonic() + timeout
    state = ""
    while time.monotonic() < deadline:
        state = str(get_client().get_value(xpath) or "")
        if state.upper() in done:
            return state
        if state.upper().startswith(error_prefix):
            raise RuntimeError(f"El router devolvió {state}")
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"No terminó a tiempo (estado {state or 'desconocido'})")


# ---- diagnóstico ---------------------------------------------------------------------------

def traceroute(host: str) -> dict:
    host = _host(host)
    params = {"Host": host, "Protocol": "AUTO", "Count": 3}
    iface = _wan_interface()
    if iface:
        params["Interface"] = iface
    r = get_client().remote_call("Device", "traceRoute", params) or {}
    hops = as_list(field(r, "RouteHops", default=None))
    if not hops:
        # Algunos firmwares responden enseguida y dejan el resultado en el nodo TraceRoute
        _wait("Device/IP/Diagnostics/TraceRoute/DiagnosticsState", ("COMPLETE", "COMPLETED"), 90)
        node = get_client().get_value("Device/IP/Diagnostics/TraceRoute") or {}
        hops = as_list(field(node, "RouteHops", default=[]))
    return {"host": host, "hops": [{
        "n": i + 1,
        "host": field(h, "Host", default="") or field(h, "HostAddress", default="") or "*",
        "ip": field(h, "HostAddress", default=""),
        "ms": field(h, "RTTimes", default=""),
    } for i, h in enumerate(hops) if isinstance(h, dict)], "raw": r}


def nslookup(host: str) -> dict:
    host = _host(host)
    r = get_client().remote_call("Device", "nslookup", {"Host": host}) or {}
    return {"host": host, "result": r}


def speedtest(timeout: float = 120) -> dict:
    c = get_client()
    c.remote_call(SPEEDTEST, "speedTestClient", {})
    _wait(f"{SPEEDTEST}/DiagnosticsStatus", ("COMPLETE", "COMPLETED"), timeout)
    r = speedtest_results()
    history.add_event("diag", f"Test de velocidad: ↓ {r['last']['download']} ↑ {r['last']['upload']} Mbit/s, {r['last']['latency']} ms")
    return r


def speedtest_results() -> dict:
    node = get_client().get_value(SPEEDTEST) or {}
    hist = field(node, "History", default={}) or {}

    def split(k):
        return [x for x in str(field(hist, k, default="") or "").split(",")]

    rows = []
    for i, ts in enumerate(split("Timestamp")):
        if not ts:
            continue
        get = lambda k: (split(k)[i] if i < len(split(k)) else "")  # noqa: E731
        rows.append({"time": datetime.fromtimestamp(int(float(ts))).isoformat() if ts.replace(".", "").isdigit() else ts,
                     "download": to_num(get("Download")), "upload": to_num(get("Upload")),
                     "latency": to_num(get("Latency")), "server": get("SelectedServerAddress")})
    return {
        "status": field(node, "DiagnosticsStatus", default=""),
        "last": {"download": to_num(field(node, "Download")), "upload": to_num(field(node, "Upload")),
                 "latency": to_num(field(node, "Latency")), "server": field(node, "SelectedServerAddress", default="")},
        "history": rows[::-1],
    }


def system_log(lines: int = 300) -> list[str]:
    c = get_client()
    r = c.remote_call("Device/DeviceInfo/VendorLogFiles/VendorLogFile[@uid='1']", "getVendorLogDownloadURI",
                      {"FileName": "logFile"}) or {}
    uri = field(r, "uri", default="")
    if not uri:
        raise RuntimeError("El router no devolvió el enlace del registro")
    text = c.download(uri).decode("utf-8", errors="replace")
    return [ln for ln in text.splitlines() if ln.strip()][-lines:][::-1]


def backup() -> tuple[bytes, str]:
    c = get_client()
    r = c.remote_call('Device/DeviceInfo/VendorConfigFiles/VendorConfigFile[Alias="DEVICE_CONFIG"]',
                      "getDownloadURI", {"FileName": "device.cfg", "Type": 1}) or {}
    uri = field(r, "uri", default="")
    if not uri:
        raise RuntimeError("El router no devolvió el enlace de la copia de seguridad")
    data = c.download(uri)
    name = f"router-{datetime.now():%Y%m%d-%H%M}.cfg"
    history.add_event("maintenance", f"Copia de seguridad descargada ({len(data) // 1024} KB)")
    return data, name


# ---- ajustes -------------------------------------------------------------------------------

def overview() -> dict:
    c = get_client()
    t = c.get_value("Device/Time") or {}
    dd = c.get_value(DYNDNS) or {}
    ra = c.get_value("Device/Services/RemoteAccess") or {}
    info = c.get_values({
        "firmware": "Device/DeviceInfo/ExternalFirmwareVersion",
        "internal_firmware": "Device/DeviceInfo/InternalFirmwareVersion",
        "build_date": "Device/DeviceInfo/BuildDate",
        "reboot_count": "Device/DeviceInfo/RebootCount",
        "first_use": "Device/DeviceInfo/FirstUseDate",
        "serial": "Device/DeviceInfo/SerialNumber",
        "mac": "Device/DeviceInfo/MACAddress",
    })
    syslog = c.get_values({
        "enabled": "Device/DeviceInfo/Logging/Syslog/Server/Enable",
        "ip": "Device/DeviceInfo/Logging/Syslog/Server/IPv4Address",
        "port": "Device/DeviceInfo/Logging/Syslog/Server/Port",
    })
    services = [field(s, "Name") for s in as_list(field(dd, "Services", default=[])) if isinstance(s, dict)]
    client = next((x for x in as_list(field(dd, "Clients", default=[])) if isinstance(x, dict)), {})
    hostname = next((h for h in as_list(field(client, "Hostnames", default=[])) if isinstance(h, dict)), {})
    service_ref = field(client, "ServiceReference", default="")
    m = re.search(r"Name='([^']+)'", service_ref)
    return {
        "info": info,
        "time": {
            "enabled": to_bool(field(t, "Enable", default=True)),
            "status": field(t, "Status", default=""),
            "local_time": field(t, "CurrentLocalTime", default=""),
            "timezone": field(t, "LocalTimeZoneName", default=""),
            "servers": [field(t, f"NTPServer{i}", default="") for i in range(1, 6) if field(t, f"NTPServer{i}", default="")],
        },
        "dyndns": {
            "exists": bool(client),
            "enabled": to_bool(field(client, "Enable", default=False)),
            "service": m.group(1) if m else "",
            "services": services,
            "username": field(client, "Username", default=""),
            "hostname": field(hostname, "Name", default=""),
            "status": field(client, "Status", default=""),
            "last_error": field(client, "LastError", default=""),
            "last_ip": field(hostname, "LastIP", default=""),
            "last_update": field(hostname, "LastUpdate", default=""),
        },
        "remote_access": {
            "http": to_bool(field(ra, "HTTPEnable", default=False)),
            "https": to_bool(field(ra, "HTTPSEnable", default=False)),
            "ssh": to_bool(field(ra, "SSHEnable", default=False)),
            "telnet": to_bool(field(ra, "TELNETEnable", default=False)),
            "trusted": [f"{field(d, 'IPAddress')}/{field(d, 'PrefixLength')}"
                        for d in as_list(field(ra, "TrustedDomains", default=[])) if isinstance(d, dict) and to_bool(field(d, "Enable", default=True))],
        },
        "syslog": {"enabled": to_bool(syslog["enabled"]), "ip": syslog["ip"] or "", "port": int(to_num(syslog["port"]) or 514)},
        "speedtest": speedtest_results(),
        "user": settings.user,
    }


def set_ntp(servers: list[str]):
    clean = [s.strip() for s in servers if s.strip()]
    if not 1 <= len(clean) <= 5:
        raise ValueError("Indica entre 1 y 5 servidores de hora")
    for s in clean:
        if not HOST_RE.match(s):
            raise ValueError(f"Servidor no válido: {s}")
    values = {f"Device/Time/NTPServer{i}": (clean[i - 1] if i <= len(clean) else "") for i in range(1, 6)}
    get_client().set_values(values)
    history.add_event("maintenance", f"Servidores de hora: {', '.join(clean)}")


def set_dyndns(enabled: bool, service: str = "", username: str = "", password: str | None = None, hostname: str = ""):
    ov = overview()["dyndns"]
    if not ov["exists"]:
        raise RuntimeError("Este router no tiene cliente de DNS dinámico")
    base = f'{DYNDNS}/Clients/Client[@uid="1"]'
    values = {f"{base}/Enable": enabled}
    if enabled:
        if service not in ov["services"]:
            raise ValueError(f"Servicio no válido (posibles: {', '.join(ov['services'])})")
        if not hostname.strip() or not username.strip():
            raise ValueError("Faltan el dominio o el usuario")
        values[f"{base}/ServiceReference"] = f"Device/Services/DynamicDNS/Services/Service[Name='{service}']"
        values[f"{base}/Username"] = username.strip()
        values[f'{base}/Hostnames/Hostname[@uid="1"]/Name'] = hostname.strip()
        if password:
            values[f"{base}/Password"] = password
    get_client().set_values(values)
    history.add_event("maintenance", f"DNS dinámico {'activado: ' + hostname + ' (' + service + ')' if enabled else 'desactivado'}")


def set_remote_access(http: bool | None = None, https: bool | None = None, ssh: bool | None = None, telnet: bool | None = None):
    base = "Device/Services/RemoteAccess"
    values = {f"{base}/{k}": v for k, v in (("HTTPEnable", http), ("HTTPSEnable", https), ("SSHEnable", ssh), ("TELNETEnable", telnet)) if v is not None}
    if values:
        get_client().set_values(values)
        history.add_event("maintenance", "Acceso remoto: " + ", ".join(f"{k.split('/')[-1]}={v}" for k, v in values.items()))


def set_syslog(enabled: bool, ip: str = "", port: int = 514):
    base = "Device/DeviceInfo/Logging/Syslog/Server"
    values = {f"{base}/Enable": enabled}
    if enabled:
        try:
            ipaddress.ip_address(ip.strip())
        except ValueError:
            raise ValueError("IP del servidor syslog no válida")
        if not 1 <= port <= 65535:
            raise ValueError("Puerto no válido")
        values[f"{base}/IPv4Address"] = ip.strip()
        values[f"{base}/Port"] = int(port)
    get_client().set_values(values)
    history.add_event("maintenance", f"Syslog remoto {'→ ' + ip + ':' + str(port) if enabled else 'desactivado'}")


def _update_env_password(new: str):
    env = ENV_FILE
    if settings.mock or not env.exists():
        return
    lines = env.read_text(encoding="utf-8").splitlines()
    out, done = [], False
    for line in lines:
        if line.startswith("SAGEMCOM_PASS="):
            out.append(f"SAGEMCOM_PASS={new}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"SAGEMCOM_PASS={new}")
    env.write_text("\n".join(out) + "\n", encoding="utf-8")


def change_password(old: str, new: str):
    c = get_client()
    if hasattr(c, "password_hash") and md5(old) != c.password_hash:
        raise ValueError("La contraseña actual no es correcta")
    if len(new) < 8 or not all(32 <= ord(ch) < 127 for ch in new) or new != new.strip():
        raise ValueError("La nueva contraseña debe tener al menos 8 caracteres, sin tildes, ñ ni espacios al principio o al final")
    c.remote_call(f"Device/UserAccounts/Users/User[Login='{settings.user}']", "changeClearPassword",
                  {"OldPassword": old, "NewPassword": new})
    if hasattr(c, "set_password"):
        c.set_password(new)
    _update_env_password(new)
    history.add_event("maintenance", f"Contraseña del usuario {settings.user} cambiada (.env actualizado)")


def factory_reset(confirm: str):
    if confirm != RESET_PHRASE:
        raise PermissionError(f"Para restablecer escribe exactamente: {RESET_PHRASE}")
    history.add_event("maintenance", "Restablecimiento de fábrica solicitado desde el dashboard")
    get_client().remote_call("Device", "reinitialize", {})


# ---- API -----------------------------------------------------------------------------------

api = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


@api.get("")
def api_overview():
    return overview()


class HostBody(BaseModel):
    host: str = Field(min_length=1, max_length=253)


@api.post("/traceroute")
def api_traceroute(body: HostBody):
    return traceroute(body.host)


@api.post("/nslookup")
def api_nslookup(body: HostBody):
    return nslookup(body.host)


@api.post("/speedtest")
def api_speedtest():
    return speedtest()


@api.get("/log")
def api_log(lines: int = 300):
    return {"lines": system_log(max(10, min(lines, 2000)))}


@api.get("/backup")
def api_backup():
    data, name = backup()
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


class NtpBody(BaseModel):
    servers: list[str]


@api.put("/ntp")
def api_ntp(body: NtpBody):
    set_ntp(body.servers)
    return {"ok": True}


class DynDnsBody(BaseModel):
    enabled: bool
    service: str = ""
    username: str = ""
    password: str | None = None
    hostname: str = ""


@api.put("/dyndns")
def api_dyndns(body: DynDnsBody):
    set_dyndns(**body.model_dump())
    return {"ok": True}


class RemoteBody(BaseModel):
    http: bool | None = None
    https: bool | None = None
    ssh: bool | None = None
    telnet: bool | None = None


@api.put("/remote-access")
def api_remote(body: RemoteBody):
    set_remote_access(**body.model_dump())
    return {"ok": True}


class SyslogBody(BaseModel):
    enabled: bool
    ip: str = ""
    port: int = 514


@api.put("/syslog")
def api_syslog(body: SyslogBody):
    set_syslog(body.enabled, body.ip, body.port)
    return {"ok": True}


class PasswordBody(BaseModel):
    old: str
    new: str


@api.post("/password")
def api_password(body: PasswordBody):
    change_password(body.old, body.new)
    return {"ok": True}


class ResetBody(BaseModel):
    confirm: str


@api.post("/factory-reset")
def api_factory_reset(body: ResetBody):
    factory_reset(body.confirm)
    return {"ok": True}
