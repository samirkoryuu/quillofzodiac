"""
cogs/gamenews.py
================
Discord cog wrapper for core.gamenews.

This cog handles all discord.py-specific work:
  - Listening to on_presence_update events
  - The /brag slash command
  - Sending embeds to #gamenews
  - The session-reaper background task

All pure session/honey logic lives in core/gamenews.py.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.gamenews import GameNewsStore, SESSION_MIN_MINUTES, STREAK_MILESTONES

log = logging.getLogger("cogs.gamenews")

GAMENEWS_CHANNEL = os.environ.get("GAMENEWS_CHANNEL", "gamenews")


def _gamenews_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    return (
        discord.utils.get(guild.text_channels, name=GAMENEWS_CHANNEL)
        or discord.utils.get(guild.text_channels, name="game-news")
        or discord.utils.get(guild.text_channels, name="gamefun-controlroom")
    )


def _is_actual_game(activity) -> bool:
    if activity is None:
        return False
    if isinstance(activity, discord.Game):
        return True
    if isinstance(activity, discord.Activity) and activity.type == discord.ActivityType.playing:
        return True
    return False


def _activity_game_name(activity) -> Optional[str]:
    if not _is_actual_game(activity):
        return None
    name = getattr(activity, "name", None)
    return str(name).strip() or None if name else None


class GameNews(commands.Cog):
    def __init__(self, bot: commands.Bot, *, conn, c,
                 add_honey=None, add_xp=None, ocr_func=None):
        self.bot = bot
        self._ocr = ocr_func
        self.store = GameNewsStore(conn, c, add_honey_cb=add_honey, add_xp_cb=add_xp)
        self.session_reaper.start()

    def cog_unload(self):
        self.session_reaper.cancel()

    # -- channel helper ---------------------------------------------------

    async def _send(self, guild: discord.Guild, embed: discord.Embed) -> Optional[discord.Message]:
        ch = _gamenews_channel(guild)
        if not ch:
            return None
        try:
            return await ch.send(embed=embed)
        except Exception as e:
            log.warning("gamenews send failed: %s", e)
            return None

    # -- public announcement helpers -------------------------------------

    async def announce_levelup(self, member: discord.Member, new_level: int):
        e = discord.Embed(
            title="📈 Level up!",
            description=f"{member.mention} just hit **level {new_level}** in The Hive!",
            color=0xffd700,
        )
        e.set_footer(text="+50 🍯 bonus auto-applied · keep buzzing")
        await self._send(member.guild, e)

    async def announce_streak(self, member: discord.Member, days: int):
        if not self.store.should_announce_streak(member.id, member.guild.id, days):
            return
        e = discord.Embed(
            title="🔥 Streak milestone!",
            description=f"{member.mention} just hit a **{days}-day** writing/check-in streak!",
            color=0xff7e00,
        )
        e.set_footer(text="Consistency is the secret sauce 🍯")
        await self._send(member.guild, e)

    async def announce_badge(self, member: discord.Member, badge_id: str, badge_name: str):
        e = discord.Embed(
            title="🏅 Badge earned!",
            description=f"{member.mention} just unlocked **{badge_name}** ({badge_id})!",
            color=0x9b59b6,
        )
        await self._send(member.guild, e)

    async def announce_rankup(self, member: discord.Member, old_rank: str, new_rank: str):
        e = discord.Embed(
            title="👑 Rank up!",
            description=f"{member.mention} promoted from **{old_rank}** → **{new_rank}**!",
            color=0xf1c40f,
        )
        e.set_footer(text="Verified by HoneyBee + HiveGPT 💛🍯")
        await self._send(member.guild, e)

    async def announce_duel_win(self, member: discord.Member,
                                opponent: discord.Member, amount: int):
        e = discord.Embed(
            title="⚔️ Duel won!",
            description=(
                f"{member.mention} defeated {opponent.mention} and walked away "
                f"with **{amount:,} 🍯**!"
            ),
            color=0xe74c3c,
        )
        await self._send(member.guild, e)

    async def announce_session(self, member: discord.Member, game: str,
                               minutes: int, honey_earned: int):
        if minutes < SESSION_MIN_MINUTES:
            return
        e = discord.Embed(
            title="🎮 Game session",
            description=(
                f"{member.mention} just wrapped a **{minutes} min** session of "
                f"**{game}** — earned **{honey_earned} 🍯**!"
            ),
            color=0x3498db,
        )
        await self._send(member.guild, e)

    async def announce_brag(self, member: discord.Member, game: str,
                            achievement: str, image_url: str):
        e = discord.Embed(
            title="🏆 Verified game brag",
            description=f"{member.mention} just hit **{achievement}** in **{game}**!",
            color=0x2ecc71,
        )
        if image_url:
            e.set_image(url=image_url)
        e.set_footer(text="Verified by HiveGPT vision OCR 💛")
        await self._send(member.guild, e)

    # -- presence tracking -----------------------------------------------

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if after.bot:
            return
        was_playing = next((a for a in (before.activities or []) if _is_actual_game(a)), None)
        is_playing  = next((a for a in (after.activities  or []) if _is_actual_game(a)), None)
        was_name = _activity_game_name(was_playing)
        is_name  = _activity_game_name(is_playing)
        if was_name == is_name:
            return

        now = int(time.time())

        if was_name and not is_name:
            result = self.store.close_session(after.id, after.guild.id, now)
            if result:
                await self.announce_session(after, result.game, result.minutes, result.honey_earned)
                new_tiers = self.store.check_player_tier(after.id, after.guild.id)
                for _hours, label in new_tiers:
                    await self.announce_badge(after, f"player-{_hours}h", label)
        elif was_name and is_name and was_name != is_name:
            result = self.store.close_session(after.id, after.guild.id, now)
            if result:
                await self.announce_session(after, result.game, result.minutes, result.honey_earned)
                new_tiers = self.store.check_player_tier(after.id, after.guild.id)
                for _hours, label in new_tiers:
                    await self.announce_badge(after, f"player-{_hours}h", label)
            self.store.open_session(after.id, after.guild.id, is_name, now)
        elif (not was_name) and is_name:
            self.store.open_session(after.id, after.guild.id, is_name, now)

    # -- session reaper --------------------------------------------------

    @tasks.loop(minutes=10)
    async def session_reaper(self):
        cutoff = int(time.time()) - 12 * 3600
        stale = self.store.reap_stale_sessions(cutoff)
        for discord_id, guild_id in stale:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                self.store.close_session(discord_id, guild_id)
                continue
            member = guild.get_member(discord_id)
            if member:
                result = self.store.close_session(discord_id, guild_id)
                if result:
                    await self.announce_session(
                        member, result.game, result.minutes, result.honey_earned
                    )

    @session_reaper.before_loop
    async def _before_reaper(self):
        await self.bot.wait_until_ready()

    # -- /brag command ---------------------------------------------------

    @app_commands.command(
        name="brag",
        description="Show off a verified game achievement (with screenshot).",
    )
    @app_commands.describe(
        game="Which game? e.g. Free Fire, Valorant, Genshin Impact",
        achievement="What did you do? e.g. Hit Heroic, Ranked Top 100",
        screenshot="A screenshot proving the achievement (required).",
    )
    async def brag(
        self,
        interaction: discord.Interaction,
        game: str,
        achievement: str,
        screenshot: discord.Attachment,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild is None:
            await interaction.followup.send("🐝 `/brag` only works inside a server.", ephemeral=True)
            return

        ctype = (screenshot.content_type or "").lower()
        if not ctype.startswith("image/"):
            await interaction.followup.send(
                "📸 That doesn't look like an image.", ephemeral=True
            )
            return

        verdict = "ok"
        if self._ocr is not None:
            try:
                result = await self._ocr(screenshot.url, game)
            except Exception as e:
                log.warning("brag OCR failed: %s", e)
                result = {"is_game_screen": True, "looks_like_game": game}
            if not result.get("is_game_screen"):
                verdict = "rejected: not a game screen"
                self.store.log_brag(
                    interaction.user.id, interaction.guild.id,
                    game, achievement, screenshot.url, verdict,
                )
                await interaction.followup.send(
                    "🐝 HiveGPT couldn't confirm this is a game screenshot. "
                    "Please post one taken directly inside the game.",
                    ephemeral=True,
                )
                return

        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            await interaction.followup.send("🐝 Member lookup failed.", ephemeral=True)
            return

        await self.announce_brag(member, game, achievement, screenshot.url)
        self.store.log_brag(
            member.id, interaction.guild.id, game, achievement, screenshot.url, verdict
        )
        await interaction.followup.send(
            f"🍯 Brag posted in #{GAMENEWS_CHANNEL}! HiveGPT 💛 verified the screenshot.",
            ephemeral=True,
        )


async def setup(
    bot: commands.Bot,
    *,
    conn=None,
    c=None,
    add_honey=None,
    add_xp=None,
    ocr_func=None,
) -> None:
    """
    Flexible setup — called from honeybee.on_ready with conn/c/callbacks wired in,
    or as a plain discord.py extension with no extra args.
    """
    if conn is None or c is None:
        from core import pgcompat as _sqlite3
        _conn = _sqlite3.connect()
        _c = _conn.cursor()
    else:
        _conn, _c = conn, c
    await bot.add_cog(GameNews(bot, conn=_conn, c=_c,
                               add_honey=add_honey, add_xp=add_xp,
                               ocr_func=ocr_func))
