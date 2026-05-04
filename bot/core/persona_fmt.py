"""
core/persona_fmt.py
====================
Persona-aware response formatting — pure Python, no discord imports.

This module bridges core/persona.py (the store) and services/banter.py
(the phrases) into ready-to-use helpers for cog command handlers.

Two usage patterns
------------------

1.  **Inline formatting** — wrap any programmatically-generated string
    before sending it to Discord:

        from core.persona_fmt import format_bot_response
        from core.persona import store

        user_persona = await store.aget(interaction.user.id)
        text = format_bot_response(
            "Here are your stats: ...",
            responding_as="nishibee",
            user_persona=user_persona,
        )
        await interaction.response.send_message(text, ephemeral=True)

2.  **Handoff prefix** — prepend before delegating to the other bot:

        from core.persona_fmt import handoff_prefix

        phrase = handoff_prefix("nishibee", "hivegpt")
        await interaction.response.send_message(
            f"{phrase}\n\n{ai_generated_reply}"
        )

3.  **@persona_context decorator** — auto-reads persona and injects it
    as a kwarg into any cog coroutine:

        from core.persona_fmt import persona_context

        class MyCog(commands.Cog):
            @app_commands.command()
            @persona_context          # must be BELOW @app_commands.command
            async def my_cmd(self, interaction, *, user_persona="nishibee"):
                text = format_bot_response("Hello!", responding_as="nishibee",
                                           user_persona=user_persona)
                await interaction.response.send_message(text)

Persona mismatch notes
-----------------------
When the user's active persona is "hivegpt" but NishiBee is responding
(or vice-versa), a light contextual note is added so the user understands
why the "wrong" bot replied.  The note is optional and comes from
services/banter.py so the wording stays consistent across the hive.
"""
from __future__ import annotations

import functools
from typing import Callable

from core.persona import store as _store, PersonaName
from services.banter import (
    honey_handoff_to_gpt,
    gpt_handoff_to_honey,
    honey_aside,
    gpt_aside,
)

# ---------------------------------------------------------------------------
# Cross-bot mismatch notes
# ---------------------------------------------------------------------------

# When a user's persona is X but bot Y is responding, we add a short aside
# so the response feels intentional rather than like a bug.
_MISMATCH_NOTES: dict[tuple[str, str], Callable[[], str]] = {
    # user expects HiveGPT, but NishiBee is answering
    ("hivegpt", "nishibee"): lambda: (
        f"_(my boyfriend {honey_aside()} usually handles this, "
        "but I've got it for you! 🍯)_"
    ),
    # user expects NishiBee, but HiveGPT is answering
    ("nishibee", "hivegpt"): lambda: (
        f"_(my honey {gpt_aside()} usually handles this, "
        "but I can help too! 💛)_"
    ),
}


def cross_bot_note(user_persona: PersonaName, responding_as: PersonaName) -> str:
    """
    Return a banter aside when the responding bot ≠ the user's active persona.

    Returns "" when they match (no note needed).

    Parameters
    ----------
    user_persona  : persona currently assigned to the user
    responding_as : persona of the bot that is generating the response
    """
    if user_persona == responding_as:
        return ""
    factory = _MISMATCH_NOTES.get((user_persona, responding_as))
    return factory() if factory else ""


# ---------------------------------------------------------------------------
# Full response formatter
# ---------------------------------------------------------------------------

def format_bot_response(
    text: str,
    *,
    responding_as: PersonaName,
    user_persona: PersonaName,
    include_note: bool = True,
) -> str:
    """
    Wrap `text` with a persona mismatch note when appropriate.

    Parameters
    ----------
    text          : the core message body
    responding_as : persona of the bot generating this response
    user_persona  : persona currently assigned to the user
    include_note  : set False to suppress the cross-bot aside

    Returns
    -------
    The message string, optionally with a one-liner aside appended.

    Example
    -------
        # User has HiveGPT persona; NishiBee is responding
        msg = format_bot_response(
            "Your honey balance is 🍯 1,200.",
            responding_as="nishibee",
            user_persona="hivegpt",
        )
        # → "Your honey balance is 🍯 1,200.\n\n_(my boyfriend … usually handles this, but I've got it for you! 🍯)_"
    """
    note = cross_bot_note(user_persona, responding_as) if include_note else ""
    if note:
        return f"{text}\n\n{note}"
    return text


# ---------------------------------------------------------------------------
# Handoff prefix (delegation line)
# ---------------------------------------------------------------------------

def handoff_prefix(from_bot: PersonaName, to_bot: PersonaName) -> str:
    """
    Return the banter hand-off line to prepend when explicitly delegating.

    Use this when one bot is ABOUT to pass the request to the other:

        phrase = handoff_prefix("nishibee", "hivegpt")
        await ctx.send(f"{phrase}\n\n{hivegpt_reply}")

    Returns "" for same-bot or unknown combinations.
    """
    if from_bot == "nishibee" and to_bot == "hivegpt":
        return honey_handoff_to_gpt()
    if from_bot == "hivegpt" and to_bot == "nishibee":
        return gpt_handoff_to_honey()
    return ""


# ---------------------------------------------------------------------------
# @persona_context decorator
# ---------------------------------------------------------------------------

def persona_context(coro: Callable) -> Callable:
    """
    Decorator for discord.py app_commands coroutines.

    Reads the invoking user's persona from the shared store and injects it
    as the ``user_persona`` keyword argument.  The cog can then call
    ``format_bot_response(...)`` without an extra ``await store.aget(...)``
    call at the top of every handler.

    Usage::

        @app_commands.command()
        @persona_context            # must sit BELOW @app_commands.command
        async def my_cmd(self, interaction, *, user_persona="nishibee"):
            text = format_bot_response(
                "Hello!",
                responding_as="nishibee",
                user_persona=user_persona,
            )
            await interaction.response.send_message(text)

    The default ``user_persona="nishibee"`` in the signature is required
    for discord.py's introspection to ignore the injected kwarg.
    """
    @functools.wraps(coro)
    async def wrapper(*args, **kwargs):
        # args[0] = self (Cog), args[1] = interaction
        interaction = args[1] if len(args) > 1 else kwargs.get("interaction")
        user_id = interaction.user.id if interaction else 0
        kwargs["user_persona"] = await _store.aget(user_id)
        return await coro(*args, **kwargs)
    return wrapper


__all__ = [
    "cross_bot_note",
    "format_bot_response",
    "handoff_prefix",
    "persona_context",
]
