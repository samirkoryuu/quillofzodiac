"""
cogs/status_cmd.py
==================
/status — admin-only live health dashboard in Discord.

Shows in a single ephemeral embed:
  • Both bots' websocket latency, guild count, and logged-in user
  • AI key pool (provider, model, cooldown state) — HiveGPT only
  • Persona store  — how many users have explicit assignments
  • Conversation memory  — total rows, unexpired vs expired breakdown
  • Database backend — Postgres or SQLite, connection pool state
  • Webnovel tracker — registered writers and books count

Single source of truth
-----------------------
Bot health data comes exclusively from ``services.webserver._bot_status_dict()``.
That function powers the HTTP /status JSON endpoint AND this Discord command —
no data is duplicated between the two.

Access control
--------------
Requires Manage Server or Administrator in the invoking channel.
Response is always ephemeral so the output never leaks to the channel.

Module-level structure
----------------------
All data-gathering helpers are pure Python with NO discord imports so they
can be unit-tested without discord.py installed.  The discord-dependent Cog
class and setup() function are defined inside a try/except ImportError block
at the bottom of the file.

Loading
-------
    from cogs import status_cmd
    if not bot.get_cog("StatusCmd"):
        await status_cmd.setup(bot)      # call in each bot's on_ready()
"""
from __future__ import annotations

import datetime
import logging

log = logging.getLogger("status_cmd")


# ---------------------------------------------------------------------------
# Data-gathering helpers — pure Python, no discord, fully unit-testable
# ---------------------------------------------------------------------------

def _bots_field_value() -> str:
    """
    Format all registered bots' health into a Discord field string.

    Calls ``services.webserver._bot_status_dict()`` — the same function that
    powers the HTTP /status JSON endpoint — so there is a single source of
    truth for bot health data.
    """
    try:
        from services.webserver import _bot_status_dict
        bots = _bot_status_dict()
        if not bots:
            return "⚠️ No bots registered with webserver yet."
        lines = []
        for name, info in bots.items():
            if info["ready"]:
                detail = (
                    f"`{info['user']}` · "
                    f"{info['guild_count']} server(s) · "
                    f"{info['latency_ms']} ms"
                )
                lines.append(f"✅ **{name}** — {detail}")
            elif info["closed"]:
                lines.append(f"💤 **{name}** — closed")
            else:
                lines.append(f"⏳ **{name}** — connecting…")
        return "\n".join(lines)
    except Exception as exc:
        return f"⚠️ {exc}"


def _db_backend_line() -> str:
    """Return a one-liner describing the active database backend."""
    try:
        from core import pgcompat
        if pgcompat._USE_POSTGRES:
            pool_size = len(pgcompat._get_pool()._pool) if pgcompat._pool else 0  # type: ignore[attr-defined]
            return f"✅ PostgreSQL — pool size {pool_size}"
        else:
            import os
            path = os.environ.get("HONEYBEE_DB", "writers.db")
            return f"🗃️ SQLite (local) — `{path}`"
    except Exception as exc:
        return f"⚠️ unknown ({exc})"


def _persona_stats_lines() -> str:
    """Return a compact summary of the persona store state."""
    try:
        from core.persona import store as _store, PERSONA_LABELS
        snap = _store.snapshot()
        if not snap:
            return "No explicit assignments — all users on default (HoneyBee)."
        counts: dict[str, int] = {}
        for persona in snap.values():
            counts[persona] = counts.get(persona, 0) + 1
        lines = [f"**{len(snap)}** user(s) with explicit assignments:"]
        for persona, n in sorted(counts.items()):
            label = PERSONA_LABELS.get(persona, persona)  # type: ignore[arg-type]
            lines.append(f"  • {label}: {n}")
        return "\n".join(lines)
    except Exception as exc:
        return f"⚠️ {exc}"


def _memory_stats_lines() -> str:
    """Return memory row counts split by TTL window."""
    try:
        import hivegpt
        db  = hivegpt.db
        now = datetime.datetime.utcnow()

        total = db.execute("SELECT COUNT(*) FROM hive_memory").fetchone()[0]
        if total == 0:
            return "No memory rows."

        cutoff = (now - datetime.timedelta(hours=hivegpt.MEMORY_TTL_HOURS)).isoformat()
        fresh  = db.execute(
            "SELECT COUNT(*) FROM hive_memory WHERE created_at > ?", (cutoff,)
        ).fetchone()[0]
        users  = db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM hive_memory WHERE created_at > ?",
            (cutoff,),
        ).fetchone()[0]
        expired = total - fresh
        ttl_h   = hivegpt.MEMORY_TTL_HOURS
        return (
            f"**{fresh}** live rows ({users} user(s)) within {ttl_h}h TTL\n"
            f"**{expired}** expired rows (purged on next cycle)"
        )
    except Exception as exc:
        return f"⚠️ {exc}"


def _key_pool_lines() -> str:
    """Return the AI key pool status from hivegpt."""
    try:
        import hivegpt
        summary = hivegpt.keys_status_summary()
        if not summary or summary == "(no keys loaded)":
            return "⚠️ No API keys loaded — HiveGPT cannot make LLM calls."
        lines = summary.splitlines()
        # Truncate at 10 keys to stay within Discord's 1024-char field limit
        if len(lines) > 10:
            lines = lines[:10] + [f"_…and {len(lines) - 10} more_"]
        return "```\n" + "\n".join(lines) + "\n```"
    except Exception as exc:
        return f"⚠️ {exc}"


def _wn_tracker_lines() -> str:
    """Return a brief summary from the webnovel tracker store."""
    try:
        from core.webnovel_tracker import TrackerStore
        ts      = TrackerStore()
        writers = ts.list_writers()
        if not writers:
            return "No tracked writers."
        total_books = 0
        for w in writers:
            try:
                books = ts.list_books(w["profile_id"])
                total_books += len(books)
            except Exception:
                pass
        return (
            f"**{len(writers)}** tracked writer(s), "
            f"**{total_books}** book(s) on record"
        )
    except Exception as exc:
        return f"⚠️ {exc}"


# ---------------------------------------------------------------------------
# Discord Cog — only defined when discord.py is installed (production).
# The pure helpers above remain importable in test environments without it.
# ---------------------------------------------------------------------------

try:
    import discord
    from discord import app_commands
    from discord.ext import commands

    _COLOUR = discord.Colour.from_rgb(255, 215, 0)  # hive gold

    class StatusCmdCog(commands.Cog, name="StatusCmd"):
        """Admin-only /status health command — works on both HoneyBee and HiveGPT."""

        def __init__(self, bot: commands.Bot) -> None:
            self.bot = bot

        @app_commands.command(
            name="status",
            description="Live health dashboard — key pool, memory, DB, bots (admin only).",
        )
        async def status_cmd(self, interaction: discord.Interaction) -> None:
            # ── Permission gate ───────────────────────────────────────────────
            perms = (
                interaction.channel.permissions_for(interaction.user)  # type: ignore[arg-type]
                if interaction.channel else None
            )
            is_admin = perms and (perms.manage_guild or perms.administrator)
            if not is_admin and interaction.guild is not None:
                await interaction.response.send_message(
                    "🚫 This command requires **Manage Server** or **Administrator**.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)

            # Detect which bot is running this command so HiveGPT can show
            # the AI key pool section and HoneyBee can show a helpful hint.
            is_hivegpt = "honey" not in (self.bot.user.name or "").lower()

            embed = discord.Embed(
                title="🐝 HiveSlave — Live Status",
                colour=_COLOUR,
                timestamp=datetime.datetime.utcnow(),
            )
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")

            # ── Bots — via webserver._bot_status_dict() ───────────────────────
            embed.add_field(name="🤖 Bots", value=_bots_field_value(), inline=False)

            # ── Database ──────────────────────────────────────────────────────
            embed.add_field(name="🗄️ Database", value=_db_backend_line(), inline=False)

            # ── Persona store ─────────────────────────────────────────────────
            embed.add_field(name="🎭 Persona store", value=_persona_stats_lines(), inline=False)

            # ── Conversation memory ───────────────────────────────────────────
            embed.add_field(name="🧠 Memory", value=_memory_stats_lines(), inline=False)

            # ── AI key pool (HiveGPT only) ────────────────────────────────────
            if is_hivegpt:
                embed.add_field(name="🔑 AI key pool", value=_key_pool_lines(), inline=False)
            else:
                embed.add_field(
                    name="🔑 AI key pool",
                    value="_(run `/status` on HiveGPT for key details)_",
                    inline=False,
                )

            # ── Webnovel tracker ──────────────────────────────────────────────
            embed.add_field(name="📖 Webnovel tracker", value=_wn_tracker_lines(), inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)

    async def setup(bot: commands.Bot) -> None:
        """
        Load the StatusCmd cog onto any bot.

        Call from on_ready() on both HoneyBee and HiveGPT:

            from cogs import status_cmd
            if not bot.get_cog("StatusCmd"):
                await status_cmd.setup(bot)
        """
        await bot.add_cog(StatusCmdCog(bot))

except ImportError:
    # discord.py not installed (e.g. running tests).
    # The pure helper functions above are still importable.
    log.debug("discord.py not available — StatusCmd cog not registered.")
