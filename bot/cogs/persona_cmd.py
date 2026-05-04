"""
cogs/persona_cmd.py
====================
/persona slash command — registered on BOTH HoneyBee and HiveGPT.

Sub-actions
-----------
  status          — show which bot you are currently talking to
  switch [target] — change your active persona
  info   [target] — user-friendly description of a persona (not the raw
                    system prompt)

Design rules
------------
- Every response is ephemeral (only the invoking user sees it).
- No honeybee-specific imports at module level — cog works on either bot.
- Uses core.persona.store for all reads/writes (async-safe).
- Banter hand-off phrases are shown when the user switches persona.

Loading
-------
    from cogs import persona_cmd
    await persona_cmd.setup(bot)          # call in each bot's on_ready()
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from core.persona import store as _store, handoff_line, PERSONA_LABELS

# ── Colours and copy ──────────────────────────────────────────────────────────

_COLOUR = {
    "honeybee": discord.Colour.from_rgb(255, 215, 0),   # gold
    "hivegpt":  discord.Colour.from_rgb(88,  101, 242),  # discord blurple
}

_EMOJI = {
    "honeybee": "🐝🍯",
    "hivegpt":  "🐝💬",
}

# User-facing descriptions — readable prose, NOT the raw LLM system prompt.
_INFO = {
    "honeybee": (
        "**HoneyBee** is the warm, cheerful community manager of The Hive.\n\n"
        "She handles:\n"
        "• 🍯 Honey rewards and the economy (`/honey`, `/give`, `/shop`)\n"
        "• 📚 Rank-ups, badges, and writing milestones (`/rank`, `/badges`)\n"
        "• 🗓️ Daily check-ins, streaks, and writing quests\n"
        "• 📣 Server events, spotlights, and announcements\n"
        "• 🎫 Ticket management and member verification\n\n"
        "She has a soft spot for her boyfriend HiveGPT 💛 and will ping him "
        "whenever she needs AI help with wording or analysis."
    ),
    "hivegpt": (
        "**HiveGPT** is the AI helper bee of The Hive.\n\n"
        "He handles:\n"
        "• 💬 Writing questions, feedback, and genre advice (`/ask`, `/helpbee`)\n"
        "• ✍️ Intro polishing and summary writing\n"
        "• 📖 Webnovel chapter tracking and reader reports\n"
        "• 🤖 Multi-model AI completions (OpenAI, Gemini, Groq, …)\n"
        "• 🔍 OCR on Inkstone dashboards for rank verification\n\n"
        "He always calls HoneyBee 'my honey 🍯' when he needs her to grant "
        "a role, drop honey, or post in an announcement channel."
    ),
}

_TARGET_CHOICES = [
    app_commands.Choice(name="HoneyBee 🐝🍯", value="honeybee"),
    app_commands.Choice(name="HiveGPT 🐝💬",  value="hivegpt"),
]

_ACTION_CHOICES = [
    app_commands.Choice(name="Check status", value="status"),
    app_commands.Choice(name="Switch",        value="switch"),
    app_commands.Choice(name="Info",          value="info"),
    app_commands.Choice(name="Reset",         value="reset"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _status_embed(user: discord.User | discord.Member, persona: str) -> discord.Embed:
    label   = PERSONA_LABELS.get(persona, persona)
    other   = "hivegpt" if persona == "honeybee" else "honeybee"
    o_label = PERSONA_LABELS.get(other, other)
    embed = discord.Embed(
        title=f"{_EMOJI[persona]}  Active persona: {label}",
        description=(
            f"Every command you run is currently handled by **{label}**.\n\n"
            f"Use `/persona switch target:{other}` to talk to **{o_label}** instead.\n"
            f"Use `/persona info` to learn what each bot does."
        ),
        colour=_COLOUR[persona],
    )
    embed.set_footer(text=f"Requested by {user.display_name}")
    return embed


def _switch_embed(
    user: discord.User | discord.Member,
    old_persona: str,
    new_persona: str,
) -> discord.Embed:
    label = PERSONA_LABELS.get(new_persona, new_persona)
    phrase = handoff_line(old_persona, new_persona)
    body = f"You are now talking to **{label}**."
    if phrase:
        body += f"\n\n_{phrase}_"
    embed = discord.Embed(
        title=f"Switched to {_EMOJI[new_persona]}  {label}",
        description=body,
        colour=_COLOUR[new_persona],
    )
    embed.set_footer(text=f"Requested by {user.display_name}")
    return embed


def _info_embed(persona: str) -> discord.Embed:
    label = PERSONA_LABELS.get(persona, persona)
    embed = discord.Embed(
        title=f"{_EMOJI[persona]}  About {label}",
        description=_INFO[persona],
        colour=_COLOUR[persona],
    )
    embed.set_footer(text="Use /persona switch to change your active persona.")
    return embed


def _error_embed(title: str, body: str) -> discord.Embed:
    return discord.Embed(title=f"⚠️  {title}", description=body,
                         colour=discord.Colour.red())


# ── Cog ───────────────────────────────────────────────────────────────────────

class PersonaCmdCog(commands.Cog, name="PersonaCmd"):
    """User-facing /persona management — works on both HoneyBee and HiveGPT."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="persona",
        description="Check, switch, or learn about your active bot persona.",
    )
    @app_commands.describe(
        action="What to do — check status, switch persona, view info, or reset.",
        target="Which persona to switch to or get info about.",
    )
    @app_commands.choices(action=_ACTION_CHOICES, target=_TARGET_CHOICES)
    async def persona_cmd(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        target: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        user    = interaction.user
        user_id = user.id
        act     = action.value

        # ── status ────────────────────────────────────────────────────────────
        if act == "status":
            current = await _store.aget(user_id)
            embed   = _status_embed(user, current)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # ── switch ────────────────────────────────────────────────────────────
        if act == "switch":
            if target is None:
                return await interaction.response.send_message(
                    embed=_error_embed(
                        "Target required",
                        "Please pick a persona to switch to:\n"
                        "`/persona switch target:HoneyBee 🐝🍯`\n"
                        "`/persona switch target:HiveGPT 🐝💬`",
                    ),
                    ephemeral=True,
                )
            old = await _store.aget(user_id)
            new = target.value
            if old == new:
                label = PERSONA_LABELS.get(new, new)
                return await interaction.response.send_message(
                    embed=discord.Embed(
                        title=f"{_EMOJI[new]}  Already active",
                        description=f"**{label}** is already your active persona.",
                        colour=_COLOUR[new],
                    ),
                    ephemeral=True,
                )
            await _store.set(new, user_id)
            embed = _switch_embed(user, old, new)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # ── info ──────────────────────────────────────────────────────────────
        if act == "info":
            persona = target.value if target else await _store.aget(user_id)
            if persona not in _INFO:
                return await interaction.response.send_message(
                    embed=_error_embed("Unknown persona", f"No info for `{persona}`."),
                    ephemeral=True,
                )
            return await interaction.response.send_message(
                embed=_info_embed(persona), ephemeral=True,
            )

        # ── reset ─────────────────────────────────────────────────────────────
        if act == "reset":
            await _store.clear(user_id)
            default_label = PERSONA_LABELS.get("honeybee", "HoneyBee 🐝🍯")
            embed = discord.Embed(
                title="🔄  Persona reset",
                description=(
                    f"Your persona has been cleared.\n"
                    f"The default (**{default_label}**) will be restored "
                    f"the next time you run any command."
                ),
                colour=discord.Colour.greyple(),
            )
            embed.set_footer(text=f"Requested by {user.display_name}")
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # ── fallback (should never reach here) ────────────────────────────────
        await interaction.response.send_message(
            embed=_error_embed("Unknown action", f"Unrecognised action: `{act}`."),
            ephemeral=True,
        )


# ── Setup ─────────────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    """
    Load the PersonaCmd cog onto any bot.

    Call from on_ready() on both HoneyBee and HiveGPT:

        from cogs import persona_cmd
        if not bot.get_cog("PersonaCmd"):
            await persona_cmd.setup(bot)
    """
    await bot.add_cog(PersonaCmdCog(bot))
