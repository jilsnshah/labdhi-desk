"""SQLite access. Explicit SQL on purpose - this is money, not a CRUD app."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("LABDHI_DB", os.path.join(ROOT, "data", "labdhi.db"))
SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

_local = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "x"


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=8000")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = connect()
    with open(SCHEMA) as fh:
        conn.executescript(fh.read())
    defaults = {
        "company_name": "Labdhi Exim",
        "alloc_policy": "fifo",
        "allow_short_sales": "0",   # a short must be an explicit choice, never a default
        "unit": "kg",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))


@contextmanager
def tx():
    """One atomic unit of work. Any exception rolls the whole thing back."""
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ------------------------------------------------------------------ helpers
def q(sql: str, args: Iterable = ()) -> List[sqlite3.Row]:
    return connect().execute(sql, tuple(args)).fetchall()


def q1(sql: str, args: Iterable = ()) -> Optional[sqlite3.Row]:
    return connect().execute(sql, tuple(args)).fetchone()


def scalar(sql: str, args: Iterable = (), default=0):
    row = q1(sql, args)
    if row is None or row[0] is None:
        return default
    return row[0]


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def settings() -> Dict[str, str]:
    return {r["key"]: r["value"] for r in q("SELECT key,value FROM settings")}


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


# ------------------------------------------------------------------ audit
def log(conn, entity: str, entity_id: Optional[int], action: str, summary: str,
        payload: Optional[dict] = None, undoable: bool = False, actor: str = "trader") -> int:
    cur = conn.execute(
        "INSERT INTO events(ts,actor,entity,entity_id,action,summary,payload,undoable) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (now(), actor, entity, entity_id, action, summary,
         json.dumps(payload or {}, default=str), 1 if undoable else 0),
    )
    return int(cur.lastrowid)
