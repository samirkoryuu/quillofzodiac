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

botsuba: Optional[Client] = None


def _get_botsuba() -> Optional[Client]:
    global botsuba
    if botsuba:
        return botsuba
    if not BOTSUBA_URL or not BOTSUBA_KEY:
        print("⚠️ [BULK_PUSH] BotSuba not configured. Worker will idle.")
        return None
    try:
        botsuba = create_client(BOTSUBA_URL, BOTSUBA_KEY)
        print("✅ [BULK_PUSH] BotSuba connected.")
        return botsuba
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


async def perform_sync():
    """Performs a single sync cycle from BotSuba to CockroachDB."""
    try:
        client = _get_botsuba()
        if not client:
            return False

        from sequencer import get_integer_id
        from router import get_db_conn

        # 1. Fetch IDs of pending writes
        res = client.table("bot_writes").select("id").eq("status", "pending").execute()
        pending_ids = [r['id'] for r in res.data or []]

        if not pending_ids:
            return True

        # 2. Claim records
        claim_res = client.table("bot_writes").update({"status": "processing"}).in_("id", pending_ids).eq("status", "pending").execute()
        records = claim_res.data or []

        if not records:
            return False

        # Group by integer_id
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

        if db1_records:
            conn1 = get_db_conn(1)
            await _push_economy(db1_records, conn1)
            if conn1: conn1.close()

        if db2_records:
            conn2 = get_db_conn(2)
            await _push_economy(db2_records, conn2)
            if conn2: conn2.close()

        if record_ids:
            client.table("bot_writes").delete().in_("id", record_ids).execute()
        
        return True

    except Exception as e:
        print(f"[BULK_PUSH] Manual sync error: {e}")
        return False

async def bulk_push_worker():
    """Background task: Drains BotSuba into the correct CockroachDB every 5 minutes."""
    await asyncio.sleep(30)
    while True:
        await perform_sync()
        await asyncio.sleep(300)
