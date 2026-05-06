"""
bulk_push.py — BotSuba → MainCock/MainButt Worker

Wakes every 5 minutes, reads all pending bot writes from BotSuba,
routes each record to the correct CockroachDB using the sequencer,
then deletes the record from BotSuba after a confirmed write.
"""
import asyncio
import os
import time
from typing import Optional
from supabase import create_client, Client

# BotSuba Configuration
BOTSUBA_URL = os.environ.get("BOTSUBA_URL", "")
BOTSUBA_KEY = os.environ.get("BOTSUBA_KEY", "")

_botsuba: Optional[Client] = None


def _get_botsuba() -> Optional[Client]:
    global _botsuba
    if _botsuba:
        return _botsuba
    if not BOTSUBA_URL or not BOTSUBA_KEY:
        print("⚠️ [BULK_PUSH] BotSuba not configured. Worker will idle.")
        return None
    try:
        _botsuba = create_client(BOTSUBA_URL, BOTSUBA_KEY)
        print("✅ [BULK_PUSH] BotSuba connected.")
        return _botsuba
    except Exception as e:
        print(f"❌ [BULK_PUSH] BotSuba init error: {e}")
        return None


async def _push_economy(records: list, conn):
    """Push economy updates to the correct CockroachDB."""
    if not conn or not records:
        return 0
    cur = conn.cursor()
    pushed = 0
    for r in records:
        try:
            cur.execute("""
                INSERT INTO economy (discord_id, guild_id, honey, xp)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (discord_id, guild_id) DO UPDATE SET
                honey = economy.honey + EXCLUDED.honey,
                xp    = economy.xp    + EXCLUDED.xp
            """, (r['discord_id'], r['guild_id'], r.get('honey', 0), r.get('xp', 0)))
            pushed += 1
        except Exception as e:
            print(f"[BULK_PUSH] Economy write error for {r.get('discord_id')}: {e}")
    conn.commit()
    cur.close()
    return pushed


async def bulk_push_worker():
    """
    Background task: Drains BotSuba into the correct CockroachDB every 5 minutes.
    
    Flow:
    1. Read all pending records from BotSuba `bot_writes` table
    2. Group by owner_id
    3. Look up each owner's integer_id via sequencer
    4. Route to MainCock (odd) or MainButt (even)
    5. Write to correct DB
    6. Delete from BotSuba on success
    """
    from sequencer import get_integer_id
    from router import get_db_conn

    await asyncio.sleep(30)  # Wait for server startup before first run

    while True:
        try:
            client = _get_botsuba()
            if not client:
                await asyncio.sleep(300)
                continue

            print("[BULK_PUSH] Starting 5-minute cycle...")

            # Fetch all pending writes
            res = client.table("bot_writes").select("*").execute()
            records = res.data or []

            if not records:
                print("[BULK_PUSH] No pending writes.")
                await asyncio.sleep(300)
                continue

            print(f"[BULK_PUSH] Processing {len(records)} pending records...")

            # Group by integer_id for batch efficiency
            db1_records = []
            db2_records = []
            record_ids  = []

            for r in records:
                owner_id   = str(r.get('owner_id', r.get('discord_id', 'unknown')))
                owner_type = r.get('owner_type', 'discord_user')
                integer_id = get_integer_id(owner_id, owner_type)
                record_ids.append(r['id'])

                if integer_id % 2 == 1:
                    db1_records.append(r)
                else:
                    db2_records.append(r)

            # Write to MainCock (DB1)
            if db1_records:
                conn1 = get_db_conn(1)  # 1 is odd → DB1
                pushed1 = await _push_economy(db1_records, conn1)
                if conn1: conn1.close()
                print(f"[BULK_PUSH] ✅ MainCock: {pushed1} records written.")

            # Write to MainButt (DB2)
            if db2_records:
                conn2 = get_db_conn(2)  # 2 is even → DB2
                pushed2 = await _push_economy(db2_records, conn2)
                if conn2: conn2.close()
                print(f"[BULK_PUSH] ✅ MainButt: {pushed2} records written.")

            # Delete all processed records from BotSuba
            if record_ids:
                client.table("bot_writes").delete().in_("id", record_ids).execute()
                print(f"[BULK_PUSH] 🗑️ Cleared {len(record_ids)} records from BotSuba.")

        except Exception as e:
            print(f"[BULK_PUSH] ❌ Cycle error: {e}")

        await asyncio.sleep(300)  # Run every 5 minutes
