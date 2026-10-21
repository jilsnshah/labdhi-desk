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
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("LABDHI_DB", os.path.join(ROOT, "data", "labdhi.db"))
SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = bool(DATABASE_URL)

SCHEMA_VERSION = "2"

_local = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "x"


def clean(text) -> str:
    """Collapse whitespace - the one normalisation every name goes through."""
    return re.sub(r"\s+", " ", (text or "").strip())


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


def statements(sql: str) -> List[str]:
    """Split a script into statements, comments removed.

    sqlite3's executescript() commits whatever transaction is open before it
    runs, so a migration that must be all-or-nothing runs its DDL one
    statement at a time instead.
    """
    body = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in body.split(";")
            if s.strip() and not s.strip().upper().startswith("PRAGMA")]


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
        self.used = time.monotonic()        # when the server last answered

    def execute(self, sql: str, args: Iterable = ()):
        if self.is_pg:
            cur = self.raw.cursor()
            cur.execute(to_pg(sql), tuple(args))
            self.used = time.monotonic()
            return cur
        return self.raw.execute(sql, tuple(args))

    def insert(self, sql: str, args: Iterable = ()) -> int:
        """Run an INSERT and hand back the new row's id."""
        if self.is_pg:
            cur = self.raw.cursor()
            cur.execute(to_pg(sql) + " RETURNING id", tuple(args))
            self.used = time.monotonic()
            row = cur.fetchone()
            return int(row["id"] if isinstance(row, dict) else row[0])
        return int(self.raw.execute(sql, tuple(args)).lastrowid)

    def script(self, sql: str) -> None:
        """Run a schema script statement by statement, inside any open transaction."""
        for stmt in statements(schema_for_pg(sql) if self.is_pg else sql):
            self.execute(stmt)


# ------------------------------------------------------------------ liveness
# A desk sleeps between trades, and a hosted one sleeps hard: Cloud Run freezes
# the container the moment a request is answered, and Neon closes a client that
# has gone quiet - then suspends its compute altogether. The Postgres socket is
# therefore usually dead by the time the next sauda is booked, and nothing has
# told this process. psycopg only finds out when a query fails, which used to
# surface as a 500 on a perfectly good request.
#
# So the cached handle is treated as provisional: proved before it is handed
# out after a quiet spell, and replaced rather than raised through.
IDLE_PROVE = 20.0       # seconds of quiet after which a handle must prove itself


def _lost(exc: BaseException) -> bool:
    """Did the connection die, as against the query being wrong?"""
    if not IS_PG:
        return False
    import psycopg
    return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))


def _alive(conn: Conn, prove: bool = False) -> bool:
    raw = conn.raw
    if raw.closed or getattr(raw, "broken", False):
        return False
    if not prove and time.monotonic() - conn.used < IDLE_PROVE:
        return True                         # it answered a moment ago
    try:
        raw.cursor().execute("SELECT 1")
    except Exception:
        return False
    conn.used = time.monotonic()
    return True


def _drop() -> None:
    """Throw this thread's handle away. The next connect() opens a fresh one."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.raw.close()
        except Exception:
            pass
    _local.__dict__.pop("conn", None)


def connect(prove: bool = False) -> Conn:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        if not conn.is_pg or _alive(conn, prove):
            return conn
        _drop()

    if IS_PG:
        import psycopg
        raw = psycopg.connect(
            DATABASE_URL, autocommit=True, row_factory=_int_rows,
            # Fail fast rather than hang a request for the platform's timeout.
            connect_timeout=10,
            # Keepalives notice a socket the far end has dropped. On a host
            # that freezes the CPU between requests they cannot fire, which is
            # why the check above exists as well.
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
        )
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


# ------------------------------------------------------------------ introspection
def has_table(conn, table: str) -> bool:
    if conn.is_pg:
        return bool(scalar("SELECT COUNT(*) FROM information_schema.tables "
                           "WHERE table_schema = current_schema() AND table_name = ?", (table,)))
    return bool(scalar("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def has_column(conn, table: str, column: str) -> bool:
    if conn.is_pg:
        return bool(scalar(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ? AND column_name = ?",
            (table, column)))
    return any(r["name"] == column for r in conn.raw.execute("PRAGMA table_info(%s)" % table))


def sync_sequence(conn, table: str) -> None:
    """After rows are copied in with their ids, Postgres' counter must move past them."""
    if conn.is_pg:
        conn.execute("SELECT setval(pg_get_serial_sequence('%s','id'), "
                     "COALESCE((SELECT MAX(id) FROM %s), 0) + 1, false)" % (table, table))


# ------------------------------------------------------------------ boot
def init_db() -> None:
    conn = connect()
    from .migrate import add_columns, upgrade
    upgrade(conn)                              # a v1 book is rebuilt first, if there is one
    add_columns(conn)                          # then any column added since it was last opened
    with open(SCHEMA) as fh:
        schema = fh.read()
    with tx() as c:
        c.script(schema)
        seed_reference(c)
        defaults = {
            "company_name": "Labdhi Exim",
            "sauda_prefix": "LE",       # LE/26-27/0001
            "alloc_policy": "fifo",
            "allow_short_sales": "0",   # a short must be an explicit choice, never a default
            "unit": "kg",
            "schema_version": SCHEMA_VERSION,
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))


def seed_reference(conn) -> None:
    from .gst import STATES
    for code, name in STATES.items():
        conn.execute("INSERT OR IGNORE INTO states(code,name) VALUES (?,?)", (code, name))


# Everything, children first. Legacy names are listed so a reset also clears a
# half-migrated book.
TABLES = ["stock_moves", "allocations", "lots", "marks", "deals", "events",
          "products", "manufacturers", "grades", "materials", "parties", "states",
          "warehouses", "settings",
          "skus", "catalog_makers", "catalog_grades", "catalog_materials"]


def reset() -> None:
    """Wipe the book. Used by the seed script and the tests."""
    if IS_PG:
        conn = connect()
        conn.execute("DROP VIEW IF EXISTS v_products")
        for table in TABLES:
            conn.execute("DROP TABLE IF EXISTS %s CASCADE" % table)
        conn.raw.close()
        _local.__dict__.clear()
        return
    close()
    for suffix in ("", "-wal", "-shm"):
        path = DB_PATH + suffix
        if os.path.exists(path):
            os.remove(path)


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.raw.close()
        except Exception:
            pass
    _local.__dict__.clear()


@contextmanager
def tx():
    """One atomic unit of work. Any exception rolls the whole thing back.

    Nested use joins the outer transaction instead of committing half of it.
    """
    # A write cannot be replayed once it has begun, so the handle is proved
    # first. One extra round trip, on the rare request that changes the book.
    conn = connect(prove=not getattr(_local, "depth", 0))
    if getattr(_local, "depth", 0):
        _local.depth += 1
        try:
            yield conn
        finally:
            _local.depth -= 1
        return
    _local.depth = 1
    try:
        if conn.is_pg:
            with conn.raw.transaction():
                yield conn
            return
        conn.raw.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.raw.execute("COMMIT")
        except BaseException:
            conn.raw.execute("ROLLBACK")
            raise
    finally:
        _local.depth = 0


# ------------------------------------------------------------------ helpers
def _read(run):
    """Run a read, once more on a fresh connection if the old one had died.

    Only outside a transaction: inside one, the statements already run would be
    silently lost on a retry, so the error goes up and the whole unit of work
    rolls back.
    """
    try:
        return run(connect())
    except Exception as exc:
        if getattr(_local, "depth", 0) or not _lost(exc):
            raise
        _drop()
        return run(connect())


def q(sql: str, args: Iterable = ()) -> List[Any]:
    return _read(lambda c: c.execute(sql, args).fetchall())


def q1(sql: str, args: Iterable = ()) -> Optional[Any]:
    return _read(lambda c: c.execute(sql, args).fetchone())


def scalar(sql: str, args: Iterable = (), default=0):
    row = q1(sql, args)
    if row is None:
        return default
    value = next(iter(row.values())) if isinstance(row, dict) else row[0]
    return default if value is None else value


def row_to_dict(row) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def dicts(rows) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def like(term: Optional[str]) -> Optional[str]:
    term = (term or "").strip().lower()
    return "%%%s%%" % term if term else None


# ------------------------------------------------------------------ paging
# Every list in the system is served the same way: ?q=&limit=&offset= plus its
# own filters, answered with one page and the size of the whole match. The
# screens never hold a full table, so a list can grow without limit.
DEFAULT_PAGE = 25
MAX_PAGE = 200


def page_args(limit: Optional[int], offset: Optional[int], default: int = DEFAULT_PAGE):
    limit = default if limit is None else max(1, min(MAX_PAGE, int(limit)))
    return limit, max(0, int(offset or 0))


def page(items: List[Dict[str, Any]], total: int, limit: int, offset: int) -> Dict[str, Any]:
    return {"items": items, "total": int(total), "limit": limit, "offset": offset,
            "has_more": offset + len(items) < int(total)}


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
