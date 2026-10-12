"""
Datos de la tabla de dispositivos: lista enriquecida, acciones en bloque y
exportar/importar CSV.

Lo que va al router (nombre, IP fija) se aplica de uno en uno y solo si se pide;
tipo, grupos, notas y "conocido" solo viven en la base de datos del dashboard.
"""

import csv
import io
import ipaddress
import logging
import re
import unicodedata
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import devices, history, network, router

log = logging.getLogger(__name__)
api = APIRouter(prefix="/api/hosts", tags=["dispositivos"])

MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
COLUMNS = ["mac", "nombre", "ip", "ip_fija", "tipo", "grupos", "notas", "conexion", "conocido", "visto_por_primera_vez"]
EDITABLE = ["nombre", "ip_fija", "tipo", "grupos", "notas", "conocido"]
ROUTER_FIELDS = {"nombre", "ip_fija"}
# Cabeceras alternativas aceptadas al importar (inglés o sin guion bajo)
ALIASES = {"name": "nombre", "fixed_ip": "ip_fija", "ipfija": "ip_fija", "ip fija": "ip_fija", "type": "tipo",
           "groups": "grupos", "notes": "notas", "known": "conocido", "interface": "conexion"}
GROUP_SEP = "|"
MAX_CSV = 1_000_000


def enriched_hosts() -> list[dict]:
    """Los hosts del router con lo que añade el dashboard (tipo, notas, grupos, IP fija…)."""
    seen = history.devices()
    pending = {p["uid"]: p["run_at"] for p in history.pending_restores()}
    try:
        fixed = {r["mac"]: r["ip"] for r in network.reservations() if r["enabled"]}
    except Exception as e:  # sin reservas la lista sigue siendo útil
        log.warning("No se pudieron leer las IPs fijas: %s", e)
        fixed = {}
    member_of: dict[str, list[str]] = {}
    for name, macs in history.groups().items():
        for m in macs:
            member_of.setdefault(m, []).append(name)
    out = []
    for h in router.hosts():
        d = seen.get(h["mac"], {})
        out.append({**h, "known": bool(d.get("known")), "first_seen": d.get("first_seen"),
                    "blocked_until": pending.get(h["uid"]),
                    "detected_type": h["type"], "user_type": d.get("type") or "",
                    "type": d.get("type") or h["type"], "notes": d.get("notes") or "",
                    "fixed_ip": fixed.get(h["mac"]), "groups": member_of.get(h["mac"], [])})
    return out


def ip_key(ip: str):
    try:
        return (0, int(ipaddress.ip_address(ip)))
    except ValueError:
        return (1, 0)


def _mac(s: str) -> str:
    return (s or "").strip().lower().replace("-", ":")


# ---- acciones en bloque ---------------------------------------------------------------

class BulkBody(BaseModel):
    macs: list[str] = Field(min_length=1, max_length=500)
    action: Literal["add_group", "remove_group", "set_type", "known", "unknown"]
    value: str = Field("", max_length=40)


def bulk(macs: list[str], action: str, value: str = "") -> str:
    macs = sorted({_mac(m) for m in macs})
    bad = [m for m in macs if not MAC_RE.match(m)]
    if bad:
        raise ValueError(f"MAC no válida: {bad[0]}")
    value = value.strip()
    if action in ("add_group", "remove_group") and not value:
        raise ValueError("Falta el nombre del grupo")
    member_of: dict[str, set[str]] = {}
    for name, members in history.groups().items():
        for m in members:
            member_of.setdefault(m, set()).add(name)
    for mac in macs:
        if action == "add_group":
            history.set_device_groups(mac, sorted(member_of.get(mac, set()) | {value}))
        elif action == "remove_group":
            history.set_device_groups(mac, sorted(member_of.get(mac, set()) - {value}))
        elif action == "set_type":
            history.set_device_meta(mac, type=value)
        else:
            history.set_device_meta(mac)  # crea la fila si no existía
            history.set_known(mac, action == "known")
    what = {"add_group": f"añadidos a {value}", "remove_group": f"quitados de {value}",
            "set_type": f"con tipo {value or 'detectado'}", "known": "marcados como conocidos",
            "unknown": "marcados como no revisados"}[action]
    return f"{len(macs)} dispositivo(s) {what}"


@api.post("/bulk")
def api_bulk(body: BulkBody):
    return {"ok": True, "message": bulk(body.macs, body.action, body.value)}


# ---- exportar ---------------------------------------------------------------------------

def _safe_cell(v) -> str:
    """Evita que Excel interprete como fórmula un nombre que viene de un aparato
    (HostName lo pone el propio dispositivo)."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export_csv(macs: list[str] | None = None) -> str:
    wanted = {_mac(m) for m in macs or [] if m}
    rows = sorted((h for h in enriched_hosts() if not wanted or h["mac"] in wanted), key=lambda h: ip_key(h["ip"]))
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")  # ";" para el Excel en español
    w.writerow(COLUMNS)
    for h in rows:
        first = datetime.fromtimestamp(h["first_seen"]).strftime("%Y-%m-%d %H:%M") if h["first_seen"] else ""
        w.writerow([_safe_cell(v) for v in (
            h["mac"], h["name"], h["ip"], h["fixed_ip"] or "", h["type"], GROUP_SEP.join(h["groups"]),
            h["notes"], h["interface"], "sí" if h["known"] else "no", first)])
    return "﻿" + buf.getvalue()  # BOM: Excel lo abre como UTF-8 (tildes bien)


@api.get("/export.csv")
def api_export(macs: str = ""):
    data = export_csv(macs.split(",") if macs else None)
    name = f"dispositivos-{datetime.now():%Y%m%d-%H%M}.csv"
    return Response(data.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---- importar ---------------------------------------------------------------------------

def _norm_header(h: str) -> str:
    h = unicodedata.normalize("NFD", (h or "").strip().lower())
    h = "".join(c for c in h if unicodedata.category(c) != "Mn")
    return ALIASES.get(h, h.replace(" ", "_"))


def _cell(v) -> str:
    s = (v or "").replace("\r\n", "\n").strip()
    return s[1:] if s[:2] in ("'=", "'+", "'-", "'@") else s


def _bool(v: str) -> bool:
    return _cell(v).lower() in ("si", "sí", "s", "yes", "y", "true", "1", "x")


def _parse(text: str) -> tuple[list[str], list[tuple[int, dict]]]:
    if len(text) > MAX_CSV:
        raise ValueError("El archivo es demasiado grande (máximo 1 MB)")
    text = text.lstrip("﻿")
    first = text.split("\n", 1)[0]
    delim = max(";,\t", key=first.count)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        headers = [_norm_header(h) for h in next(reader)]
    except StopIteration:
        raise ValueError("El archivo está vacío")
    if "mac" not in headers:
        raise ValueError("Falta la columna 'mac' (es la que identifica cada dispositivo)")
    rows = []
    for n, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        rows.append((n, {h: values[i] if i < len(values) else "" for i, h in enumerate(headers)}))
    return [c for c in EDITABLE if c in headers], rows


def _split_groups(s: str) -> list[str]:
    s = _cell(s)
    parts = s.split(GROUP_SEP) if GROUP_SEP in s else s.split(",")
    return sorted({p.strip() for p in parts if p.strip()})


def plan_import(text: str) -> dict:
    """Compara el CSV con lo que hay ahora. Una columna que no está en el CSV no se
    toca; una celda vacía sí borra (nombre por defecto, sin IP fija, sin notas…)."""
    columns, rows = _parse(text)
    hosts = {h["mac"]: h for h in enriched_hosts()}
    meta = history.devices()
    reserved = {r["mac"]: r["ip"] for r in network.reservations() if r["enabled"]}
    member_of: dict[str, list[str]] = {}
    for name, macs in history.groups().items():
        for m in macs:
            member_of.setdefault(m, []).append(name)
    changes, errors, seen = [], [], set()
    for n, row in rows:
        mac = _mac(_cell(row.get("mac")))
        if not MAC_RE.match(mac):
            errors.append(f"Fila {n}: MAC no válida ('{_cell(row.get('mac'))}')")
            continue
        if mac in seen:
            errors.append(f"Fila {n}: la MAC {mac} está repetida; se usa solo la primera")
            continue
        seen.add(mac)
        h, d = hosts.get(mac), meta.get(mac, {})
        fields, skipped = {}, []
        for col in columns:
            raw = row.get(col, "")
            if col == "nombre":
                new, old = _cell(raw), h["name"] if h else ""
                if h is None:
                    if new:
                        skipped.append("nombre (el router no conoce este dispositivo)")
                    continue
                if new == old or (not new and not h["custom_name"]):
                    continue
            elif col == "ip_fija":
                new, old = _cell(raw), reserved.get(mac, "")
            elif col == "tipo":
                new = _cell(raw)
                old = h["type"] if h else d.get("type") or ""
                if new == old:
                    continue
            elif col == "grupos":
                new, old = _split_groups(raw), sorted(member_of.get(mac, []))
            elif col == "notas":
                new, old = _cell(raw), d.get("notes") or ""
            else:  # conocido
                new, old = _bool(raw), bool(d.get("known"))
            if new != old:
                fields[col] = [old, new]
        if fields or skipped:
            changes.append({"mac": mac, "name": h["name"] if h else _cell(row.get("nombre")) or mac,
                            "in_router": h is not None, "fields": fields, "skipped": skipped})
    return {
        "rows": len(rows), "columns": columns, "changes": [c for c in changes if c["fields"]],
        "skipped": [f"{c['name']}: {s}" for c in changes for s in c["skipped"]],
        "router_changes": sum(1 for c in changes for f in c["fields"] if f in ROUTER_FIELDS),
        "errors": errors,
    }


def apply_import(text: str, include_router: bool) -> dict:
    plan = plan_import(text)
    uids = {h["mac"]: h["uid"] for h in router.hosts()}
    applied, errors = 0, list(plan["errors"])
    for c in plan["changes"]:
        mac = c["mac"]
        for col, (_, new) in c["fields"].items():
            if col in ROUTER_FIELDS and not include_router:
                continue
            try:
                if col == "nombre":
                    devices.rename(uids[mac], new, mac)
                elif col == "ip_fija":
                    network.set_fixed_ip(mac, new or None)
                elif col == "tipo":
                    history.set_device_meta(mac, type=new)
                elif col == "notas":
                    history.set_device_meta(mac, notes=new)
                elif col == "grupos":
                    history.set_device_groups(mac, new)
                elif col == "conocido":
                    history.set_device_meta(mac)
                    history.set_known(mac, new)
                applied += 1
            except Exception as e:  # un fallo en una fila no para el resto
                errors.append(f"{c['name']} ({col}): {e}")
    history.add_event("manual", f"Importados {applied} cambios de dispositivos desde CSV"
                                + (f" ({len(errors)} con error)" if errors else ""))
    return {"ok": True, "applied": applied, "errors": errors}


class ImportBody(BaseModel):
    csv: str = Field(max_length=MAX_CSV)
    include_router: bool = False


@api.post("/import/preview")
def api_import_preview(body: ImportBody):
    return plan_import(body.csv)


@api.post("/import/apply")
def api_import_apply(body: ImportBody):
    return apply_import(body.csv, body.include_router)
