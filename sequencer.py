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

_seeksuba: Optional[Client] = None
_id_cache: dict[str, int] = {}  # external_id → integer_id (in-memory cache)

def _get_client() -> Optional[Client]:
    global _seeksuba
    if _seeksuba:
        return _seeksuba
    if not SEEKSUBA_URL or not SEEKSUBA_KEY:
        print("⚠️ [SEQUENCER] SeekSuba not configured. Falling back to single-DB mode.")
        return None
    try:
        _seeksuba = create_client(SEEKSUBA_URL, SEEKSUBA_KEY)
        print("✅ [SEQUENCER] SeekSuba connected.")
        return _seeksuba
    except Exception as e:
        print(f"❌ [SEQUENCER] SeekSuba init error: {e}")
        return None


def get_integer_id(external_id: str, owner_type: str = "unknown") -> int:
    """
    Returns the persistent integer ID for an owner.
    Creates a new sequential ID if this owner is new.
    
    Args:
        external_id: The raw external ID (Webnovel profile_id, Discord user_id, node MAC)
        owner_type:  'writer' | 'discord_user' | 'node'
    
    Returns:
        integer_id (odd → DB1/MainCock, even → DB2/MainButt)
    """
    # 0. Skip 'unknown' IDs (failed scrapes)
    if not external_id or external_id == "unknown":
        return 1

    # 1. Check in-memory cache first (fastest)
    if external_id in _id_cache:
        return _id_cache[external_id]

    client = _get_client()

    # 2. Fallback: no SeekSuba → always return 1 (DB1 only mode)
    if not client:
        return 1

    try:
        # 3. Check if owner already exists in SeekSuba
        res = client.table("owner_sequences") \
            .select("integer_id") \
            .eq("external_id", external_id) \
            .maybe_single() \
            .execute()

        if res.data:
            integer_id = res.data["integer_id"]
            _id_cache[external_id] = integer_id
            return integer_id

        # 4. New owner — call the increment RPC to get next ID atomically
        counter_res = client.rpc("increment_owner_counter", {}).execute()
        integer_id = counter_res.data  # Returns the new counter value

        # 5. Store the mapping permanently
        client.table("owner_sequences").insert({
            "external_id": external_id,
            "integer_id": integer_id,
            "owner_type": owner_type
        }).execute()

        _id_cache[external_id] = integer_id
        print(f"[SEQUENCER] New owner '{external_id}' ({owner_type}) → ID {integer_id} → {'MainCock (DB1)' if integer_id % 2 == 1 else 'MainButt (DB2)'}")
        return integer_id

    except Exception as e:
        print(f"[SEQUENCER] Error getting ID for '{external_id}': {e}. Defaulting to DB1.")
        return 1


def which_db(external_id: str, owner_type: str = "unknown") -> str:
    """Returns 'DB1' or 'DB2' for logging/debugging."""
    integer_id = get_integer_id(external_id, owner_type)
    return "DB1 (MainCock)" if integer_id % 2 == 1 else "DB2 (MainButt)"
