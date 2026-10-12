"""
Modo experto: escritura directa de cualquier xpath.

Todo lo que hace la web del router se reduce a getValue / setValue / addChild /
deleteChild sobre xpaths, así que esto da acceso al 100 % del router. Por eso
cada escritura exige confirmación y las rutas que pueden dejarte sin Internet
o sin acceso al router piden una frase explícita.
"""

import re
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from . import history
from .router import get_client

DANGER_PHRASE = "ENTIENDO EL RIESGO"

# Rutas cuya modificación puede cortar Internet, el acceso al router o la
# gestión del operador. Se comparan sin distinguir mayúsculas.
DANGEROUS = [
    (r"^Device/Optical", "configuración de la fibra (GPON/PLOAM)"),
    (r"^Device/(PPP|Ethernet/VLANTerminations|Ethernet/Links)", "conexión a Internet (WAN)"),
    (r'^Device/IP/Interfaces/Interface\[[^\]]*(IP_DATA|IP_BR_LAN|IP_VOIP|IP_IPTV)', "interfaces IP de Internet o de la LAN"),
    (r"^Device/ManagementServer", "gestión remota del operador (TR-069)"),
    (r"^Device/UserAccounts", "cuentas de acceso al router"),
    (r"^Device/UserInterface", "acceso a la interfaz web"),
    (r"^Device/Managers/Network", "modo de red del router"),
    (r"^Device/Services/VoiceServices", "telefonía (SIP)"),
    (r"^Device/Firewall/(Level|Config|Chains)", "firewall"),
    (r"^Device$|^Device/DeviceInfo/(FirmwareImages|VendorConfigFiles)", "firmware/configuración completa"),
]


def danger(xpath: str) -> str | None:
    for pat, label in DANGEROUS:
        if re.search(pat, xpath.strip(), re.I):
            return label
    return None


def _check_confirm(xpath: str, confirm: str):
    d = danger(xpath)
    if d and confirm != DANGER_PHRASE:
        raise PermissionError(f"Ruta delicada ({d}). Para continuar escribe exactamente: {DANGER_PHRASE}")
    if not d and confirm not in ("yes", DANGER_PHRASE):
        raise PermissionError("Falta confirmar la operación")


def _short(v) -> str:
    s = repr(v)
    return s if len(s) <= 80 else s[:77] + "..."


def set_value(xpath: str, value, confirm: str):
    _check_confirm(xpath, confirm)
    before = None
    try:
        before = get_client().get_value(xpath)
    except Exception:
        pass
    get_client().set_value(xpath, value)
    history.add_event("expert", f"setValue {xpath}: {_short(before)} → {_short(value)}")
    return {"before": before}


def add_child(xpath: str, value: dict, confirm: str):
    _check_confirm(xpath, confirm)
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError('El valor de addChild debe ser {"TipoDeElemento": {campos...}}, p. ej. {"PortMapping": {...}}')
    r = get_client().add_child(xpath, value)
    history.add_event("expert", f"addChild {xpath}: {_short(value)}")
    return r


def delete_child(xpath: str, confirm: str):
    _check_confirm(xpath, confirm)
    get_client().delete_child(xpath)
    history.add_event("expert", f"deleteChild {xpath}")


# ---- API ------------------------------------------------------------------------

api = APIRouter(prefix="/api/expert", tags=["expert"])


class SetBody(BaseModel):
    path: str
    value: Any
    confirm: str = ""


class AddBody(BaseModel):
    path: str
    value: dict
    confirm: str = ""


class DeleteBody(BaseModel):
    path: str
    confirm: str = ""


@api.get("/danger")
def get_danger(path: str):
    return {"danger": danger(path), "phrase": DANGER_PHRASE}


@api.post("/set")
def api_set(body: SetBody):
    return {"ok": True, **set_value(body.path, body.value, body.confirm)}


@api.post("/add")
def api_add(body: AddBody):
    return {"ok": True, "result": add_child(body.path, body.value, body.confirm)}


@api.post("/delete")
def api_delete(body: DeleteBody):
    delete_child(body.path, body.confirm)
    return {"ok": True}
