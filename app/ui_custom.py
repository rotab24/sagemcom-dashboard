"""
Personalización de la interfaz desde la propia web (modo edición, Ctrl+E).

  - Textos: data/texts.json, {clave: texto}. La clave la calcula el navegador:
    "<id de la sección>|<texto original>" (o "...|ph|..." para un placeholder).
  - Apariencia: data/theme.json, colores (variables CSS) para el modo claro y el
    oscuro, tamaño de letra y escala.

La página se sirve con las personalizaciones ya incrustadas (sin parpadeo).
Los textos se ponen siempre con textContent en el navegador: nunca es HTML.
"""

import json
import os
import re
import threading
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .config import settings

api = APIRouter(prefix="/api/ui", tags=["interfaz"])

STATIC = Path(__file__).parent / "static"
COLOR_VARS = {"--bg", "--surface", "--text", "--muted", "--border", "--accent", "--ok", "--warn",
              "--denied", "--bad", "--rx", "--tx", "--code-bg", "--primary-text", "--warn-bg"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_TEXTS = 3000
_lock = threading.Lock()


def _path(name: str) -> Path:
    return settings.db_path.parent / name


def _read(name: str, default):
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _write(name: str, data):
    p = _path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)  # nunca queda un archivo a medias


# ---- textos ---------------------------------------------------------------------------------

def texts() -> dict[str, str]:
    data = _read("texts.json", {})
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(data, dict) else {}


def set_text(key: str, value: str | None):
    key = key.strip()
    if not key or "|" not in key:
        raise ValueError("Clave de texto no válida")
    with _lock:
        data = texts()
        if value is None or not value.strip():
            data.pop(key, None)  # vacío = volver al texto original
        else:
            if key not in data and len(data) >= MAX_TEXTS:
                raise ValueError(f"Demasiados textos personalizados (máximo {MAX_TEXTS})")
            data[key] = " ".join(value.split())  # el HTML junta los espacios igualmente
        _write("texts.json", data)


def reset_texts():
    with _lock:
        _write("texts.json", {})


# ---- apariencia -----------------------------------------------------------------------------

def theme() -> dict:
    t = _read("theme.json", {})
    t = t if isinstance(t, dict) else {}
    return {"light": t.get("light") or {}, "dark": t.get("dark") or {},
            "font_size": t.get("font_size"), "zoom": t.get("zoom")}


def _clean_colors(colors: dict) -> dict:
    out = {}
    for k, v in (colors or {}).items():
        if k not in COLOR_VARS:
            raise ValueError(f"Variable de color no permitida: {k}")
        if not HEX.match(str(v)):
            raise ValueError(f"Color no válido para {k}: {v} (usa #rrggbb)")
        out[k] = str(v).lower()
    return out


def save_theme(data: dict) -> dict:
    fs, zoom = data.get("font_size"), data.get("zoom")
    if fs is not None and not 11 <= int(fs) <= 22:
        raise ValueError("El tamaño de letra va de 11 a 22 px")
    if zoom is not None and not 0.7 <= float(zoom) <= 1.6:
        raise ValueError("La escala va del 70 % al 160 %")
    t = {"light": _clean_colors(data.get("light")), "dark": _clean_colors(data.get("dark")),
         "font_size": int(fs) if fs is not None else None,
         "zoom": round(float(zoom), 2) if zoom is not None else None}
    with _lock:
        _write("theme.json", t)
    return t


def theme_css(t: dict) -> str:
    """CSS que se añade después de style.css. Los colores claros van dentro de un
    @media para no pisar los del modo oscuro (y al revés)."""
    decl = lambda d: ";".join(f"{k}:{v}" for k, v in sorted(d.items()))  # noqa: E731
    parts = []
    if t["light"]:
        parts.append(f"@media not all and (prefers-color-scheme: dark){{:root{{{decl(t['light'])}}}}}")
    if t["dark"]:
        parts.append(f"@media (prefers-color-scheme: dark){{:root{{{decl(t['dark'])}}}}}")
    if t["font_size"]:
        parts.append(f":root{{--font-size:{int(t['font_size'])}px}}")
    if t["zoom"] and t["zoom"] != 1:
        parts.append(f"body{{zoom:{float(t['zoom'])}}}")
    return "\n".join(parts)


def render_index() -> str:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    t = theme()
    data = json.dumps({"texts": texts(), "theme": t}, ensure_ascii=False).replace("<", "\\u003c")
    inject = f'<style id="ui-theme">{theme_css(t)}</style>\n<script>window.UI_CUSTOM = {data};</script>\n'
    return html.replace("</head>", inject + "</head>", 1)


# ---- API --------------------------------------------------------------------------------------

@api.get("")
def api_get():
    return {"texts": texts(), "theme": theme()}


class TextBody(BaseModel):
    key: str = Field(max_length=500)
    value: str | None = Field(None, max_length=2000)


@api.put("/text")
def api_text(body: TextBody):
    set_text(body.key, body.value)
    return {"ok": True}


@api.delete("/texts")
def api_reset_texts():
    reset_texts()
    return {"ok": True}


class ThemeBody(BaseModel):
    light: dict[str, str] = Field(default_factory=dict)
    dark: dict[str, str] = Field(default_factory=dict)
    font_size: int | None = None
    zoom: float | None = None


@api.put("/theme")
def api_theme(body: ThemeBody):
    return save_theme(body.model_dump())


@api.delete("/theme")
def api_reset_theme():
    with _lock:
        _write("theme.json", {})
    return {"ok": True}


def index_response() -> HTMLResponse:
    return HTMLResponse(render_index(), headers={"Cache-Control": "no-cache"})
