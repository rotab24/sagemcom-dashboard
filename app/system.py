"""
El propio dashboard: identificador de arranque y reinicio desde la interfaz.

Reiniciar = lanzar iniciar.ps1 en segundo plano (espera a que este proceso
termine y arranca otro igual) y salir. El navegador sabe que ha funcionado de
verdad cuando /api/system devuelve un boot_id distinto.
"""

import logging
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from . import automations, router
from .config import settings

log = logging.getLogger(__name__)
api = APIRouter(prefix="/api/system", tags=["sistema"])

ROOT = Path(__file__).resolve().parent.parent
START_SCRIPT = ROOT / "iniciar.ps1"
BOOT_ID = secrets.token_hex(6)
STARTED = time.time()
EXIT_DELAY = 0.8  # da tiempo a que la respuesta llegue al navegador


def info() -> dict:
    return {"boot_id": BOOT_ID, "started": STARTED, "pid": os.getpid(), "mock": settings.mock,
            "can_restart": os.name == "nt" and START_SCRIPT.exists()}


def _launch_restarter(port: int):
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
           "-File", str(START_SCRIPT), "-NoBrowser", "-Port", str(port), "-WaitPid", str(os.getpid())]
    base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    kwargs = dict(cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  close_fds=True)
    try:
        # Que no muera con este proceso si se lanzó dentro de un "job" de Windows
        subprocess.Popen(cmd, creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:
        subprocess.Popen(cmd, creationflags=base, **kwargs)


def _exit():
    log.info("Reiniciando el dashboard (pid %s)", os.getpid())
    try:
        automations.stop()
        router.get_client().logout()
    except Exception:
        pass
    finally:
        os._exit(0)


def restart(port: int):
    if not info()["can_restart"]:
        raise RuntimeError("El reinicio desde la web solo funciona en Windows con iniciar.ps1 en la carpeta del proyecto")
    _launch_restarter(port)
    threading.Timer(EXIT_DELAY, _exit).start()


@api.get("")
def api_info():
    return info()


class RestartBody(BaseModel):
    confirm: bool  # cuerpo JSON obligatorio: una web ajena no puede lanzarlo con un simple formulario


@api.post("/restart")
def api_restart(body: RestartBody, request: Request):
    if not body.confirm:
        raise ValueError("Falta confirmar el reinicio")
    restart(request.url.port or 8765)
    return {"ok": True, "boot_id": BOOT_ID, "message": "Reiniciando el dashboard…"}
