"""
Tareas en segundo plano:
  - collector: cada COLLECT_INTERVAL s guarda tráfico, fibra y dispositivos
    en el histórico, y genera un evento cuando aparece un dispositivo nuevo.
  - automatizaciones definidas en automations.json (programadas con cron).

Formato de automations.json:
[
  {"name": "Apagar invitados de noche", "enabled": true, "cron": "0 23 * * *",
   "action": "wifi", "params": {"alias": "WL_GUEST", "enabled": false}},
  ...
]
Acciones disponibles: ver ACTIONS más abajo.
"""

import json
import logging
import uuid
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import devices, history, router
from .config import settings

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Europe/Madrid")


# ---- collector ---------------------------------------------------------------

def collect():
    from . import extras  # import tardío: extras importa este módulo
    try:
        wan = router.wan()
        gpon = router.gpon()
        hosts = router.hosts()
    except Exception as e:  # el router puede estar reiniciando, sin red, etc.
        log.warning("collect: no se pudo leer el router: %s", e)
        extras.monitor_unreachable(e)
        return
    # En la primera ejecución todos los dispositivos son "nuevos": no avisar
    first_run = history.is_empty_devices()
    history.record_sample(wan, gpon, sum(h["active"] for h in hosts))
    for h in history.update_devices(hosts):
        if not first_run:
            history.add_event("new_device", f"Nuevo dispositivo: {h['name']} ({h['mac']}, {h['ip']})",
                              mac=h["mac"], nombre=h["name"], ip=h["ip"], tipo=h.get("type", ""), conexion=h.get("interface", ""))
    try:
        extras.monitor(wan, gpon, hosts)
    except Exception as e:
        log.warning("Vigilancia: %s", e)


# ---- acciones programables ---------------------------------------------------

def _action_wifi(alias: str, enabled: bool):
    router.set_ssid_enabled(alias, enabled)
    return f"Wi-Fi {alias} {'activada' if enabled else 'desactivada'}"


def _action_reboot():
    router.reboot()
    return "Router reiniciado"


def _action_ping_watchdog(host: str = "1.1.1.1", count: int = 4, reboot_on_fail: bool = False):
    """Hace ping desde el router; si se pierden todos los paquetes registra un
    evento y, opcionalmente, reinicia el router."""
    r = router.ping(host, count)
    if r["received"] > 0:
        return f"Ping {host}: {r['received']}/{r['sent']} OK"
    if reboot_on_fail:
        router.reboot()
        return f"Ping {host} sin respuesta: router reiniciado"
    return f"Ping {host} sin respuesta"


ACTIONS = {
    "wifi": _action_wifi,
    "reboot": _action_reboot,
    "ping_watchdog": _action_ping_watchdog,
}


def load_automations() -> list[dict]:
    if not settings.automations_file.exists():
        return []
    items = json.loads(settings.automations_file.read_text(encoding="utf-8"))
    for i, a in enumerate(items):
        a.setdefault("id", f"auto-{i}")
        a.setdefault("enabled", False)
        a.setdefault("params", {})
    return items


def validate_automation(a: dict) -> dict:
    name = str(a.get("name", "")).strip()
    if not name:
        raise ValueError("Cada automatización necesita un nombre")
    if a.get("action") not in ACTIONS:
        raise ValueError(f"[{name}] acción desconocida: {a.get('action')} (posibles: {', '.join(sorted(ACTIONS))})")
    try:
        CronTrigger.from_crontab(str(a.get("cron", "")), timezone="Europe/Madrid")
    except ValueError as e:
        raise ValueError(f"[{name}] horario cron no válido '{a.get('cron')}': {e}")
    params = a.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError(f"[{name}] los parámetros deben ser un objeto JSON")
    return {"id": str(a.get("id") or f"auto-{uuid.uuid4().hex[:8]}"), "name": name, "enabled": bool(a.get("enabled")),
            "cron": str(a["cron"]).strip(), "action": a["action"], "params": params}


def save_automations(items: list[dict]):
    clean = [validate_automation(a) for a in items]
    ids = [a["id"] for a in clean]
    if len(ids) != len(set(ids)):
        raise ValueError("Hay automatizaciones con el mismo id")
    settings.automations_file.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    schedule_automations()
    history.add_event("automation", f"Automatizaciones guardadas ({sum(a['enabled'] for a in clean)} activas de {len(clean)})")


_auto_jobs: set[str] = set()


def schedule_automations():
    """(Re)programa las automatizaciones activas sin reiniciar el servidor."""
    for job_id in list(_auto_jobs):
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
    _auto_jobs.clear()
    for a in load_automations():
        if a["action"] not in ACTIONS:
            log.warning("Automatización %s: acción desconocida %s", a["name"], a["action"])
            continue
        if a["enabled"]:
            scheduler.add_job(_scheduled, CronTrigger.from_crontab(a["cron"], timezone="Europe/Madrid"),
                              args=[a], id=a["id"], max_instances=1, coalesce=True, replace_existing=True)
            _auto_jobs.add(a["id"])


def run_automation(a: dict) -> str:
    fn = ACTIONS.get(a["action"])
    if fn is None:
        raise ValueError(f"Acción desconocida: {a['action']}")
    try:
        msg = fn(**a["params"])
        history.add_event("automation", f"[{a['name']}] {msg}")
        return msg
    except Exception as e:
        history.add_event("error", f"[{a['name']}] falló: {e}")
        raise


def _scheduled(a: dict):
    try:
        run_automation(a)
    except Exception as e:
        log.warning("Automatización %s falló: %s", a["name"], e)


def _apply_wifi_schedules():
    from .wifi_advanced import apply_schedules  # import tardío: wifi_advanced importa router
    try:
        apply_schedules()
    except Exception as e:
        log.warning("No se pudieron aplicar los horarios de Wi-Fi: %s", e)
        history.add_event("error", f"Horario de Wi-Fi: {e}")


# ---- bloqueo temporal ------------------------------------------------------------

def _restore_schedule(uid: int, name: str, grid: dict):
    try:
        devices.set_schedule(uid, grid)
        history.pop_pending_restore(uid)
        history.add_event("parental", f"Fin del bloqueo temporal de {name}", nombre=name, accion="fin del bloqueo")
    except Exception as e:
        log.warning("No se pudo restaurar el horario de %s: %s (se reintenta en 5 min)", name, e)
        history.add_event("error", f"No se pudo desbloquear {name}: {e}. Reintento en 5 min")
        _schedule_restore(uid, name, grid, time.time() + 300)


def _schedule_restore(uid: int, name: str, grid: dict, run_at: float):
    history.save_pending_restore(uid, run_at, name, grid)
    scheduler.add_job(_restore_schedule, "date", run_date=datetime.fromtimestamp(run_at),
                      args=[uid, name, grid], id=f"restore-{uid}", replace_existing=True)


def block_for(uid: int, name: str, minutes: int) -> datetime:
    """Bloquea ya y, pasados `minutes`, deja el horario como estaba antes."""
    pending = {p["uid"]: p for p in history.pending_restores()}
    # Si ya había un bloqueo temporal, se conserva el horario original
    previous = pending[uid]["grid"] if uid in pending else devices.get_schedule(uid)
    devices.set_blocked(uid, True)
    run_at = time.time() + minutes * 60
    _schedule_restore(uid, name, previous, run_at)
    return datetime.fromtimestamp(run_at)


def cancel_restore(uid: int):
    history.pop_pending_restore(uid)
    if scheduler.get_job(f"restore-{uid}"):
        scheduler.remove_job(f"restore-{uid}")


def start():
    history.init()
    scheduler.add_job(collect, "interval", seconds=settings.collect_interval, id="collect",
                      max_instances=1, coalesce=True)
    scheduler.add_job(history.prune, "cron", hour=4, id="prune")
    # Horarios de Wi-Fi (el firmware no los tiene): se comprueban al empezar cada hora
    scheduler.add_job(_apply_wifi_schedules, "cron", minute=0, second=5, id="wifi-schedules",
                      max_instances=1, coalesce=True)
    scheduler.add_job(_apply_wifi_schedules, id="wifi-schedules-now")
    scheduler.start()
    # Bloqueos temporales que quedaron pendientes si el servidor se paró
    for p in history.pending_restores():
        _schedule_restore(p["uid"], p["name"], p["grid"], max(p["run_at"], time.time() + 5))
    # Primera muestra en cuanto arranca, sin esperar al primer intervalo
    scheduler.add_job(collect, id="collect-now")


def stop():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def next_runs() -> dict[str, str | None]:
    return {j.id: (j.next_run_time.isoformat() if j.next_run_time else None) for j in scheduler.get_jobs()}
