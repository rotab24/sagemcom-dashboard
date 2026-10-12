"""
Avisos al móvil con ntfy (https://ntfy.sh), totalmente configurables.

  - Varias **listas** (temas de ntfy): cada persona se suscribe a la que le interese.
  - En cada lista, una **regla por categoría** de evento: activada, título y mensaje
    con plantilla ({nombre}, {ip}…), prioridad, etiquetas (emojis de ntfy) y, en los
    avisos de un aparato, a qué aparatos se aplica (todos / solo / excepto ciertos
    grupos o tipos).
  - Horas de silencio por lista: los avisos llegan sin sonido o no se envían
    (los de prioridad urgente sí suenan).

Escucha los eventos del dashboard (history.listeners); no lee del router.
Se guarda en settings["notify_lists"]; la configuración antigua ("notify") se
convierte en una lista "Principal" la primera vez.
"""

import logging
import re
import secrets
import threading
import time
from datetime import datetime

import requests
from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import history

log = logging.getLogger(__name__)
api = APIRouter(prefix="/api/notify", tags=["avisos"])

DEVICE_VARS = ["nombre", "mac", "ip", "tipo", "grupos", "conexion"]
COMMON_VARS = ["mensaje", "hora", "fecha"]
# Cada categoría: texto, valores por defecto de su regla, variables propias y si es de un aparato
CATEGORIES = {
    "new_device": {"label": "Aparato nuevo en la red", "title": "Aparato nuevo", "tags": ["new"], "priority": 3,
                   "device": True, "vars": DEVICE_VARS, "on": True,
                   "example": {"mensaje": "Nuevo dispositivo: Galaxy-S24 (aa:bb:cc:dd:ee:ff, 192.168.1.57)",
                               "nombre": "Galaxy-S24", "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.57",
                               "tipo": "Smartphone", "grupos": "", "conexion": "WiFi"}},
    "intruder": {"label": "Aparato desconocido conectado de madrugada", "title": "¡Intruso!", "tags": ["warning"],
                 "priority": 5, "device": True, "vars": DEVICE_VARS, "on": True,
                 "example": {"mensaje": "Galaxy-S24 (aa:bb:cc:dd:ee:ff, 192.168.1.57) conectado a las 03:12 y no está marcado como conocido",
                             "nombre": "Galaxy-S24", "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.57",
                             "tipo": "Smartphone", "grupos": "", "conexion": "WiFi"}},
    "internet_down": {"label": "Internet caído", "title": "Internet caído", "tags": ["x"], "priority": 4,
                      "device": False, "vars": ["estado"], "on": True,
                      "example": {"mensaje": "Internet caído (estado WAN: DOWN)", "estado": "DOWN"}},
    "internet_up": {"label": "Internet recuperado", "title": "Internet recuperado", "tags": ["white_check_mark"],
                    "priority": 3, "device": False, "vars": ["duracion"], "on": True,
                    "example": {"mensaje": "Internet recuperado tras 12 min sin conexión", "duracion": "12 min"}},
    "router": {"label": "Router sin respuesta / recuperado", "title": "Router", "tags": ["satellite"], "priority": 4,
               "device": False, "vars": ["estado", "duracion", "error"], "on": True,
               "example": {"mensaje": "El router vuelve a responder (sin respuesta 3 min)", "estado": "recuperado",
                           "duracion": "3 min", "error": ""}},
    "fiber": {"label": "Problemas con la señal de la fibra", "title": "Fibra", "tags": ["warning"], "priority": 4,
              "device": False, "vars": ["rx"], "on": True,
              "example": {"mensaje": "La señal de la fibra ha bajado: -24.1 dBm (normalmente -19.8 dBm)", "rx": "-24.1 dBm"}},
    "parental": {"label": "Bloqueos y horarios (inicio/fin)", "title": "Control parental", "tags": ["lock"],
                 "priority": 2, "device": True, "vars": DEVICE_VARS + ["accion", "hasta", "grupo"], "on": False,
                 "example": {"mensaje": "Galaxy-S24 bloqueado 30 min (hasta las 21:30)", "nombre": "Galaxy-S24",
                             "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.57", "tipo": "Smartphone", "grupos": "Niños",
                             "conexion": "WiFi", "accion": "bloqueado 30 min", "hasta": "21:30", "grupo": ""}},
    "guest": {"label": "Wi‑Fi de invitados", "title": "Wi‑Fi invitados", "tags": ["wifi"], "priority": 2,
              "device": False, "vars": ["estado", "hasta", "red", "contrasena"], "on": False,
              "example": {"mensaje": "Wi-Fi de invitados activada hasta las 23:00 con contraseña nueva",
                          "estado": "activada", "hasta": "23:00", "red": "MiCasa-Invitados", "contrasena": "sol-mar-123"}},
    "report": {"label": "Resumen semanal", "title": "Resumen semanal", "tags": ["bar_chart"], "priority": 2,
               "device": False, "vars": ["bajada", "subida", "caidas", "minutos_caida", "nuevos", "fibra"], "on": True,
               "example": {"mensaje": "Últimos 7 días: ↓ 120.5 GB · ↑ 18.2 GB. Caídas de Internet: 1 (12 min).",
                           "bajada": "120.5 GB", "subida": "18.2 GB", "caidas": "1", "minutos_caida": "12",
                           "nuevos": "2", "fibra": "-19.8 dBm"}},
    "error": {"label": "Errores del dashboard", "title": "Error del dashboard", "tags": ["warning"], "priority": 3,
              "device": False, "vars": [], "on": False,
              "example": {"mensaje": "No se pudo desbloquear Galaxy-S24: el router no responde. Reintento en 5 min"}},
}
FILTER_MODES = ("all", "include", "exclude")
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
TAG_RE = re.compile(r"^[A-Za-z0-9_+-]{1,40}$")
VAR_RE = re.compile(r"\{(\w+)\}")

_lock = threading.Lock()
_status: dict[str, dict] = {}  # último envío por lista (solo en memoria)


# ---- configuración ---------------------------------------------------------------------------

def default_rule(cat: str, enabled: bool | None = None) -> dict:
    c = CATEGORIES[cat]
    return {"enabled": c["on"] if enabled is None else enabled, "title": c["title"], "template": "{mensaje}",
            "priority": c["priority"], "tags": list(c["tags"]),
            "filter": {"mode": "all", "groups": [], "types": []}}


def new_list(name: str = "Nueva lista", enabled_kinds: list[str] | None = None) -> dict:
    return {"id": secrets.token_hex(4), "name": name, "enabled": False, "server": "https://ntfy.sh",
            "topic": "", "token": "", "quiet": {"enabled": False, "start": 23, "end": 8, "mode": "silent"},
            "rules": {c: default_rule(c, None if enabled_kinds is None else c in enabled_kinds) for c in CATEGORIES}}


def load_lists() -> list[dict]:
    lists = history.get_setting("notify_lists")
    if lists is None:
        lists = []
        old = history.get_setting("notify")  # formato anterior: un único tema
        if old and old.get("topic"):
            lst = new_list("Principal", old.get("kinds"))
            lst.update(enabled=bool(old.get("enabled")), topic=old["topic"],
                       server=old.get("server") or "https://ntfy.sh")
            lists.append(lst)
        history.set_setting("notify_lists", lists)
    for lst in lists:  # categorías añadidas después de crear la lista
        for c in CATEGORIES:
            lst["rules"].setdefault(c, default_rule(c))
    return lists


def public(lst: dict) -> dict:
    """La lista sin el token (solo se dice si hay uno)."""
    return {**{k: v for k, v in lst.items() if k != "token"}, "has_token": bool(lst.get("token")),
            "status": _status.get(lst["id"])}


def _clean_rule(cat: str, r: dict) -> dict:
    prio = int(r.get("priority", 3))
    if not 1 <= prio <= 5:
        raise ValueError(f"{CATEGORIES[cat]['label']}: la prioridad va de 1 a 5")
    title = str(r.get("title", "")).strip()[:100]
    template = str(r.get("template", "")).strip()[:1000] or "{mensaje}"
    tags = [t.strip() for t in r.get("tags", []) if str(t).strip()][:10]
    bad = [t for t in tags if not TAG_RE.match(t)]
    if bad:
        raise ValueError(f"{CATEGORIES[cat]['label']}: etiqueta no válida '{bad[0]}' (solo letras, números, _ + -)")
    f = r.get("filter") or {}
    mode = f.get("mode", "all")
    if mode not in FILTER_MODES:
        raise ValueError("Filtro de aparatos no válido")
    clean = lambda xs: sorted({str(x).strip()[:40] for x in xs or [] if str(x).strip()})  # noqa: E731
    groups, types = clean(f.get("groups")), clean(f.get("types"))
    if mode != "all" and not (groups or types):
        raise ValueError(f"{CATEGORIES[cat]['label']}: elige al menos un grupo o tipo para el filtro")
    return {"enabled": bool(r.get("enabled")), "title": title, "template": template, "priority": prio,
            "tags": tags, "filter": {"mode": mode, "groups": groups, "types": types}}


def save_list(list_id: str, data: dict) -> dict:
    name = str(data.get("name", "")).strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("El nombre de la lista debe tener entre 1 y 40 caracteres")
    topic = str(data.get("topic", "")).strip()
    if topic and not TOPIC_RE.match(topic):
        raise ValueError("El tema debe tener 8–64 letras, números, - o _ (sin espacios). Usa algo difícil de adivinar.")
    if data.get("enabled") and not topic:
        raise ValueError("Para activar la lista hace falta un tema")
    server = str(data.get("server", "https://ntfy.sh")).strip().rstrip("/")
    if not server.startswith(("http://", "https://")):
        raise ValueError("El servidor debe empezar por http:// o https://")
    q = data.get("quiet") or {}
    start, end = int(q.get("start", 23)), int(q.get("end", 8))
    if not (0 <= start <= 23 and 0 <= end <= 23):
        raise ValueError("Las horas de silencio van de 0 a 23")
    if q.get("mode", "silent") not in ("silent", "skip"):
        raise ValueError("Modo de silencio no válido")
    rules = data.get("rules") or {}
    unknown = set(rules) - set(CATEGORIES)
    if unknown:
        raise ValueError(f"Categorías desconocidas: {', '.join(sorted(unknown))}")
    with _lock:
        lists = load_lists()
        current = next((x for x in lists if x["id"] == list_id), None)
        if any(x["topic"] == topic and x["id"] != list_id for x in lists if topic):
            raise ValueError("Ya hay otra lista con ese tema")
        lst = current or {**new_list(name), "id": list_id}
        token = data.get("token")  # None = no tocar; "" = quitar
        lst.update({
            "name": name, "enabled": bool(data.get("enabled")), "server": server, "topic": topic,
            "token": lst.get("token", "") if token is None else str(token).strip(),
            "quiet": {"enabled": bool(q.get("enabled")), "start": start, "end": end, "mode": q.get("mode", "silent")},
            "rules": {c: _clean_rule(c, rules[c]) if c in rules else lst["rules"].get(c, default_rule(c))
                      for c in CATEGORIES},
        })
        if current is None:
            lists.append(lst)
        history.set_setting("notify_lists", lists)
    return public(lst)


def delete_list(list_id: str):
    with _lock:
        lists = load_lists()
        if not any(x["id"] == list_id for x in lists):
            raise LookupError("No existe esa lista")
        history.set_setting("notify_lists", [x for x in lists if x["id"] != list_id])


# ---- envío ----------------------------------------------------------------------------------

def render(template: str, values: dict) -> str:
    """Sustituye {variable}; las que no existen se quedan tal cual (así se ve el error).
    No usa str.format: una plantilla no puede leer atributos de nada."""
    return VAR_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def _device_info(data: dict) -> dict:
    """Completa los datos del aparato con lo que sabe el dashboard (nombre, tipo, grupos)."""
    mac = (data.get("mac") or "").lower()
    out = {k: data.get(k, "") or "" for k in DEVICE_VARS}
    groups = [n for n, macs in history.groups().items() if mac and mac in macs]
    if data.get("grupo"):
        groups.append(data["grupo"])
    d = history.devices().get(mac, {}) if mac else {}
    out["tipo"] = d.get("type") or out["tipo"]
    out["grupos"] = ", ".join(sorted(set(groups)))
    return {**out, "_groups": set(groups), "_types": {t for t in (d.get("type"), data.get("tipo")) if t}}


def passes_filter(f: dict, dev: dict) -> bool:
    if f["mode"] == "all":
        return True
    match = bool(set(f["groups"]) & dev["_groups"]) or bool(set(f["types"]) & dev["_types"])
    return match if f["mode"] == "include" else not match


def in_quiet_hours(q: dict, hour: int) -> bool:
    if not q.get("enabled") or q["start"] == q["end"]:
        return False
    s, e = q["start"], q["end"]
    return s <= hour < e if s < e else hour >= s or hour < e  # p. ej. de 23 a 8 cruza la medianoche


def build(lst: dict, kind: str, message: str, data: dict, now: datetime | None = None) -> dict | None:
    """Lo que se mandaría a ntfy para este evento, o None si la lista no lo quiere."""
    if kind not in CATEGORIES or not lst.get("enabled") or not lst.get("topic"):
        return None
    rule = lst["rules"].get(kind) or default_rule(kind)
    if not rule["enabled"]:
        return None
    now = now or datetime.now()
    values = {"mensaje": message, "hora": f"{now:%H:%M}", "fecha": f"{now:%d/%m/%Y}",
              **{k: "" if v is None else str(v) for k, v in data.items()}}
    if CATEGORIES[kind]["device"]:
        dev = _device_info(data)
        if not passes_filter(rule["filter"], dev):
            return None
        values.update({k: v for k, v in dev.items() if not k.startswith("_")})
    priority = rule["priority"]
    if in_quiet_hours(lst["quiet"], now.hour) and priority < 5:
        if lst["quiet"]["mode"] == "skip":
            return None
        priority = 1  # llega sin sonido ni vibración
    return {"topic": lst["topic"], "title": render(rule["title"], values) or CATEGORIES[kind]["title"],
            "message": render(rule["template"], values), "priority": priority, "tags": rule["tags"]}


def post(lst: dict, payload: dict):
    headers = {"Authorization": f"Bearer {lst['token']}"} if lst.get("token") else {}
    resp = requests.post(lst["server"], json=payload, headers=headers, timeout=8)
    resp.raise_for_status()


def _send(lst: dict, payload: dict, kind: str):
    try:
        post(lst, payload)
        _status[lst["id"]] = {"ts": time.time(), "ok": True, "kind": kind, "title": payload["title"]}
    except Exception as e:
        _status[lst["id"]] = {"ts": time.time(), "ok": False, "kind": kind, "error": str(e)}
        log.warning("Aviso a la lista %s no enviado: %s", lst["name"], e)


def on_event(kind: str, message: str, data: dict | None = None):
    for lst in load_lists():
        payload = build(lst, kind, message, data or {})
        if payload:
            threading.Thread(target=_send, args=(lst, payload, kind), daemon=True).start()


def send_test(list_id: str, kind: str | None = None) -> dict:
    """Envía ya (sin hilo, para poder decir si ha llegado a ntfy) un ejemplo de la
    categoría con su plantilla, o un mensaje de prueba genérico."""
    lst = next((x for x in load_lists() if x["id"] == list_id), None)
    if lst is None:
        raise LookupError("No existe esa lista")
    if not lst["topic"]:
        raise ValueError("Falta el tema de la lista")
    if kind:
        if kind not in CATEGORIES:
            raise ValueError("Categoría desconocida")
        rule = lst["rules"][kind]
        ex = CATEGORIES[kind]["example"]
        values = {"hora": f"{datetime.now():%H:%M}", "fecha": f"{datetime.now():%d/%m/%Y}", **ex}
        payload = {"topic": lst["topic"], "title": "[Prueba] " + (render(rule["title"], values) or CATEGORIES[kind]["title"]),
                   "message": render(rule["template"], values), "priority": rule["priority"], "tags": rule["tags"]}
    else:
        payload = {"topic": lst["topic"], "title": f"Prueba · {lst['name']}",
                   "message": "Si ves esto, los avisos del router funcionan 🎉", "priority": 3, "tags": ["tada"]}
    try:
        post(lst, payload)
    except requests.RequestException as e:
        _status[lst["id"]] = {"ts": time.time(), "ok": False, "kind": kind or "test", "error": str(e)}
        raise RuntimeError(f"ntfy no ha aceptado el aviso: {e}")
    _status[lst["id"]] = {"ts": time.time(), "ok": True, "kind": kind or "test", "title": payload["title"]}
    return {"ok": True, "message": f"Enviado a «{lst['topic']}»: ntfy lo ha aceptado", "sent": payload}


def start():
    if on_event not in history.listeners:
        history.listeners.append(on_event)


# ---- API --------------------------------------------------------------------------------------

@api.get("")
def api_get():
    return {
        "categories": {k: {"label": c["label"], "device": c["device"], "vars": COMMON_VARS + c["vars"],
                           "defaults": default_rule(k), "example": c["example"]} for k, c in CATEGORIES.items()},
        "lists": [public(x) for x in load_lists()],
    }


class ListBody(BaseModel):
    name: str = Field(max_length=40)
    enabled: bool = False
    server: str = Field("https://ntfy.sh", max_length=200)
    topic: str = Field("", max_length=64)
    token: str | None = Field(None, max_length=200)
    quiet: dict = Field(default_factory=dict)
    rules: dict = Field(default_factory=dict)


@api.post("/lists")
def api_create(body: ListBody):
    return save_list(secrets.token_hex(4), body.model_dump())


@api.put("/lists/{list_id}")
def api_update(list_id: str, body: ListBody):
    if not any(x["id"] == list_id for x in load_lists()):
        raise LookupError("No existe esa lista")
    return save_list(list_id, body.model_dump())


@api.delete("/lists/{list_id}")
def api_delete(list_id: str):
    delete_list(list_id)
    return {"ok": True}


class TestBody(BaseModel):
    kind: str | None = None


@api.post("/lists/{list_id}/test")
def api_test(list_id: str, body: TestBody):
    return send_test(list_id, body.kind)
