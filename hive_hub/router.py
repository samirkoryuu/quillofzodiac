"""
router.py — Dual-DB Traffic Cop

Takes an integer ID (from sequencer.py) and returns the correct
CockroachDB connection: MainCock (DB1) for odd, MainButt (DB2) for even.
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional

# MainCock — DB1 — ODD integer IDs
MAINCOCK_URL = os.environ.get("COCKROACH_DATABASE_URL", "")

# MainButt — DB2 — EVEN integer IDs
MAINBUTT_URL = os.environ.get("COCKROACH_DATABASE_URL_2", "")


def get_db_conn(integer_id: int):
    """
    Returns a psycopg2 connection to the correct CockroachDB.
    - ODD  integer_id → MainCock (DB1)
    - EVEN integer_id → MainButt (DB2)
    """
    if integer_id % 2 == 1:
        url = MAINCOCK_URL
        db_name = "MainCock (DB1)"
    else:
        url = MAINBUTT_URL
        db_name = "MainButt (DB2)"

    if not url:
        print(f"⚠️ [ROUTER] {db_name} URL not configured. Cannot connect.")
        return None

    try:
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"❌ [ROUTER] Failed to connect to {db_name}: {e}")
        return None


def get_db_conn_by_external_id(external_id: str, owner_type: str = "unknown"):
    """
    Convenience wrapper: takes an external ID (profile_id, discord_id, etc.)
    and returns the correct DB connection directly.
    """
    from sequencer import get_integer_id
    integer_id = get_integer_id(external_id, owner_type)
    return get_db_conn(integer_id)


def get_both_connections():
    """
    Returns connections to BOTH databases.
    Used for scatter-gather queries (global leaderboards, all-writers scans).
    Returns: (conn_db1, conn_db2) — either may be None if not configured.
    """
    conn1 = None
    conn2 = None
    if MAINCOCK_URL:
        try:
            conn1 = psycopg2.connect(MAINCOCK_URL, cursor_factory=RealDictCursor)
        except Exception as e:
            print(f"❌ [ROUTER] MainCock connection failed: {e}")
    if MAINBUTT_URL:
        try:
            conn2 = psycopg2.connect(MAINBUTT_URL, cursor_factory=RealDictCursor)
        except Exception as e:
            print(f"❌ [ROUTER] MainButt connection failed: {e}")
    return conn1, conn2
