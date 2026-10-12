import logging
from contextlib import asynccontextmanager
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sagemcom import SagemcomError

from . import automations, devices, history, network, router
from . import wifi as wifi_mod
from .expert import api as expert_api
from .network import api as network_api
from .wifi_advanced import api as wifi_advanced_api
from .maintenance import api as maintenance_api
from .phone_usb import api as phone_usb_api
from . import extras, hosts_table, notify, system, ui_custom
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    automations.start()
    extras.start()
    notify.start()
    automations.schedule_automations()  # después de extras: registra acciones nuevas
    yield
    automations.stop()
    try:
        router.get_client().logout()
    except Exception:
        pass


app = FastAPI(title="Sagemcom Dashboard", lifespan=lifespan)
app.include_router(expert_api)
app.include_router(network_api)
app.include_router(wifi_advanced_api)
app.include_router(maintenance_api)
app.include_router(phone_usb_api)
app.include_router(extras.api)
app.include_router(hosts_table.api)
app.include_router(system.api)
app.include_router(notify.api)
app.include_router(ui_custom.api)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    # El frontend cambia a menudo: que el navegador revalide siempre HTML/JS/CSS
    verified: list[int] = []
    token = router.verified_writes.set(verified)
    try:
        response = await call_next(request)
    finally:
        router.verified_writes.reset(token)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    if verified:
        # Cuántos valores se han releído del router tras escribirlos (el frontend lo muestra)
        response.headers["X-Router-Verified"] = str(sum(verified))
    return response


@app.exception_handler(SagemcomError)
async def sagemcom_error(request: Request, exc: SagemcomError):
    return JSONResponse(status_code=502, content={"detail": f"Router: {exc}"})


@app.exception_handler(requests.RequestException)
async def network_error(request: Request, exc: requests.RequestException):
    return JSONResponse(status_code=504, content={"detail": f"No se puede contactar con el router: {exc}"})


@app.exception_handler(ValueError)
async def bad_value(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(LookupError)
async def not_found(request: Request, exc: LookupError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(PermissionError)
async def needs_confirm(request: Request, exc: PermissionError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(TimeoutError)
async def timed_out(request: Request, exc: TimeoutError):
    return JSONResponse(status_code=504, content={"detail": str(exc)})


@app.exception_handler(RuntimeError)
async def config_error(request: Request, exc: RuntimeError):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# ---- lecturas ----------------------------------------------------------------

@app.get("/api/status")
def status():
    def read():
        return {
            "mock": settings.mock,
            "host": settings.host,
            "device": router.device_info(),
            "wan": router.wan(),
            "gpon": router.gpon(),
        }
    # Varias pestañas abiertas no multiplican las lecturas
    return router.cached("status", min(10, settings.cache_ttl), read)


@app.get("/api/hosts")
def hosts():
    return hosts_table.enriched_hosts()


def _find_host(uid: int) -> dict:
    for h in router.hosts():
        if h["uid"] == uid:
            return h
    raise LookupError(f"No existe el dispositivo {uid}")


class NameBody(BaseModel):
    name: str = Field(max_length=64)


@app.post("/api/hosts/{uid}/name")
def rename_host(uid: int, body: NameBody):
    h = _find_host(uid)
    devices.rename(uid, body.name, h["mac"])
    history.add_event("manual", f"{h['name']} renombrado a {body.name.strip() or h['mac']}")
    return {"ok": True}


class DetailsBody(BaseModel):
    # None = no tocar ese campo
    name: str | None = Field(None, max_length=64)
    fixed_ip: str | None = Field(None, max_length=15)  # "" = quitar la IP fija
    type: str | None = Field(None, max_length=40)
    notes: str | None = Field(None, max_length=2000)
    groups: list[str] | None = Field(None, max_length=50)


@app.put("/api/hosts/{uid}/details")
def host_details(uid: int, body: DetailsBody):
    """Guarda de una vez lo que se edita en la ficha del dispositivo. Primero lo
    que va al router (nombre, IP fija), luego lo que solo guarda el dashboard."""
    h = _find_host(uid)
    done = []
    if body.name is not None and body.name.strip() != (h["name"] if h["custom_name"] else ""):
        devices.rename(uid, body.name, h["mac"])
        history.add_event("manual", f"{h['name']} renombrado a {body.name.strip() or h['mac']}")
        done.append("nombre")
    if body.fixed_ip is not None:
        done.append(network.set_fixed_ip(h["mac"], body.fixed_ip.strip() or None))
    if body.groups is not None:
        bad = [g for g in body.groups if not 1 <= len(g.strip()) <= 40]
        if bad:
            raise ValueError("El nombre de cada grupo debe tener entre 1 y 40 caracteres")
        history.set_device_groups(h["mac"], body.groups)
    if body.type is not None or body.notes is not None:
        history.set_device_meta(h["mac"], type=body.type, notes=body.notes)
    return {"ok": True, "message": "; ".join(done) or "Guardado"}


class BlockBody(BaseModel):
    blocked: bool
    minutes: int | None = Field(None, ge=1, le=24 * 60)


@app.post("/api/hosts/{uid}/block")
def block_host(uid: int, body: BlockBody):
    h = _find_host(uid)
    if body.blocked and body.minutes:
        until = automations.block_for(uid, h["name"], body.minutes)
        history.add_event("parental", f"{h['name']} bloqueado {body.minutes} min (hasta las {until:%H:%M})",
                          mac=h["mac"], nombre=h["name"], ip=h["ip"], tipo=h["type"],
                          accion=f"bloqueado {body.minutes} min", hasta=f"{until:%H:%M}")
    else:
        automations.cancel_restore(uid)
        devices.set_blocked(uid, body.blocked)
        what = "bloqueado" if body.blocked else "desbloqueado"
        history.add_event("parental", f"{h['name']} {what}", mac=h["mac"], nombre=h["name"], ip=h["ip"],
                          tipo=h["type"], accion=what)
    return {"ok": True}


@app.get("/api/hosts/{uid}/schedule")
def get_schedule(uid: int):
    return devices.get_schedule(uid)


class ScheduleBody(BaseModel):
    grid: dict[str, list[bool]]


@app.put("/api/hosts/{uid}/schedule")
def put_schedule(uid: int, body: ScheduleBody):
    h = _find_host(uid)
    automations.cancel_restore(uid)
    devices.set_schedule(uid, body.grid)
    hours = sum(sum(v) for v in body.grid.values())
    history.add_event("parental", f"Horario de {h['name']} guardado ({hours} h/semana sin Internet)",
                      mac=h["mac"], nombre=h["name"], ip=h["ip"], tipo=h["type"], accion="horario guardado")
    return {"ok": True}


@app.get("/api/parental")
def parental():
    return devices.parental_status()


@app.get("/api/wifi")
def wifi():
    return router.wifi()


@app.get("/api/history")
def get_history(hours: float = 24):
    return history.samples(hours * 3600)


@app.get("/api/events")
def get_events(limit: int = 50):
    return history.events(limit)


@app.get("/api/xpath")
def read_xpath(path: str):
    """Explorador de solo lectura: devuelve el valor crudo de cualquier xpath."""
    return {"xpath": path, "value": router.get_client().get_value(path)}


# ---- acciones ----------------------------------------------------------------

class KnownBody(BaseModel):
    known: bool


@app.post("/api/hosts/{mac}/known")
def set_known(mac: str, body: KnownBody):
    history.set_known(mac, body.known)
    return {"ok": True}


class WifiBody(BaseModel):
    enabled: bool


@app.post("/api/wifi/{alias}")
def set_wifi(alias: str, body: WifiBody):
    if alias not in {s["alias"] for s in router.wifi()["ssids"]}:
        raise HTTPException(404, f"No existe la red {alias}")
    try:
        router.set_ssid_enabled(alias, body.enabled)
    except ValueError as e:
        raise HTTPException(400, str(e))
    history.add_event("manual", f"Wi-Fi {alias} {'activada' if body.enabled else 'desactivada'} desde el dashboard")
    return {"ok": True}


class PingBody(BaseModel):
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9.:\-]+$")
    count: int = Field(4, ge=1, le=20)


@app.get("/api/wifi/details")
def wifi_details():
    return wifi_mod.details()


class WifiUpdateBody(BaseModel):
    ssid: str | None = None
    password: str | None = None
    visible: bool | None = None


@app.patch("/api/wifi/{alias}")
def update_wifi(alias: str, body: WifiUpdateBody):
    wifi_mod.update_network(alias, body.ssid, body.password, body.visible)
    changed = [k for k, v in (("nombre", body.ssid), ("contraseña", body.password),
                              ("visibilidad", body.visible)) if v is not None]
    history.add_event("manual", f"Wi-Fi {alias}: cambiado {', '.join(changed)}")
    return {"ok": True}


@app.get("/api/wifi/{alias}/qr.svg")
def wifi_qr(alias: str):
    return Response(wifi_mod.qr_svg(alias), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


class ChannelBody(BaseModel):
    channel: int | None = None  # None = automático


@app.post("/api/radios/{radio}/channel")
def set_channel(radio: str, body: ChannelBody):
    wifi_mod.set_channel(radio, body.channel)
    history.add_event("manual", f"Canal de {radio}: {body.channel or 'automático'}")
    return {"ok": True}


@app.post("/api/ping")
def do_ping(body: PingBody):
    return router.ping(body.host, body.count)


class RebootBody(BaseModel):
    confirm: str


@app.post("/api/reboot")
def do_reboot(body: RebootBody):
    if body.confirm != "REINICIAR":
        raise HTTPException(400, 'Para reiniciar envía {"confirm": "REINICIAR"}')
    router.reboot()
    history.add_event("manual", "Reinicio del router desde el dashboard")
    return {"ok": True}


@app.get("/api/automations")
def list_automations():
    runs = automations.next_runs()
    return [{**a, "next_run": runs.get(a["id"])} for a in automations.load_automations()]


@app.get("/api/automations/actions")
def automation_actions():
    return sorted(automations.ACTIONS)


@app.put("/api/automations")
def save_automations(items: list[dict]):
    automations.save_automations(items)
    return {"ok": True}


@app.post("/api/automations/{auto_id}/run")
def run_automation(auto_id: str):
    for a in automations.load_automations():
        if a["id"] == auto_id:
            return {"ok": True, "message": automations.run_automation(a)}
    raise HTTPException(404, "Automatización no encontrada")


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    # La página lleva ya incrustados los textos y colores personalizados
    return ui_custom.index_response()


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
