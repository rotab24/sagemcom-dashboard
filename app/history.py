"""Histórico en SQLite: muestras periódicas, dispositivos vistos y eventos."""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import settings

_lock = threading.Lock()

# Funciones a las que se avisa de cada evento nuevo (p. ej. las notificaciones al móvil)
listeners: list = []

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts            REAL PRIMARY KEY,
    bytes_rx      REAL,
    bytes_tx      REAL,
    rx_dbm        REAL,
    tx_dbm        REAL,
    temperature   REAL,
    hosts_active  INTEGER
);
CREATE TABLE IF NOT EXISTS devices (
    mac         TEXT PRIMARY KEY,
    name        TEXT,
    first_seen  REAL,
    last_seen   REAL,
    known       INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pending_restores (
    uid      INTEGER PRIMARY KEY,
    run_at   REAL,
    name     TEXT,
    grid     TEXT
);
CREATE TABLE IF NOT EXISTS wifi_schedules (
    alias    TEXT PRIMARY KEY,
    enabled  INTEGER,
    grid     TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key      TEXT PRIMARY KEY,
    value    TEXT
);
CREATE TABLE IF NOT EXISTS groups (
    name     TEXT PRIMARY KEY,
    macs     TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL,
    kind     TEXT,
    message  TEXT
);
"""


@contextmanager
def _db():
    with _lock:
        conn = sqlite3.connect(settings.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init():
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with _db() as db:
        db.executescript(SCHEMA)
        # Columnas añadidas después de la primera versión
        cols = {r["name"] for r in db.execute("PRAGMA table_info(devices)")}
        for col in ("type", "notes"):
            if col not in cols:
                db.execute(f"ALTER TABLE devices ADD COLUMN {col} TEXT")


def record_sample(wan: dict, gpon: dict, hosts_active: int, ts: float | None = None):
    with _db() as db:
        db.execute(
            "INSERT OR REPLACE INTO samples VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts or time.time(), wan.get("bytes_rx"), wan.get("bytes_tx"),
             gpon.get("rx_dbm"), gpon.get("tx_dbm"), gpon.get("temperature"), hosts_active),
        )


def samples(since_seconds: float = 86400) -> list[dict]:
    """Muestras de las últimas `since_seconds`, con el caudal (bit/s) calculado
    entre muestras consecutivas."""
    with _db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM samples WHERE ts >= ? ORDER BY ts", (time.time() - since_seconds,))]
    prev = None
    for r in rows:
        r["rate_rx_bps"] = r["rate_tx_bps"] = None
        if prev and r["ts"] > prev["ts"]:
            dt = r["ts"] - prev["ts"]
            for d in ("rx", "tx"):
                a, b = prev[f"bytes_{d}"], r[f"bytes_{d}"]
                # Si el contador baja (reinicio del router) no hay caudal válido
                if a is not None and b is not None and b >= a:
                    r[f"rate_{d}_bps"] = (b - a) * 8 / dt
        prev = r
    return rows


def update_devices(hosts: list[dict]) -> list[dict]:
    """Registra los dispositivos activos y devuelve los que no se habían visto nunca."""
    now = time.time()
    new = []
    with _db() as db:
        for h in hosts:
            if not h["mac"] or not h["active"]:
                continue
            cur = db.execute("UPDATE devices SET last_seen = ?, name = ? WHERE mac = ?",
                             (now, h["name"], h["mac"]))
            if cur.rowcount == 0:
                db.execute("INSERT INTO devices (mac, name, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                           (h["mac"], h["name"], now, now))
                new.append(h)
    return new


def devices() -> dict[str, dict]:
    with _db() as db:
        return {r["mac"]: dict(r) for r in db.execute("SELECT * FROM devices")}


def set_known(mac: str, known: bool):
    with _db() as db:
        db.execute("UPDATE devices SET known = ? WHERE mac = ?", (int(known), mac.lower()))


def set_device_meta(mac: str, type: str | None = None, notes: str | None = None):
    """Tipo y notas que pone el usuario (solo en el dashboard; el router no los guarda)."""
    mac, now = mac.lower(), time.time()
    with _db() as db:
        db.execute("INSERT OR IGNORE INTO devices (mac, first_seen, last_seen) VALUES (?, ?, ?)", (mac, now, now))
        if type is not None:
            db.execute("UPDATE devices SET type = ? WHERE mac = ?", (type.strip() or None, mac))
        if notes is not None:
            db.execute("UPDATE devices SET notes = ? WHERE mac = ?", (notes.strip() or None, mac))


def set_device_groups(mac: str, names: list[str]):
    """Deja al dispositivo exactamente en esos grupos (crea los que no existan y
    borra los que se queden vacíos)."""
    mac = mac.lower()
    wanted = {n.strip() for n in names if n.strip()}
    with _db() as db:
        current = {r["name"]: json.loads(r["macs"]) for r in db.execute("SELECT * FROM groups")}
        for name in wanted | set(current):
            macs = set(current.get(name, []))
            if name in wanted:
                macs.add(mac)
            else:
                macs.discard(mac)
            if macs:
                db.execute("INSERT OR REPLACE INTO groups VALUES (?, ?)", (name, json.dumps(sorted(macs))))
            else:
                db.execute("DELETE FROM groups WHERE name = ?", (name,))


def is_empty_devices() -> bool:
    with _db() as db:
        return db.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 0


def add_event(kind: str, message: str, **data):
    """Guarda el evento y avisa a los oyentes (avisos al móvil). `data` lleva los
    datos sueltos (mac, ip, duración…) para las plantillas de los avisos."""
    with _db() as db:
        db.execute("INSERT INTO events (ts, kind, message) VALUES (?, ?, ?)", (time.time(), kind, message))
    for fn in list(listeners):
        try:
            fn(kind, message, data)
        except Exception:  # un aviso fallido nunca debe romper la acción que lo generó
            pass


def events(limit: int = 50, since: float | None = None, kinds: list[str] | None = None) -> list[dict]:
    sql, args = "SELECT * FROM events WHERE 1=1", []
    if since is not None:
        sql += " AND ts >= ?"
        args.append(since)
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += kinds
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _db() as db:
        return [dict(r) for r in db.execute(sql, args)]


def get_setting(key: str, default=None):
    with _db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value):
    with _db() as db:
        if value is None:
            db.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))


def groups() -> dict[str, list[str]]:
    with _db() as db:
        return {r["name"]: json.loads(r["macs"]) for r in db.execute("SELECT * FROM groups ORDER BY name")}


def save_group(name: str, macs: list[str]):
    with _db() as db:
        db.execute("INSERT OR REPLACE INTO groups VALUES (?, ?)", (name, json.dumps(macs)))


def delete_group(name: str):
    with _db() as db:
        db.execute("DELETE FROM groups WHERE name = ?", (name,))


def save_pending_restore(uid: int, run_at: float, name: str, grid: dict):
    with _db() as db:
        db.execute("INSERT OR REPLACE INTO pending_restores VALUES (?, ?, ?, ?)",
                   (uid, run_at, name, json.dumps(grid)))


def pop_pending_restore(uid: int):
    with _db() as db:
        db.execute("DELETE FROM pending_restores WHERE uid = ?", (uid,))


def pending_restores() -> list[dict]:
    with _db() as db:
        return [{**dict(r), "grid": json.loads(r["grid"])} for r in db.execute("SELECT * FROM pending_restores")]


def save_wifi_schedule(alias: str, enabled: bool, grid: dict):
    with _db() as db:
        db.execute("INSERT OR REPLACE INTO wifi_schedules VALUES (?, ?, ?)", (alias, int(enabled), json.dumps(grid)))


def wifi_schedules() -> list[tuple[str, bool, dict]]:
    with _db() as db:
        return [(r["alias"], bool(r["enabled"]), json.loads(r["grid"])) for r in db.execute("SELECT * FROM wifi_schedules")]


def prune(keep_days: int = 30):
    with _db() as db:
        cutoff = time.time() - keep_days * 86400
        db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
