"""Database access.

Two backends, one dialect. Locally the book lives in a SQLite file, which needs
no service and makes the test suite instant. In production it lives in Postgres,
because a hosted disk that is wiped on every deploy is not a place to keep a
trading book.

Every query in this codebase is written once, in SQLite's dialect with `?`
placeholders, and translated on the way out when the target is Postgres. That
keeps one source of truth for the SQL instead of two drifting copies.
"""
from __future__ import annotations

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

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = bool(DATABASE_URL)

_local = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "x"


# ------------------------------------------------------------------ dialect
def to_pg(sql: str) -> str:
    """SQLite text -> Postgres text.

    Only two constructs differ across the whole codebase: the placeholder
    style, and SQLite's `INSERT OR IGNORE`. No `?` ever appears inside a string
    literal here, so a straight replacement is safe.
    """
    if "INSERT OR IGNORE" in sql:
        sql = sql.replace("INSERT OR IGNORE", "INSERT") + " ON CONFLICT DO NOTHING"
    return sql.replace("?", "%s")


def schema_for_pg(sql: str) -> str:
    """SQLite's INTEGER is whatever it needs to be; Postgres' is 32-bit.

    Quantities are grams and rates are paise, so 48 MT at Rs 103 is already
    ~5e11 once multiplied - far past a 32-bit column. SQLite widened silently,
    Postgres raises. Everything numeric therefore becomes BIGINT.
    """
    out = []
    for line in sql.splitlines():
        if line.strip().upper().startswith("PRAGMA"):
            continue
        line = line.replace("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY")
        line = re.sub(r"\bINTEGER\b(?! PRIMARY KEY)", "BIGINT", line)
        out.append(line)
    return "\n".join(out)


def _int_rows(cursor):
    """Postgres returns SUM(bigint) as numeric, which arrives as Decimal.

    Every number in this system is an exact integer - grams, paise - so a
    Decimal leaking through would break arithmetic downstream and fail JSON
    encoding at the edge. They are converted back here, once, at the boundary.
    """
    from decimal import Decimal
    from psycopg.rows import dict_row
    base = dict_row(cursor)

    def make(values):
        row = base(values)
        for key, value in row.items():
            if isinstance(value, Decimal):
                row[key] = int(value) if value == value.to_integral_value() else float(value)
        return row
    return make


class Conn:
    """A thin, uniform handle over either driver."""

    def __init__(self, raw, is_pg: bool):
        self.raw = raw
        self.is_pg = is_pg

    def execute(self, sql: str, args: Iterable = ()):
        if self.is_pg:
            cur = self.raw.cursor()
            cur.execute(to_pg(sql), tuple(args))
            return cur
        return self.raw.execute(sql, tuple(args))

    def insert(self, sql: str, args: Iterable = ()) -> int:
        """Run an INSERT and hand back the new row's id."""
        if self.is_pg:
            cur = self.raw.cursor()
            cur.execute(to_pg(sql) + " RETURNING id", tuple(args))
            row = cur.fetchone()
            return int(row["id"] if isinstance(row, dict) else row[0])
        return int(self.raw.execute(sql, tuple(args)).lastrowid)

    def executescript(self, sql: str) -> None:
        if self.is_pg:
            self.raw.execute(schema_for_pg(sql))
        else:
            self.raw.executescript(sql)


def connect() -> Conn:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn

    if IS_PG:
        import psycopg
        raw = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=_int_rows)
    else:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        raw = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys=ON")
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA busy_timeout=8000")

    conn = Conn(raw, IS_PG)
    _local.conn = conn
    return conn


# Columns added after the first production deploy. CREATE TABLE IF NOT EXISTS
# never alters a table that already exists, so a live database would silently
# lack them; these are added in place, once, on boot.
MIGRATIONS = [
    ("deals", "warehouse", "TEXT"),
    ("deals", "payment_due", "TEXT"),
    ("deals", "ex_place", "TEXT"),
    ("lots", "warehouse", "TEXT"),
    ("parties", "address", "TEXT"),
    ("parties", "gstin", "TEXT"),
    ("parties", "pan", "TEXT"),
    ("warehouses", "location", "TEXT"),
]


def _has_column(conn, table: str, column: str) -> bool:
    if conn.is_pg:
        return bool(scalar(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?", (table, column)))
    return any(r["name"] == column for r in conn.raw.execute("PRAGMA table_info(%s)" % table))


def migrate(conn) -> None:
    added = set()
    for table, column, kind in MIGRATIONS:
        if not _has_column(conn, table, column):
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, kind))
            added.add((table, column))
    # Parties used to carry only a city. Carry it into the new address field the
    # one time that field appears, so nobody's details vanish from the screen -
    # and never again after that, so a deliberately cleared address stays clear.
    if ("parties", "address") in added:
        conn.execute("UPDATE parties SET address = city "
                     "WHERE address IS NULL AND city IS NOT NULL AND city != ''")
    # A party is unique by GSTIN. The index lives here, not in schema.sql: on an
    # older database schema.sql runs before the column exists, and an index on a
    # missing column would abort the whole boot. NULLs do not collide, so parties
    # with no GSTIN are unaffected.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_parties_gstin ON parties(gstin)")


def init_db() -> None:
    conn = connect()
    with open(SCHEMA) as fh:
        conn.executescript(fh.read())
    migrate(conn)
    defaults = {
        "company_name": "Labdhi Exim",
        "sauda_prefix": "LE",       # LE/26-27/0001
        "alloc_policy": "fifo",
        "allow_short_sales": "0",   # a short must be an explicit choice, never a default
        "unit": "kg",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))


TABLES = ["allocations", "lots", "deals", "marks", "events", "skus", "parties",
          "catalog_makers", "catalog_grades", "catalog_materials", "warehouses", "settings"]


def reset() -> None:
    """Wipe the book. Used only by the seed script."""
    if IS_PG:
        conn = connect()
        for table in TABLES:
            conn.execute("DROP TABLE IF EXISTS %s CASCADE" % table)
        conn.raw.close()
        _local.__dict__.clear()
        return
    for suffix in ("", "-wal", "-shm"):
        path = DB_PATH + suffix
        if os.path.exists(path):
            os.remove(path)
    _local.__dict__.clear()


@contextmanager
def tx():
    """One atomic unit of work. Any exception rolls the whole thing back."""
    conn = connect()
    if conn.is_pg:
        with conn.raw.transaction():
            yield conn
        return
    conn.raw.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.raw.execute("COMMIT")
    except Exception:
        conn.raw.execute("ROLLBACK")
        raise


# ------------------------------------------------------------------ helpers
def q(sql: str, args: Iterable = ()) -> List[Any]:
    return connect().execute(sql, args).fetchall()


def q1(sql: str, args: Iterable = ()) -> Optional[Any]:
    return connect().execute(sql, args).fetchone()


def scalar(sql: str, args: Iterable = (), default=0):
    row = q1(sql, args)
    if row is None:
        return default
    value = next(iter(row.values())) if isinstance(row, dict) else row[0]
    return default if value is None else value


def row_to_dict(row) -> Optional[Dict[str, Any]]:
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
    import json
    return conn.insert(
        "INSERT INTO events(ts,actor,entity,entity_id,action,summary,payload,undoable) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (now(), actor, entity, entity_id, action, summary,
         json.dumps(payload or {}, default=str), 1 if undoable else 0),
    )
