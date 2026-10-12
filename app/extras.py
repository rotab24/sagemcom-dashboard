"""
Bloque 5: extras que la web del router no tiene.

  - Vigilancia (se llama en cada muestra del recolector): caídas de Internet y del
    router con su duración, señal de la fibra fuera de rango o empeorando, y aparatos
    no reconocidos conectados de madrugada ("intrusos").
  - Grupos de dispositivos: bloquear / horario para varios a la vez.
  - Wi-Fi de invitados temporal: contraseña nueva, se enciende y se apaga sola.
  - Informe de los últimos días (y resumen semanal al móvil).
"""

import logging
import secrets
import statistics
import time
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import automations, devices, history, router
from . import wifi as wifi_mod
from .router import DAYS

log = logging.getLogger(__name__)

GUEST_ALIASES = {"2g": "WL_GUEST2G4", "5g": "WL_GUEST5G"}
WORDS = ["sol", "mar", "luna", "rio", "pino", "flor", "nube", "roca", "lago", "sal", "miel", "faro",
         "isla", "ola", "copo", "gato", "lobo", "oso", "tigre", "zorro", "pato", "rana", "puma", "foca"]
FIBER_MIN, FIBER_MAX = -27.0, -8.0
INTRUDER_HOURS = range(0, 7)


# ---- vigilancia (la llama el recolector en cada muestra) ----------------------------------------

_state = {"router_down_since": None}


def monitor(wan: dict, gpon: dict, hosts: list[dict], now: datetime | None = None):
    now = now or datetime.now()
    ts = now.timestamp()
    # Vuelve el router
    if _state["router_down_since"]:
        mins = round((ts - _state["router_down_since"]) / 60)
        history.add_event("router", f"El router vuelve a responder (sin respuesta {mins} min)",
                          estado="recuperado", duracion=f"{mins} min")
        _state["router_down_since"] = None

    # Internet
    down_since = history.get_setting("internet_down_since")
    if not wan.get("up") and down_since is None:
        history.set_setting("internet_down_since", ts)
        history.add_event("internet_down", f"Internet caído (estado WAN: {wan.get('status') or 'desconocido'})",
                          estado=wan.get("status") or "desconocido")
    elif wan.get("up") and down_since is not None:
        history.set_setting("internet_down_since", None)
        mins = max(1, round((ts - down_since) / 60))
        history.add_event("internet_up", f"Internet recuperado tras {mins} min sin conexión", duracion=f"{mins} min")

    # Fibra: fuera de rango o empeorando respecto a la mediana de la última semana
    rx = gpon.get("rx_dbm")
    today = now.date().isoformat()
    if rx is not None and history.get_setting("fiber_alerted") != today:
        problem = None
        if not FIBER_MIN < rx < FIBER_MAX:
            problem = f"Señal de la fibra fuera de rango: {rx} dBm (lo normal es entre {FIBER_MIN} y {FIBER_MAX})"
        else:
            past = [s["rx_dbm"] for s in history.samples(7 * 86400) if s["rx_dbm"] is not None]
            if len(past) >= 60:
                median = statistics.median(past)
                if rx < median - 3:
                    problem = f"La señal de la fibra ha bajado: {rx} dBm (normalmente {median:.1f} dBm). Revisa el cable de fibra."
        if problem:
            history.set_setting("fiber_alerted", today)
            history.add_event("fiber", problem, rx=f"{rx} dBm")

    # Intrusos: aparatos no marcados como conocidos conectados de madrugada
    if now.hour in INTRUDER_HOURS:
        known = {mac for mac, d in history.devices().items() if d.get("known")}
        alerted = history.get_setting("intruder_alerted", {}) or {}
        changed = False
        for h in hosts:
            if h["active"] and h["mac"] and h["mac"] not in known and alerted.get(h["mac"]) != today:
                alerted[h["mac"]] = today
                changed = True
                history.add_event("intruder", f"{h['name']} ({h['mac']}, {h['ip']}) conectado a las {now:%H:%M} y no está marcado como conocido",
                                  mac=h["mac"], nombre=h["name"], ip=h["ip"], tipo=h.get("type", ""), conexion=h.get("interface", ""))
        if changed:
            history.set_setting("intruder_alerted", alerted)


def monitor_unreachable(error: Exception, now: datetime | None = None):
    if _state["router_down_since"] is None:
        _state["router_down_since"] = (now or datetime.now()).timestamp()
        history.add_event("router", f"El router no responde: {error}", estado="sin respuesta", error=str(error))


# ---- grupos de dispositivos ---------------------------------------------------------------------

def groups_overview() -> list[dict]:
    hosts = {h["mac"]: h for h in router.hosts()}
    return [{"name": name, "macs": macs,
             "members": [{"mac": m, **({k: hosts[m][k] for k in ("uid", "name", "active", "block")} if m in hosts
                                       else {"uid": None, "name": m, "active": False, "block": "none"})} for m in macs]}
            for name, macs in history.groups().items()]


def save_group(name: str, macs: list[str]):
    name = name.strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("El nombre del grupo debe tener entre 1 y 40 caracteres")
    clean = sorted({m.strip().lower() for m in macs if m.strip()})
    if not clean:
        raise ValueError("Un grupo necesita al menos un dispositivo")
    history.save_group(name, clean)


def _group_hosts(name: str) -> list[dict]:
    g = next((g for g in groups_overview() if g["name"] == name), None)
    if g is None:
        raise LookupError(f"No existe el grupo {name}")
    return [m for m in g["members"] if m["uid"] is not None]


def block_group(name: str, blocked: bool, minutes: int | None = None) -> str:
    members = _group_hosts(name)
    for m in members:
        if blocked and minutes:
            automations.block_for(m["uid"], m["name"], minutes)
        else:
            automations.cancel_restore(m["uid"])
            devices.set_blocked(m["uid"], blocked)
    what = f"bloqueado {minutes} min" if blocked and minutes else "bloqueado" if blocked else "desbloqueado"
    msg = f"Grupo {name} {what} ({len(members)} dispositivos)"
    history.add_event("parental", msg, grupo=name, accion=what)
    return msg


def schedule_group(name: str, grid: dict[str, list[bool]]):
    members = _group_hosts(name)
    for m in members:
        automations.cancel_restore(m["uid"])
        devices.set_schedule(m["uid"], grid)
    history.add_event("parental", f"Horario aplicado al grupo {name} ({len(members)} dispositivos)",
                      grupo=name, accion="horario aplicado")


# ---- Wi-Fi de invitados temporal --------------------------------------------------------------------

def _new_password() -> str:
    return f"{secrets.choice(WORDS)}-{secrets.choice(WORDS)}-{secrets.randbelow(900) + 100}"


def guest_status() -> dict:
    g = history.get_setting("guest_temp")
    if not g:
        return {"active": False}
    nets = {n["alias"]: n for n in wifi_mod.details()["networks"]}
    first = nets.get(g["aliases"][0], {})
    return {"active": True, "until": g["until"], "aliases": g["aliases"],
            "ssid": first.get("ssid", ""), "password": first.get("password", "")}


def guest_start(hours: float, bands: list[str], new_password: bool = True) -> dict:
    if not 0.25 <= hours <= 72:
        raise ValueError("La duración debe estar entre 15 minutos y 72 horas")
    aliases = [GUEST_ALIASES[b] for b in bands if b in GUEST_ALIASES]
    if not aliases:
        raise ValueError("Elige al menos una banda (2g / 5g)")
    pw = _new_password() if new_password else None
    for a in aliases:
        if pw:
            wifi_mod.update_network(a, password=pw)
        router.set_ssid_enabled(a, True)
    until = time.time() + hours * 3600
    history.set_setting("guest_temp", {"until": until, "aliases": aliases})
    _schedule_guest_off(until)
    st = guest_status()
    history.add_event("guest", f"Wi-Fi de invitados activada hasta las {datetime.fromtimestamp(until):%H:%M}"
                               + (" con contraseña nueva" if pw else ""),
                      estado="activada", hasta=f"{datetime.fromtimestamp(until):%H:%M}",
                      red=st.get("ssid", ""), contrasena=st.get("password", ""))
    return guest_status()


def guest_stop(reason: str = "manual"):
    g = history.get_setting("guest_temp")
    aliases = g["aliases"] if g else list(GUEST_ALIASES.values())
    for a in aliases:
        router.set_ssid_enabled(a, False)
    history.set_setting("guest_temp", None)
    if automations.scheduler.get_job("guest-off"):
        automations.scheduler.remove_job("guest-off")
    history.add_event("guest", "Wi-Fi de invitados apagada" + (" (tiempo agotado)" if reason == "expired" else ""),
                      estado="apagada")


def _guest_expired():
    try:
        guest_stop("expired")
    except Exception as e:
        history.add_event("error", f"No se pudo apagar la Wi-Fi de invitados: {e}. Reintento en 5 min")
        _schedule_guest_off(time.time() + 300)


def _schedule_guest_off(when: float):
    automations.scheduler.add_job(_guest_expired, "date", run_date=datetime.fromtimestamp(when),
                                  id="guest-off", replace_existing=True)


# ---- informe -------------------------------------------------------------------------------------

def report(days: int = 7) -> dict:
    since = time.time() - days * 86400
    rows = history.samples(days * 86400)
    per_day: dict[str, dict] = {}
    prev = None
    for r in rows:
        day = datetime.fromtimestamp(r["ts"]).date().isoformat()
        d = per_day.setdefault(day, {"day": day, "down_gb": 0.0, "up_gb": 0.0})
        if prev:
            for k, key in (("bytes_rx", "down_gb"), ("bytes_tx", "up_gb")):
                a, b = prev[k], r[k]
                if a is not None and b is not None and b >= a:
                    d[key] += (b - a) / 1e9
        prev = r
    for d in per_day.values():
        d["down_gb"], d["up_gb"] = round(d["down_gb"], 2), round(d["up_gb"], 2)

    evs = history.events(5000, since=since)
    downs = [e for e in evs if e["kind"] == "internet_down"]
    downtime, start = 0.0, None
    for e in sorted(evs, key=lambda e: e["ts"]):
        if e["kind"] == "internet_down":
            start = e["ts"]
        elif e["kind"] == "internet_up" and start is not None:
            downtime += e["ts"] - start
            start = None
    rx = [r["rx_dbm"] for r in rows if r["rx_dbm"] is not None]
    peak = max((r["rate_rx_bps"] or 0 for r in rows), default=0)
    return {
        "days": days,
        "per_day": sorted(per_day.values(), key=lambda d: d["day"]),
        "total_down_gb": round(sum(d["down_gb"] for d in per_day.values()), 2),
        "total_up_gb": round(sum(d["up_gb"] for d in per_day.values()), 2),
        "peak_down_mbps": round(peak / 1e6, 1),
        "outages": len(downs),
        "downtime_min": round(downtime / 60),
        "new_devices": [e["message"] for e in evs if e["kind"] == "new_device"],
        "intruders": [e["message"] for e in evs if e["kind"] == "intruder"],
        "fiber": {"min": min(rx) if rx else None, "avg": round(statistics.mean(rx), 2) if rx else None,
                  "max": max(rx) if rx else None},
        "parental_events": sum(1 for e in evs if e["kind"] == "parental"),
        "coverage_hours": round((rows[-1]["ts"] - rows[0]["ts"]) / 3600, 1) if len(rows) > 1 else 0,
    }


def weekly_report_push():
    r = report(7)
    msg = (f"Últimos 7 días: ↓ {r['total_down_gb']} GB · ↑ {r['total_up_gb']} GB. "
           f"Caídas de Internet: {r['outages']} ({r['downtime_min']} min). "
           f"Aparatos nuevos: {len(r['new_devices'])}. "
           + (f"Fibra media {r['fiber']['avg']} dBm." if r["fiber"]["avg"] is not None else ""))
    history.add_event("report", msg, bajada=f"{r['total_down_gb']} GB", subida=f"{r['total_up_gb']} GB",
                      caidas=str(r["outages"]), minutos_caida=str(r["downtime_min"]),
                      nuevos=str(len(r["new_devices"])), fibra=f"{r['fiber']['avg']} dBm" if r["fiber"]["avg"] is not None else "")


# ---- acciones extra para las automatizaciones -----------------------------------------------------------

def _action_block_group(group: str, minutes: int | None = None):
    return block_group(group, True, minutes)


def _action_unblock_group(group: str):
    return block_group(group, False)


def _action_guest_wifi(hours: float = 3, bands: list[str] | None = None, new_password: bool = True):
    st = guest_start(hours, bands or ["2g", "5g"], new_password)
    return f"Wi-Fi de invitados activada hasta {datetime.fromtimestamp(st['until']):%H:%M}"


def _action_speedtest():
    from .maintenance import speedtest
    r = speedtest()["last"]
    return f"Velocidad: ↓ {r['download']} ↑ {r['upload']} Mbit/s"


def start():
    automations.ACTIONS.update({
        "block_group": _action_block_group,
        "unblock_group": _action_unblock_group,
        "guest_wifi": _action_guest_wifi,
        "speedtest": _action_speedtest,
    })
    automations.scheduler.add_job(weekly_report_push, "cron", day_of_week="sun", hour=20, id="weekly-report",
                                  replace_existing=True)
    g = history.get_setting("guest_temp")
    if g:
        _schedule_guest_off(max(g["until"], time.time() + 5))


# ---- API --------------------------------------------------------------------------------------------

api = APIRouter(prefix="/api/extras", tags=["extras"])


@api.get("/groups")
def api_groups():
    return groups_overview()


class GroupBody(BaseModel):
    macs: list[str]


@api.put("/groups/{name}")
def api_group_put(name: str, body: GroupBody):
    save_group(name, body.macs)
    return {"ok": True}


@api.delete("/groups/{name}")
def api_group_delete(name: str):
    history.delete_group(name)
    return {"ok": True}


class GroupBlockBody(BaseModel):
    blocked: bool
    minutes: int | None = Field(None, ge=1, le=24 * 60)


@api.post("/groups/{name}/block")
def api_group_block(name: str, body: GroupBlockBody):
    return {"ok": True, "message": block_group(name, body.blocked, body.minutes)}


class GroupScheduleBody(BaseModel):
    grid: dict[str, list[bool]]


@api.put("/groups/{name}/schedule")
def api_group_schedule(name: str, body: GroupScheduleBody):
    for d in DAYS:
        if len(body.grid.get(d, [])) != 24:
            raise ValueError(f"Horario incompleto: {d}")
    schedule_group(name, body.grid)
    return {"ok": True}


@api.get("/guest")
def api_guest():
    return guest_status()


class GuestBody(BaseModel):
    hours: float = 3
    bands: list[str] = Field(default_factory=lambda: ["2g", "5g"])
    new_password: bool = True


@api.post("/guest")
def api_guest_start(body: GuestBody):
    return guest_start(body.hours, body.bands, body.new_password)


@api.delete("/guest")
def api_guest_stop():
    guest_stop()
    return {"ok": True}


@api.get("/report")
def api_report(days: int = 7):
    return report(max(1, min(days, 90)))
