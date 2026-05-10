"""
sequencer.py — Central ID Sequencer via SeekSuba (Supabase)

Hands out sequential integer IDs for every new owner (writer, discord user, node).
- ODD  integer ID → MainCock (DB1)
- EVEN integer ID → MainButt (DB2)

Uses SeekSuba's `owner_sequences` table as the single source of truth.
In-memory cache prevents redundant DB calls.
"""
import os
from supabase import create_client, Client
from typing import Optional

# SeekSuba Configuration
SEEKSUBA_URL = os.environ.get("SEEKSUBA_URL", "")
SEEKSUBA_KEY = os.environ.get("SEEKSUBA_KEY", "")

_seeksupabase: Optional[Client] = None
_id_cache: dict[str, int] = {}  # external_id → integer_id (in-memory cache)

def _get_client() -> Optional[Client]:
    global _seeksupabase
    if _seeksupabase:
        return _seeksupabase
    if not SEEKSUBA_URL or not SEEKSUBA_KEY:
        return None
    try:
        _seeksupabase = create_client(SEEKSUBA_URL, SEEKSUBA_KEY)
        return _seeksupabase
    except Exception as e:
        print(f"❌ [SEQUENCER] SeekSuba init error: {e}")
        return None


def get_integer_id(external_id: str, owner_type: str = "unknown") -> int:
    """
    Returns the persistent integer ID for an owner.
    Creates a new sequential ID if this owner is new.
    """
    if not external_id: return 1
    
    # 1. Check in-memory cache first
    ext_id_str = str(external_id)
    if ext_id_str in _id_cache:
        return _id_cache[ext_id_str]

    client = _get_client()

    # 2. Fallback: no SeekSuba → return parity of the ID itself if possible, else 1
    if not client:
        try:
            return int(external_id)
        except:
            return 1

    try:
        # 3. Check if owner already exists in SeekSuba
        res = client.table("owner_sequences") \
            .select("integer_id") \
            .eq("external_id", ext_id_str) \
            .maybe_single() \
            .execute()

        if res.data:
            integer_id = res.data["integer_id"]
            _id_cache[ext_id_str] = integer_id
            return integer_id

        # 4. New owner — call the increment RPC to get next ID atomically
        counter_res = client.rpc("increment_owner_counter", {}).execute()
        integer_id = counter_res.data

        # 5. Store the mapping permanently
        client.table("owner_sequences").insert({
            "external_id": ext_id_str,
            "integer_id": integer_id,
            "owner_type": owner_type
        }).execute()

        _id_cache[ext_id_str] = integer_id
        return integer_id

    except Exception as e:
        print(f"[SEQUENCER] Error getting ID for '{external_id}': {e}. Falling back to parity.")
        try: return int(external_id)
        except: return 1
