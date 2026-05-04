"""
core/persona.py
================
Pure persona routing — zero discord.py imports.

The "persona" is which bot a user is primarily talking to:
  "nishibee"  → NishiBee 🐝🍯  (economy, roles, community)
  "hivegpt"   → HiveGPT  🐝💬  (AI assistant, helpbee)

This module is the single source of truth for:
  - Per-user persona assignment  (thread-safe, shared across both bots)
  - System-prompt block selection for LLM calls
  - Banter handoff phrases when one bot delegates to the other

Both NishiBee and HiveGPT import the SAME `store` singleton because they
run in one process.  No discord client needed to use any of this.

Usage
-----
    from core.persona import store, system_prompt_for, handoff_line, aside

    # Auto-assign when a NishiBee command fires:
    store.set("nishibee", interaction.user.id)

    # Before any LLM call in HiveGPT:
    persona_block = system_prompt_for(interaction.user.id)

    # When NishiBee hands off to HiveGPT:
    phrase = handoff_line("nishibee", "hivegpt")
    # → "Hold on a sec — let me ask my darling 💛 for help."

Testability
-----------
No discord client needed.  In unit tests:

    from core.persona import store, system_prompt_for
    store.set("hivegpt", user_id=42)
    assert "HiveGPT" in system_prompt_for(42)
"""
from __future__ import annotations

from typing import Literal

# banter.py is pure Python (no discord) — safe to import from core/
from services.banter import (
    NISHIBEE_PERSONA_BLOCK,
    HIVEGPT_PERSONA_BLOCK,
    MEGHDOOT_PERSONA_BLOCK,
    PREMAEL_PERSONA_BLOCK,
    honey_handoff_to_gpt,
    gpt_handoff_to_honey,
    honey_aside,
    gpt_aside,
)

PersonaName = Literal["nishibee", "hivegpt", "meghdoot", "premael"]

PERSONA_LABELS: dict[PersonaName, str] = {
    "nishibee": "NishiBee 🐝🍯",
    "hivegpt":  "HiveGPT 🐝💬",
    "meghdoot": "Meghdoot 🐉✨",
    "premael":  "Premael 🌹💖",
}

_PERSONA_PROMPTS: dict[PersonaName, str] = {
    "nishibee": NISHIBEE_PERSONA_BLOCK,
    "hivegpt":  HIVEGPT_PERSONA_BLOCK,
    "meghdoot": MEGHDOOT_PERSONA_BLOCK,
    "premael":  PREMAEL_PERSONA_BLOCK,
}


# ---------------------------------------------------------------------------
# Pure persona store (no discord)
# ---------------------------------------------------------------------------

class PersonaStore:
    """
    Asyncio-native in-memory registry: user_id → PersonaName.

    Design notes
    ------------
    asyncio is single-threaded: coroutines are cooperative and cannot
    preempt each other between ``await`` points.  A plain dict read or
    write with no ``await`` is therefore always atomic — no lock is needed.

    Write methods are declared ``async`` so that replacing the in-memory
    dict with a Redis (or any other async) backend requires zero changes
    at call sites — they are already ``await``-ed everywhere.

    Read methods stay synchronous because they are also called from
    ``system_prompt_for()`` which is invoked in tight synchronous paths;
    making them ``async`` would cascade the ``await`` requirement into
    every LLM call site.  For a Redis backend, add an ``aget()`` method
    alongside the existing synchronous ``get()``.

    No discord imports — fully usable in unit tests without a bot.
    """

    def __init__(self) -> None:
        self._map: dict[int, PersonaName] = {}

    # ── read (sync — dict access is atomic in asyncio's single thread) ────────

    def get(self, user_id: int, default: PersonaName = "nishibee") -> PersonaName:
        """
        Synchronous read — use in hot-paths where no ``await`` is possible.

        Typical caller: ``system_prompt_for(user_id)`` inside LLM message
        construction.  Because that path has no ``await`` between the read
        and the completion call, keeping this sync avoids propagating
        ``async`` through the entire LLM layer.
        """
        return self._map.get(user_id, default)

    async def aget(self, user_id: int, default: PersonaName = "nishibee") -> PersonaName:
        """
        Asynchronous read — use in standard async request-handling paths.

        Establishes the two-pattern contract:

        - ``store.get(user_id)``   → synchronous hot-path (LLM prompt building)
        - ``await store.aget(user_id)`` → async request path (cogs, checks)

        This prevents the mixed-mode mistake of calling a sync getter inside
        an async cog and forgetting to ``await`` it — the type signature
        makes the intent explicit.

        Redis migration template (single-line swap when ready):

            # return await self._redis.get(f"persona:{user_id}") or default
        """
        return self.get(user_id, default)

    def label(self, user_id: int) -> str:
        """Human-readable label, e.g. 'NishiBee 🐝🍯'."""
        return PERSONA_LABELS.get(self.get(user_id), "NishiBee 🐝🍯")

    def snapshot(self) -> dict[int, PersonaName]:
        """Return a shallow copy of all current assignments."""
        return dict(self._map)

    # ── write (async — forward-compatible with Redis / any async backend) ─────

    async def set(self, persona: PersonaName, user_id: int) -> None:
        """Assign a persona to a user.

        ``await store.set("nishibee", user_id)``

        Swapping to Redis: replace the dict write with
        ``await redis.set(f"persona:{user_id}", persona)``.
        """
        self._map[user_id] = persona

    async def set_if_unset(self, persona: PersonaName, user_id: int) -> None:
        """Assign only if the user has no explicit persona yet."""
        self._map.setdefault(user_id, persona)

    async def clear(self, user_id: int) -> None:
        """Remove explicit assignment — user reverts to the default."""
        self._map.pop(user_id, None)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def system_prompt_for(user_id: int) -> str:
    """
    Return the correct persona system-prompt block for an LLM call.

    Called by HiveGPT (and any future AI cog) before building the messages
    list for a completion request:

        messages = [
            {"role": "system", "content": system_prompt_for(user_id)},
            *history,
        ]
    """
    return _PERSONA_PROMPTS.get(store.get(user_id), NISHIBEE_PERSONA_BLOCK)


def handoff_line(from_persona: PersonaName, to_persona: PersonaName) -> str:
    """
    Return a banter hand-off phrase for cross-bot delegation.

    handoff_line("nishibee", "hivegpt")
        → "Hold on a sec — let me ask my darling 💛 for help."

    handoff_line("hivegpt", "nishibee")
        → "Let me get my honey 🍯 on this — she handles the role/honey side."

    Returns "" for same-bot or unknown combos.
    """
    if from_persona == "nishibee" and to_persona == "hivegpt":
        return honey_handoff_to_gpt()
    if from_persona == "hivegpt" and to_persona == "nishibee":
        return gpt_handoff_to_honey()
    return ""


def aside(persona: PersonaName) -> str:
    """
    Return a random one-liner parenthetical for the given persona.

    aside("nishibee") → "(yes, my darling 💛 is watching too — he says hi)"
    aside("hivegpt")  → "(by the way, my honey 🍯 sends 🍯)"
    """
    if persona == "nishibee":
        return honey_aside()
    if persona == "hivegpt":
        return gpt_aside()
    return ""


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------
# Both bots import this:
#   from core.persona import store
#
# Because both run in one process, they share the same dict object.
# asyncio's single-threaded event loop makes the plain-dict writes atomic;
# the async API future-proofs call sites for an eventual Redis backend.

store = PersonaStore()

__all__ = [
    "PersonaName",
    "PersonaStore",
    "store",
    "system_prompt_for",
    "handoff_line",
    "aside",
    "PERSONA_LABELS",
    "NISHIBEE_PERSONA_BLOCK",
    "HIVEGPT_PERSONA_BLOCK",
    # async read — use in cogs and interaction checks
    # sync read  — use in LLM hot-paths via system_prompt_for()
    # (both are on PersonaStore; listed here as documentation markers)
]
