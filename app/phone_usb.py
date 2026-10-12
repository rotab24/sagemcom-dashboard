"""
Bloque 4: telefonía y USB.

Telefonía (xpaths main.phones.* de scripts.js):
  VoiceService[@uid='1']/VoiceProfiles/VoiceProfile[@uid='1']/Lines/Line[@uid=n]/CallingFeatures/*
  Registro de llamadas: VoiceService[@uid='1']/CallControl/CallLogs (borrar = deleteChild CallLog[@uid]).
  Prueba de timbre: remoteCall testPhones sobre VoiceService[@uid='1'] ($.xmo.testPhones).
USB y multimedia (mymedia.*):
  Device/USB (puertos y aparatos), StorageServices/StorageService[@uid='1'] (volúmenes, SMB),
  Device/UPnP/Device/UPnPMediaServer + Settings/UPnPMediaServer/FriendlyName (DLNA),
  expulsar = remoteCall eject {force} sobre Device/USB/Ports/Port[SysfsId='…'] ($.xmo.eject).
"""

import re

from fastapi import APIRouter
from pydantic import BaseModel

from . import history
from .router import as_list, field, get_client, to_bool, to_num

VOICE = "Device/Services/VoiceServices/VoiceService[@uid='1']"
LINES = f"{VOICE}/VoiceProfiles/VoiceProfile[@uid='1']/Lines"
STORAGE = "Device/Services/StorageServices/StorageService[@uid='1']"
PHONE_RE = re.compile(r"^[0-9+*#]{3,20}$")

# nombre en la API del dashboard -> campo de CallingFeatures
FEATURES = {
    "call_waiting": "CallWaitingEnable",
    "do_not_disturb": "DoNotDisturbEnable",
    "block_anonymous": "AnonymousCallBlockEnable",
    "hide_my_number": "PermanentCLIREnable",
    "caller_id": "CallerIDEnable",
    "forward_always": "CallForwardUnconditionalEnable",
    "forward_busy": "CallForwardOnBusyEnable",
    "forward_no_answer": "CallForwardOnNoAnswerEnable",
}
FORWARD_NUMBERS = {
    "forward_always_number": "CallForwardUnconditionalNumber",
    "forward_busy_number": "CallForwardOnBusyNumber",
    "forward_no_answer_number": "CallForwardOnNoAnswerNumber",
}


# ---- telefonía -------------------------------------------------------------------------------

def phone_overview() -> dict:
    c = get_client()
    try:
        vs = c.get_value(VOICE) or {}
    except Exception:
        return {"available": False, "lines": [], "call_logs": [], "phones": []}
    lines = []
    for p in as_list(field(vs, "VoiceProfiles", default=[]))[:1]:
        for line in as_list(field(p, "Lines", default=[])):
            if not isinstance(line, dict):
                continue
            cf = field(line, "CallingFeatures", default={}) or {}
            feats = {k: to_bool(field(cf, v, default=False)) for k, v in FEATURES.items()}
            feats.update({k: field(cf, v, default="") or "" for k, v in FORWARD_NUMBERS.items()})
            lines.append({
                "uid": field(line, "uid"),
                "name": field(line, "Name", default=""),
                "number": field(line, "DirectoryNumber", default=""),
                "enabled": str(field(line, "Enable", default="")).upper() in ("ENABLED", "TRUE", "1"),
                "status": field(line, "Status", default=""),
                "registered": str(field(line, "StatusReason", default="")).upper() == "REGISTERED",
                "reason": field(line, "StatusReason", default=""),
                "call_state": field(line, "CallState", default=""),
                "features": feats,
            })
    logs = []
    for log in as_list(field(field(vs, "CallControl", default={}) or {}, "CallLogs", default=[])):
        if isinstance(log, dict):
            logs.append({
                "uid": field(log, "uid"),
                "direction": field(log, "Direction", "CallDirection", default=""),
                "number": field(log, "RemoteParty", "CallingPartyNumber", "CalledPartyNumber", "Number", default=""),
                "start": field(log, "Start", "StartTime", "Date", default=""),
                "duration": to_num(field(log, "Duration", default=0)),
                "status": field(log, "CallTerminationCause", "Status", "Type", default=""),
                "raw": log,
            })
    phones = [{"uid": field(x, "uid"), "type": field(x, "PhyInterfaceType", default=""), "name": field(x, "Description", default="")}
              for x in as_list(field(vs, "PhyInterfaces", default=[])) if isinstance(x, dict)]
    return {"available": True, "lines": lines, "call_logs": logs, "phones": phones}


def update_line(uid: int, changes: dict):
    line = next((ln for ln in phone_overview()["lines"] if ln["uid"] == uid), None)
    if line is None:
        raise LookupError(f"No existe la línea {uid}")
    base = f"{LINES}/Line[@uid='{int(uid)}']/CallingFeatures"
    values = {}
    for k, v in changes.items():
        if v is None:
            continue
        if k in FEATURES:
            values[f"{base}/{FEATURES[k]}"] = bool(v)
        elif k in FORWARD_NUMBERS:
            v = str(v).strip().replace(" ", "")
            if v and not PHONE_RE.match(v):
                raise ValueError(f"Número no válido: {v}")
            values[f"{base}/{FORWARD_NUMBERS[k]}"] = v
        else:
            raise ValueError(f"Opción desconocida: {k}")
    # Un desvío activado necesita número
    for flag, num in (("forward_always", "forward_always_number"), ("forward_busy", "forward_busy_number"),
                      ("forward_no_answer", "forward_no_answer_number")):
        on = changes.get(flag, line["features"][flag])
        number = changes.get(num) if changes.get(num) is not None else line["features"][num]
        if on and not str(number).strip():
            raise ValueError("Para activar un desvío indica el número de destino")
    if values:
        get_client().set_values(values)
        history.add_event("phone", f"Línea {uid}: " + ", ".join(f"{k.split('/')[-1]}={v}" for k, v in values.items()))


def ring_test():
    get_client().remote_call(VOICE, "testPhones", {})
    history.add_event("phone", "Prueba de timbre lanzada")


def delete_call_log(uid: int | None = None):
    c = get_client()
    logs = phone_overview()["call_logs"]
    targets = [c["uid"] for c in logs if uid is None or c["uid"] == uid]
    if uid is not None and not targets:
        raise LookupError("No existe esa llamada")
    for t in targets:
        c.delete_child(f"{VOICE}/CallControl/CallLogs/CallLog[@uid='{t}']")
    history.add_event("phone", f"Registro de llamadas: {len(targets)} borrada(s)")


# ---- USB y multimedia ---------------------------------------------------------------------------

def usb_overview() -> dict:
    c = get_client()
    usb = c.get_value("Device/USB") or {}
    st = c.get_value(STORAGE) or {}
    media = c.get_values({"dlna": "Device/UPnP/Device/UPnPMediaServer",
                          "dlna_name": "Device/UPnP/Settings/UPnPMediaServer/FriendlyName",
                          "workgroup": f"{STORAGE}/SMBConf/WorkGroup"})
    ports = [{"sysfs": field(p, "SysfsId", default=""), "name": field(p, "Name", default=""),
              "standard": field(p, "Standard", default=""), "power": field(p, "Power", default="")}
             for p in as_list(field(usb, "Ports", default=[])) if isinstance(p, dict)]
    devices = []
    for h in as_list(field(field(usb, "USBHosts", default={}) or {}, "Hosts", default=[])):
        for d in as_list(field(h, "Devices", default=[])) if isinstance(h, dict) else []:
            if isinstance(d, dict):
                devices.append({"name": " ".join(x for x in (field(d, "Manufacturer", default=""), field(d, "ProductClass", "Product", default="")) if x) or "Dispositivo USB",
                                "class": field(d, "DeviceClass", default=""), "port": field(d, "Port", default=""),
                                "rate": field(d, "Rate", default="")})
    volumes = []
    for v in as_list(field(st, "LogicalVolumes", default=[])):
        if isinstance(v, dict):
            cap, used = to_num(field(v, "Capacity", default=0)) or 0, to_num(field(v, "UsedSpace", default=0)) or 0
            volumes.append({"uid": field(v, "uid"), "name": field(v, "Name", default=""),
                            "status": field(v, "Status", default=""), "filesystem": field(v, "FileSystem", default=""),
                            "capacity_mb": cap, "used_mb": used,
                            "used_pct": round(100 * used / cap, 1) if cap else None,
                            "shared": to_bool(field(v, "Enable", default=False))})
    return {
        "enabled": to_bool(field(usb, "Enable", default=False)),
        "ports": ports,
        "devices": devices,
        "volumes": volumes,
        "smb": {"enabled": to_bool(field(field(st, "NetworkServer", default={}) or {}, "SMBEnable", default=False)),
                "workgroup": media["workgroup"] or ""},
        "dlna": {"enabled": to_bool(media["dlna"]), "name": media["dlna_name"] or ""},
    }


def set_smb(enabled: bool):
    get_client().set_value(f"{STORAGE}/NetworkServer/SMBEnable", enabled)
    history.add_event("usb", f"Compartir disco USB en la red (SMB) {'activado' if enabled else 'desactivado'}")


def set_dlna(enabled: bool, name: str | None = None):
    values = {"Device/UPnP/Device/UPnPMediaServer": enabled}
    if name is not None:
        name = name.strip()
        if not 1 <= len(name) <= 64:
            raise ValueError("El nombre debe tener entre 1 y 64 caracteres")
        values["Device/UPnP/Settings/UPnPMediaServer/FriendlyName"] = name
    get_client().set_values(values)
    history.add_event("usb", f"Servidor multimedia DLNA {'activado' if enabled else 'desactivado'}")


def eject(sysfs: str, force: bool = False):
    if sysfs not in {p["sysfs"] for p in usb_overview()["ports"]}:
        raise LookupError("Puerto USB no encontrado")
    get_client().remote_call(f"Device/USB/Ports/Port[SysfsId='{sysfs}']", "eject", {"force": force})
    history.add_event("usb", f"Disco USB expulsado ({sysfs})")


# ---- API ---------------------------------------------------------------------------------------

api = APIRouter(tags=["phone-usb"])


@api.get("/api/phone")
def api_phone():
    return phone_overview()


class LineBody(BaseModel):
    call_waiting: bool | None = None
    do_not_disturb: bool | None = None
    block_anonymous: bool | None = None
    hide_my_number: bool | None = None
    caller_id: bool | None = None
    forward_always: bool | None = None
    forward_busy: bool | None = None
    forward_no_answer: bool | None = None
    forward_always_number: str | None = None
    forward_busy_number: str | None = None
    forward_no_answer_number: str | None = None


@api.put("/api/phone/lines/{uid}")
def api_line(uid: int, body: LineBody):
    update_line(uid, body.model_dump(exclude_none=True))
    return {"ok": True}


@api.post("/api/phone/ring-test")
def api_ring():
    ring_test()
    return {"ok": True}


@api.delete("/api/phone/calls")
def api_delete_calls():
    delete_call_log()
    return {"ok": True}


@api.delete("/api/phone/calls/{uid}")
def api_delete_call(uid: int):
    delete_call_log(uid)
    return {"ok": True}


@api.get("/api/usb")
def api_usb():
    return usb_overview()


class EnableBody(BaseModel):
    enabled: bool
    name: str | None = None


@api.put("/api/usb/smb")
def api_smb(body: EnableBody):
    set_smb(body.enabled)
    return {"ok": True}


@api.put("/api/usb/dlna")
def api_dlna(body: EnableBody):
    set_dlna(body.enabled, body.name)
    return {"ok": True}


class EjectBody(BaseModel):
    sysfs: str
    force: bool = False


@api.post("/api/usb/eject")
def api_eject(body: EjectBody):
    eject(body.sysfs, body.force)
    return {"ok": True}
