"""
pgcompat.py — SQLite → PostgreSQL compatibility shim for The Hive bots.

Drop-in replacement for sqlite3.  Change two lines in each bot file:

    OLD:  import sqlite3
          conn = sqlite3.connect("writers.db", check_same_thread=False)

    NEW:  import pgcompat as sqlite3
          conn = sqlite3.connect()

Everything else (c.execute, conn.commit, c.fetchone, c.lastrowid …)
works the same.  All data goes to Neon PostgreSQL (DATABASE_URL).
Nothing is stored on the local filesystem anymore.

Compatibility handled automatically:
  ✓  ? placeholders → %s
  ✓  INSERT OR REPLACE → ON CONFLICT (pk) DO UPDATE SET …
  ✓  INSERT OR IGNORE  → ON CONFLICT DO NOTHING
  ✓  INTEGER PRIMARY KEY AUTOINCREMENT → BIGSERIAL PRIMARY KEY
  ✓  INTEGER columns → BIGINT (covers Discord 64-bit snowflake IDs)
  ✓  REAL → DOUBLE PRECISION
  ✓  BLOB → BYTEA
  ✓  PRAGMA table_info(t) → information_schema query
  ✓  executescript(sql) → split on ; and execute each statement
  ✓  lastrowid via RETURNING id  (for auto-increment tables)
  ✓  content_pool.created → content_pool.created_at
  ✓  DictCursor (rows accessible by index AND column name)
  ✓  Thread-safe connection pool
  ✓  Discord snowflake coercion — large int params auto-cast to str
      so TEXT-typed ID columns (guild_id, discord_id, …) never raise
      "unsupported comparison operator: <string> = <int>" on CockroachDB
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3 as _stdlib_sqlite3
import threading
from typing import Any, Iterator, Optional, Union

log = logging.getLogger("pgcompat")

_DATABASE_URL: str = (
    os.environ.get("COCKROACH_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or os.environ.get("NEON_DATABASE_URL")
    or ""
).strip()

_USE_POSTGRES = bool(_DATABASE_URL)

if not _USE_POSTGRES:
    log.warning(
        "pgcompat: no Postgres URL set — using local SQLite (%s). "
        "Set NEON_DATABASE_URL or DATABASE_URL on Render for production.",
        os.environ.get("NISHIBEE_DB", "writers.db"),
    )

# ─── Connection pool (created lazily, Postgres only) ─────────────────────────
# psycopg2 is imported lazily inside _get_pool() so that environments without
# psycopg2 installed (local test runners, CI) can still import pgcompat and use
# the SQLite fallback path without an ImportError at module load time.
_pool: Optional[Any] = None
_pool_lock = threading.Lock()


def _get_pool() -> Any:
    global _pool
    if not _USE_POSTGRES:
        raise RuntimeError("pgcompat: Postgres pool requested but no DATABASE_URL")
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                import psycopg2
                import psycopg2.extras
                import psycopg2.pool
                _sslmode = os.environ.get("PGCOMPAT_SSLMODE", "prefer")
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    1,
                    32,
                    _DATABASE_URL,
                    cursor_factory=psycopg2.extras.DictCursor,
                    sslmode=_sslmode,
                )
    return _pool


def close_pool() -> None:
    """Close the Postgres connection pool and reset the module global to None.

    Safe to call at any time — including when no pool exists yet, when Postgres
    is not configured, or when called multiple times (e.g. test tear-down).

    Callers: main.py SIGTERM handler, test fixtures.
    """
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None


# ─── Primary-key registry ─────────────────────────────────────────────────────
# Used by INSERT OR REPLACE → ON CONFLICT (pk_cols) DO UPDATE SET …
_PK: dict[str, list[str]] = {
    "writers":              ["discord_id"],
    "economy":              ["discord_id", "guild_id"],
    "birthdays":            ["discord_id", "guild_id"],
    "now_playing":          ["discord_id", "guild_id"],
    "buddy_optins":         ["discord_id", "guild_id"],
    "daily_state":          ["guild_id", "kind"],
    "quest_progress":       ["discord_id", "guild_id", "flag"],
    "bingo_cards":          ["discord_id", "guild_id", "month"],
    "streaks":              ["discord_id"],
    "challenge_entries":    ["challenge_id", "discord_id"],
    "writer_intro_state":   ["channel_id"],
    "member_intros":        ["user_id", "guild_id", "kind"],
    "guide_reposts":        ["guild_id", "channel_name"],
    "announced_best":       ["guild_id", "kind", "period", "period_key"],
    "member_badges":        ["discord_id", "guild_id", "badge_id"],
    "ticket_submissions":   ["ticket_id", "discord_id"],
    "ticket_reminders":     ["ticket_id", "discord_id"],
    "forcepost_rejections": ["discord_id", "guild_id", "week_key"],
    "double_honey_days":    ["discord_id", "guild_id"],
    "slacker_state":        ["discord_id", "guild_id"],
    "member_word_counts":   ["discord_id", "guild_id"],
    "rank_check_requests":  ["discord_id"],
    "wn_writers":           ["profile_id"],
    "wn_books":             ["book_id", "profile_id"],
    "concierge_shop_extras":["item_key"],
    "chapter_posts":        ["message_id"],
    "hive_babysit_pending": ["user_id", "guild_id"],
    "badges":               ["discord_id", "guild_id", "badge_id"],
    "tickets":              ["id"],
    "content_pool":         ["id"],
}

# Tables whose 'id' column is BIGSERIAL — INSERT needs RETURNING id for lastrowid
_SERIAL_TABLES: frozenset[str] = frozenset({
    "books", "recommendations", "lfg", "lore", "story", "confessions",
    "buddy_pairs", "content_pool", "challenges", "snippets", "beta_board",
    "chapter_posts", "member_intros", "blessed_tickets", "match_duels",
    "mystery_quests", "shop_purchases", "member_word_counts", "rank_audit",
    "concierge_audit", "hive_lessons", "hive_memory", "hive_api_keys",
    "tickets", "content_pool",
})

# ─── Discord snowflake coercion ───────────────────────────────────────────────
# Discord IDs (snowflakes) are 64-bit integers starting around 10^17.
# Some databases (CockroachDB in particular) store these as TEXT/STRING columns
# due to earlier migrations.  Comparing a STRING column against a Python int
# raises "unsupported comparison operator: <string> = <int>".
#
# Fix: when running against Postgres, automatically convert any Python int that
# is larger than 2**40 (~1 trillion) to its string representation.  This covers
# all Discord snowflakes while leaving small game values (honey, words, XP, …)
# unchanged.  CockroachDB and PostgreSQL both accept a string literal where an
# INT column is expected (they coerce silently), so this is safe in both
# directions (TEXT column → no error; INT/BIGINT column → coerced cleanly).
_DISCORD_SNOWFLAKE_THRESHOLD = 2 ** 40  # ~1 trillion; all Discord IDs are larger


def _coerce_params(params: Any) -> Any:
    """Convert large-int Discord snowflakes to strings in a params tuple/list.

    Only active when using Postgres (no-op for SQLite fallback).
    Handles None params, tuples, lists, and single scalar values.
    """
    if not _USE_POSTGRES or params is None:
        return params

    def _fix(v: Any) -> Any:
        if isinstance(v, int) and not isinstance(v, bool) and v > _DISCORD_SNOWFLAKE_THRESHOLD:
            return str(v)
        return v

    if isinstance(params, (tuple, list)):
        coerced = [_fix(v) for v in params]
        return type(params)(coerced)
    # single scalar (rare but handle it)
    return _fix(params)


# ─── SQL adaptation ───────────────────────────────────────────────────────────

def _adapt_create_table(sql: str) -> str:
    # 1. INTEGER PRIMARY KEY AUTOINCREMENT → BIGSERIAL PRIMARY KEY
    sql = re.sub(
        r'\b(\w+)\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b',
        r'\1 BIGSERIAL PRIMARY KEY',
        sql, flags=re.IGNORECASE,
    )
    # 2. Any remaining AUTOINCREMENT keyword (safety net)
    sql = re.sub(r'\bAUTOINCREMENT\b', '', sql, flags=re.IGNORECASE)
    # 3. Remaining INTEGER columns → BIGINT so Discord snowflake IDs fit and
    #    match any existing BIGINT/TEXT columns created by earlier migrations.
    #    Skips the already-converted BIGSERIAL keyword.
    sql = re.sub(r'\bINTEGER\b', 'BIGINT', sql, flags=re.IGNORECASE)
    # 4. SQLite-only types
    sql = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bBLOB\b', 'BYTEA', sql, flags=re.IGNORECASE)
    return sql


def _conflict_clause(table: str, cols: list[str]) -> str:
    pk = _PK.get(table, [])
    non_pk = [c for c in cols if c not in pk]
    if pk and non_pk:
        return (
            f"ON CONFLICT ({', '.join(pk)}) "
            f"DO UPDATE SET {', '.join(f'{c}=EXCLUDED.{c}' for c in non_pk)}"
        )
    return "ON CONFLICT DO NOTHING"


def adapt(sql: str) -> tuple[str, bool]:
    """
    Convert SQLite SQL to PostgreSQL SQL.
    Returns (pg_sql, needs_returning_id).
    """
    stripped = sql.strip()

    # PRAGMA table_info(tbl) → information_schema
    pm = re.match(r'PRAGMA\s+table_info\((\w+)\)\s*$', stripped, re.IGNORECASE)
    if pm:
        tbl = pm.group(1)
        return (
            "SELECT ordinal_position-1 AS cid, column_name AS name, "
            "data_type AS type, "
            "CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END AS notnull, "
            "column_default AS dflt_value, 0 AS pk "
            "FROM information_schema.columns "
            f"WHERE table_name='{tbl}' AND table_schema='public' "
            "ORDER BY ordinal_position",
            False,
        )

    is_ignore  = bool(re.search(r'\bINSERT\s+OR\s+IGNORE\b',  stripped, re.IGNORECASE))
    is_replace = bool(re.search(r'\bINSERT\s+OR\s+REPLACE\b', stripped, re.IGNORECASE))

    sql = re.sub(r'\bINSERT\s+OR\s+IGNORE\s+INTO\b',  'INSERT INTO', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bINSERT\s+OR\s+REPLACE\s+INTO\b', 'INSERT INTO', sql, flags=re.IGNORECASE)

    if re.search(r'\bCREATE\s+TABLE\b', sql, re.IGNORECASE):
        sql = _adapt_create_table(sql)

    sql = sql.replace('?', '%s')

    needs_returning = False
    im = re.match(r'\s*INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)', sql, re.IGNORECASE)

    if is_ignore:
        sql = sql.rstrip().rstrip(';') + '\nON CONFLICT DO NOTHING'
    elif is_replace and im:
        table = im.group(1).lower()
        cols  = [c.strip().lower() for c in im.group(2).split(',')]
        sql   = sql.rstrip().rstrip(';') + '\n' + _conflict_clause(table, cols)
    elif is_replace:
        sql = sql.rstrip().rstrip(';') + '\nON CONFLICT DO NOTHING'

    if im:
        table = im.group(1).lower()
        if table in _SERIAL_TABLES and 'RETURNING' not in sql.upper():
            sql = sql.rstrip().rstrip(';') + '\nRETURNING id'
            needs_returning = True

    if 'content_pool' in sql.lower():
        sql = re.sub(r'\bcreated\b', 'created_at', sql, flags=re.IGNORECASE)

    return sql, needs_returning


def _split_script(script: str) -> list[str]:
    parts = re.split(r';\s*\n', script)
    return [p.strip() for p in parts if p.strip() and not p.strip().startswith('--')]


# ─── Cursor wrapper ───────────────────────────────────────────────────────────

class PGCursor:
    def __init__(self, raw_cursor: Any) -> None:
        self._cur = raw_cursor
        self.lastrowid: Optional[int] = None
        self.rowcount:  int = 0

    def execute(self, sql: str, params: Any = None) -> "PGCursor":
        pg_sql, needs_id = adapt(sql)
        coerced = _coerce_params(params)
        try:
            self._cur.execute(pg_sql, coerced)
        except Exception as exc:
            log.error("pgcompat execute error: %s\nSQL: %s\nParams: %s", exc, pg_sql[:300], params)
            raise
        self.rowcount = self._cur.rowcount
        if needs_id:
            row = self._cur.fetchone()
            self.lastrowid = row[0] if row else None
        return self

    def executemany(self, sql: str, seq: Any) -> "PGCursor":
        pg_sql, _ = adapt(sql)
        coerced_seq = [_coerce_params(row) for row in seq] if _USE_POSTGRES else seq
        self._cur.executemany(pg_sql, coerced_seq)
        self.rowcount = self._cur.rowcount
        return self

    def executescript(self, script: str) -> "PGCursor":
        for stmt in _split_script(script):
            self.execute(stmt)
        return self

    def fetchone(self) -> Optional[Any]:
        return self._cur.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cur.fetchall()

    def __iter__(self) -> Iterator[Any]:
        return iter(self._cur)

    def close(self) -> None:
        self._cur.close()


# ─── Connection wrapper ───────────────────────────────────────────────────────

class PGConnection:
    """
    Wraps a psycopg2 connection.  Emulates the sqlite3.Connection API.
    autocommit=True so that conn.commit() is a no-op and individual
    statements are committed immediately (matches SQLite default behaviour).
    """

    def __init__(self, raw_conn: Any) -> None:
        self._conn = raw_conn
        self._conn.autocommit = True
        self._cursor: Optional[PGCursor] = None
        self.row_factory = None

    def cursor(self) -> PGCursor:
        self._cursor = PGCursor(self._conn.cursor())
        return self._cursor

    def execute(self, sql: str, params: Any = None) -> PGCursor:
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, seq: Any) -> None:
        cur = self.cursor()
        cur.executemany(sql, seq)

    def executescript(self, script: str) -> None:
        cur = self.cursor()
        cur.executescript(script)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        try:
            _get_pool().putconn(self._conn)
        except Exception:
            pass

    def __enter__(self) -> "PGConnection":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


# ─── Public API (mirrors sqlite3 module) ─────────────────────────────────────

class Row:
    """Sentinel: conn.row_factory = sqlite3.Row (no-op — DictCursor handles it)."""


class OperationalError(Exception):
    pass


def connect(*args: Any, **_kwargs: Any) -> Union[PGConnection, Any]:
    """
    Postgres: pooled connection to NEON / DATABASE_URL / COCKROACH_DATABASE_URL.

    No URL set: falls back to stdlib SQLite at NISHIBEE_DB (default writers.db)
    so the bots run locally without secrets (Render must set a Postgres URL).
    """
    if _USE_POSTGRES:
        raw = _get_pool().getconn()
        return PGConnection(raw)
    path = (
        (args[0] if args and isinstance(args[0], str) else None)
        or _kwargs.get("database")
        or os.environ.get("NISHIBEE_DB", "writers.db")
    )
    return _stdlib_sqlite3.connect(path, check_same_thread=False)
