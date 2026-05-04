"""
services/persona_manager.py
============================
Thin Discord wrapper around core.persona.PersonaStore.

Pure persona logic (get/set, system prompt, handoff phrases) lives in
core/persona.py — no discord needed there.

This module adds only the discord.py-specific layer:
  - app_commands.check() decorators: auto_assign, requires
  - /persona slash command (future)
  - Re-exports everything callers previously imported from here

Usage (unchanged from the original API):
-----------------------------------------
    from services.persona_manager import router, NISHIBEE_PERSONA_BLOCK

    @router.auto_assign("nishibee")
    async def my_command(interaction): ...

    @router.requires("hivegpt")
    async def gpt_only(interaction): ...

    router.get_persona(user_id)          # → "nishibee" | "hivegpt"
    router.set_persona(user_id, "hivegpt")
"""
from __future__ import annotations

import discord
from discord import app_commands

from core.persona import (           # pure layer
    PersonaName,
    PersonaStore,
    store as _shared_store,          # the process-wide singleton
    NISHIBEE_PERSONA_BLOCK,          # re-export for callers
    HIVEGPT_PERSONA_BLOCK,
    system_prompt_for,
    handoff_line,
    aside,
)

# Re-export banter helpers so existing callers don't need to change imports
from services.banter import (
    honey_handoff_to_gpt,
    gpt_handoff_to_honey,
    honey_aside,
    gpt_aside,
)


class PersonaRouter(PersonaStore):
    """
    PersonaStore + discord app_commands.check() decorators.

    Delegates all storage to the parent class; adds Discord-specific
    sugar on top so cogs can gate or auto-assign persona with a decorator.
    """

    # ── backward-compat aliases ───────────────────────────────────────────────

    def get_persona(self, user_id: int) -> PersonaName:
        """Alias for PersonaStore.get() — kept for backward compat."""
        return self.get(user_id)

    async def set_persona(self, user_id: int, persona: PersonaName) -> None:
        """Alias for PersonaStore.set() — kept for backward compat."""
        await self.set(persona, user_id)

    async def clear_persona(self, user_id: int) -> None:
        """Alias for PersonaStore.clear() — kept for backward compat."""
        await self.clear(user_id)

    def all_personas(self) -> dict[int, PersonaName]:
        """Alias for PersonaStore.snapshot() — kept for backward compat."""
        return self.snapshot()

    # ── discord decorators ────────────────────────────────────────────────────

    def requires(self, bot_name: PersonaName):
        """
        app_commands.check() that BLOCKS the command if the user's persona
        doesn't match bot_name.  Sends an ephemeral hint naming the right bot.

        Example::
            @router.requires("hivegpt")
            async def ask_command(interaction): ...
        """
        _labels = {
            "nishibee": "NishiBee 🐝🍯",
            "hivegpt":  "HiveGPT 🐝💬",
        }

        async def predicate(interaction: discord.Interaction) -> bool:
            current = self.get(interaction.user.id)
            if current != bot_name:
                current_label = _labels.get(current, current)
                needed_label  = _labels.get(bot_name, bot_name)
                await interaction.response.send_message(
                    f"⚠️ This command belongs to **{needed_label}**.\n"
                    f"Your active persona is **{current_label}**.\n"
                    f"Use `/persona {bot_name}` to switch.",
                    ephemeral=True,
                )
                return False
            return True

        return app_commands.check(predicate)

    def auto_assign(self, bot_name: PersonaName):
        """
        app_commands.check() that silently assigns bot_name as the user's
        persona, then always returns True (never blocks the command).

        Example::
            @router.auto_assign("nishibee")
            async def rank_command(interaction): ...
        """
        async def predicate(interaction: discord.Interaction) -> bool:
            await self.set(bot_name, interaction.user.id)
            return True

        return app_commands.check(predicate)


# ── Process-wide singleton ────────────────────────────────────────────────────
# The router's storage IS the shared PersonaStore singleton from core.persona,
# so any persona assignment made here is immediately visible to the other bot.

class _SharedPersonaRouter(PersonaRouter):
    """PersonaRouter that delegates storage to core.persona.store."""

    def get(self, user_id: int, default: PersonaName = "nishibee") -> PersonaName:
        return _shared_store.get(user_id, default)

    async def aget(self, user_id: int, default: PersonaName = "nishibee") -> PersonaName:
        return await _shared_store.aget(user_id, default)

    async def set(self, persona: PersonaName, user_id: int) -> None:
        await _shared_store.set(persona, user_id)

    async def set_if_unset(self, persona: PersonaName, user_id: int) -> None:
        await _shared_store.set_if_unset(persona, user_id)

    async def clear(self, user_id: int) -> None:
        await _shared_store.clear(user_id)

    def snapshot(self) -> dict[int, PersonaName]:
        return _shared_store.snapshot()


router = _SharedPersonaRouter()

__all__ = [
    "PersonaName",
    "PersonaRouter",
    "router",
    "NISHIBEE_PERSONA_BLOCK",
    "HIVEGPT_PERSONA_BLOCK",
    "system_prompt_for",
    "handoff_line",
    "aside",
    "honey_handoff_to_gpt",
    "gpt_handoff_to_honey",
    "honey_aside",
    "gpt_aside",
]
