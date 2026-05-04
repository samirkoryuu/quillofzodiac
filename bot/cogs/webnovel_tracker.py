"""
cogs/webnovel_tracker.py
========================
Discord cog wrapper for core.webnovel_tracker.

Handles all discord.py-specific work:
  - Background poll + summary loops
  - /track /untrack /writers /progress /wnforce /wnsummary /wnstatus commands
  - Sending new-chapter and new-book embeds to the announcement channel

Pure scraping/DB logic lives in core/webnovel_tracker.py.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.webnovel_tracker import (
    HTTP_TIMEOUT,
    POLL_INTERVAL_HOURS,
    ANNOUNCE_CHANNEL_ID,
    SUMMARY_CHANNEL_ID,
    SUMMARY_HOUR_UTC,
    REDISCOVER_HOURS,
    DB_PATH,
    SCRAPER_API_URL,
    Book,
    TrackerStore,
    WebnovelClient,
    build_default_client,
)

log = logging.getLogger("cogs.webnovel_tracker")


class WebnovelTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = TrackerStore(DB_PATH)
        self._http = aiohttp.ClientSession(timeout=HTTP_TIMEOUT)
        self.rotating = build_default_client(session=self._http)
        self.client = WebnovelClient(self.rotating)
        self._lock = asyncio.Lock()
        self.poll_loop.change_interval(hours=POLL_INTERVAL_HOURS)
        self.poll_loop.start()
        self.summary_loop.start()
        if SCRAPER_API_URL:
            self.scraper_keepalive.start()
            log.info("Scraper keepalive loop started → %s", SCRAPER_API_URL)

    async def cog_unload(self):
        self.poll_loop.cancel()
        self.summary_loop.cancel()
        if self.scraper_keepalive.is_running():
            self.scraper_keepalive.cancel()
        for b in self.rotating.backends:
            close = getattr(b, "close", None)
            if close:
                try:
                    await close()
                except Exception:
                    pass
        await self._http.close()

    async def force_live_snapshot(self, discord_id: int):
        row = self.store.get_writer_by_discord(discord_id)
        if not row:
            return None
        profile_id = row["profile_id"]
        snap = await self.client.fetch_writer_snapshot(profile_id)
        self.store.mark_discovered(profile_id)
        for b in snap.books:
            self.store.upsert_book(profile_id, b)
        return snap

    async def _announce(self, channel_id, content=None, embed=None):
        if not channel_id:
            return
        ch = self.bot.get_channel(channel_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(channel_id)
            except Exception as e:
                log.warning("Cannot resolve channel %s: %s", channel_id, e)
                return
        try:
            await ch.send(content=content, embed=embed)
        except Exception as e:
            log.warning("Failed to send to channel %s: %s", channel_id, e)

    async def _poll_one(self, writer_row, announce: bool = True):
        import time as _time
        profile_id = writer_row["profile_id"]
        last_disc = int(writer_row["last_discovered_at"] or 0)
        age_h = (_time.time() - last_disc) / 3600.0 if last_disc else float("inf")
        known = self.store.list_books(profile_id) if last_disc else []

        if known and age_h < REDISCOVER_HOURS:
            known_books = [Book(book_id=r["book_id"], title=r["title"],
                               url=r["url"], chapter_count=r["chapter_count"] or 0,
                               views=r["views"], genre=r["genre"]) for r in known]
            snap = await self.client.refresh_known_books(profile_id, known_books)
        else:
            snap = await self.client.fetch_writer_snapshot(profile_id)
            self.store.mark_discovered(profile_id)

        new_chapter_events = []
        new_book_events = []
        for b in snap.books:
            prev, now, was_new = self.store.upsert_book(writer_row["profile_id"], b)
            if was_new and last_disc:
                new_book_events.append(b)
            elif prev and now > prev:
                new_chapter_events.append((b, prev))

        if announce:
            for b in new_book_events:
                embed = discord.Embed(
                    title=f"📕 New book published — {b.title}",
                    url=b.url,
                    description=(
                        f"**{writer_row['display_name']}** just published a new book!\n"
                        f"Starting chapters: **{b.chapter_count}**"
                    ),
                    color=0xe67e22,
                    timestamp=datetime.now(timezone.utc),
                )
                if b.genre:
                    embed.add_field(name="Genre", value=b.genre, inline=True)
                await self._announce(ANNOUNCE_CHANNEL_ID, embed=embed)

            for b, prev in new_chapter_events:
                added = b.chapter_count - prev
                embed = discord.Embed(
                    title=f"📖 New chapter on {b.title}",
                    url=b.url,
                    description=(
                        f"**{writer_row['display_name']}** posted "
                        f"**{added} new chapter{'s' if added != 1 else ''}**!\n"
                        f"Total chapters: **{b.chapter_count}** (was {prev})"
                    ),
                    color=0x2ecc71,
                    timestamp=datetime.now(timezone.utc),
                )
                if b.genre:
                    embed.add_field(name="Genre", value=b.genre, inline=True)
                if b.views:
                    embed.add_field(name="Views", value=b.views, inline=True)
                await self._announce(ANNOUNCE_CHANNEL_ID, embed=embed)

        cb = getattr(self.bot, "_wn_change_callback", None)
        if cb and (new_chapter_events or new_book_events):
            try:
                await cb(
                    int(writer_row["discord_id"]),
                    int(writer_row["guild_id"] or 0),
                    new_chapter_events,
                    new_book_events,
                )
            except Exception as e:
                log.warning("wn change callback failed: %s", e)

        return snap, new_chapter_events

    # -- background loops -----------------------------------------------

    @tasks.loop(minutes=10)
    async def scraper_keepalive(self):
        if not SCRAPER_API_URL:
            return
        try:
            async with self._http.get(
                f"{SCRAPER_API_URL}/health",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 200:
                    log.debug("Scraper API keepalive OK (%s)", SCRAPER_API_URL)
                else:
                    log.warning("Scraper API keepalive got HTTP %d", r.status)
        except Exception as e:
            log.warning("Scraper API keepalive failed: %s", e)

    @scraper_keepalive.before_loop
    async def _before_keepalive(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=POLL_INTERVAL_HOURS)
    async def poll_loop(self):
        async with self._lock:
            writers = self.store.list_writers()
            if not writers:
                return
            log.info("Polling %d writers...", len(writers))
            for w in writers:
                try:
                    await self._poll_one(w, announce=True)
                except Exception as e:
                    log.exception("Poll failed for %s: %s", w["profile_id"], e)
                await asyncio.sleep(2)

    @poll_loop.before_loop
    async def _before_poll(self):
        await self.bot.wait_until_ready()
        async with self._lock:
            for w in self.store.list_writers():
                if not self.store.list_books(w["profile_id"]):
                    try:
                        await self._poll_one(w, announce=False)
                    except Exception as e:
                        log.warning("Initial seed failed for %s: %s", w["profile_id"], e)

    @tasks.loop(minutes=30)
    async def summary_loop(self):
        now = datetime.now(timezone.utc)
        if now.hour != SUMMARY_HOUR_UTC:
            return
        marker = self.store.path + f".summary-{now.strftime('%Y%m%d%H')}"
        if os.path.exists(marker):
            return
        try:
            await self._post_daily_summary()
        finally:
            try:
                open(marker, "w").close()
            except Exception:
                pass

    @summary_loop.before_loop
    async def _before_summary(self):
        await self.bot.wait_until_ready()

    async def _post_daily_summary(self):
        writers = self.store.list_writers()
        if not writers:
            return
        embed = discord.Embed(
            title="📚 Daily Writers Progress",
            color=0x3498db,
            timestamp=datetime.now(timezone.utc),
        )
        any_data = False
        for w in writers:
            books = self.store.list_books(w["profile_id"])
            if not books:
                continue
            any_data = True
            lines = [f"• [{b['title']}]({b['url']}) — **{b['chapter_count']}** chapters"
                     for b in books]
            embed.add_field(
                name=w["display_name"],
                value=("\n".join(lines))[:1024] or "_(no books)_",
                inline=False,
            )
        if any_data:
            await self._announce(SUMMARY_CHANNEL_ID, embed=embed)

    # -- commands -------------------------------------------------------

    @app_commands.command(name="track", description="Start tracking a Webnovel writer's books")
    async def cmd_track(self, interaction: discord.Interaction, profile_url: str, member: Optional[discord.Member] = None):
        pid = WebnovelClient.parse_profile_id(profile_url)
        if not pid:
            return await interaction.response.send_message("❌ That doesn't look like a Webnovel profile URL.", ephemeral=True)
            
        target = member or interaction.user
        guild_id = interaction.guild_id or 0
        
        self.store.add_writer(pid, target.id, guild_id, target.display_name)
        await interaction.response.send_message(
            f"✅ Now tracking **{target.display_name}** → "
            f"<https://www.webnovel.com/profile/{pid}>\n"
            "_Fetching their books, hold on..._"
        )
        
        try:
            row = self.store.get_writer_by_discord(target.id)
            snap, _ = await self._poll_one(row, announce=False)
            if snap.books:
                titles = "\n".join(f"• {b.title} ({b.chapter_count} chapters)" for b in snap.books)
                await interaction.followup.send(f"Found **{len(snap.books)}** book(s):\n{titles}")
            else:
                await interaction.followup.send("No authored books found on that profile yet.")
        except Exception as e:
            log.exception("track fetch failed")
            await interaction.followup.send(f"⚠️ Couldn't fetch books right now: `{e}`.")

    @app_commands.command(name="untrack", description="Stop tracking a Webnovel profile")
    async def cmd_untrack(self, interaction: discord.Interaction, profile_url_or_mention: str):
        m = re.match(r"<@!?(\d+)>", profile_url_or_mention)
        if m:
            n = self.store.remove_writer_by_discord(int(m.group(1)))
        else:
            pid = WebnovelClient.parse_profile_id(profile_url_or_mention)
            if not pid:
                return await interaction.response.send_message("❌ Give me a member mention or a Webnovel profile URL.", ephemeral=True)
            n = self.store.remove_writer_by_profile(pid)
            
        await interaction.response.send_message(f"🗑 Removed {n} writer(s).")

    @app_commands.command(name="writers", description="List all tracked writers in the server")
    async def cmd_writers(self, interaction: discord.Interaction):
        rows = self.store.list_writers(interaction.guild_id if interaction.guild else None)
        if not rows:
            return await interaction.response.send_message("Nobody is being tracked yet. Use `/track <profile_url>` to start.")
            
        lines = []
        for r in rows:
            books = self.store.list_books(r["profile_id"])
            lines.append(f"• **{r['display_name']}** — {len(books)} book(s) "
                         f"<https://www.webnovel.com/profile/{r['profile_id']}>")
                         
        await interaction.response.send_message("\n".join(lines)[:2000])

    @app_commands.command(name="progress", description="Check Webnovel progress for yourself or someone else")
    async def cmd_progress(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        row = self.store.get_writer_by_discord(target.id)
        if not row:
            return await interaction.response.send_message(f"❌ {target.display_name} isn't being tracked.", ephemeral=True)
            
        await interaction.response.defer()
        snap, _ = await self._poll_one(row, announce=False)
        
        embed = discord.Embed(
            title=f"{target.display_name}'s Webnovel progress",
            url=snap.profile_url,
            color=0x9b59b6,
        )
        if not snap.books:
            embed.description = "_No authored books found._"
            
        for b in snap.books:
            extra = []
            if b.genre: extra.append(b.genre)
            if b.views: extra.append(f"{b.views} views")
            tag = f" ({', '.join(extra)})" if extra else ""
            embed.add_field(
                name=b.title,
                value=f"**{b.chapter_count}** chapters{tag}\n[Read]({b.url})",
                inline=False,
            )
            
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="mybooks", description="List your tracked Webnovel books")
    async def mybooks(self, interaction: discord.Interaction):
        rows = self.store.list_books_by_discord(interaction.user.id)
        if not rows:
            return await interaction.response.send_message("You aren't tracking any books yet! Use `/track <profile_url>` to start.", ephemeral=True)
            
        lines = [f"📖 **{r['title']}** (`{r['book_id']}`)" for r in rows]
        await interaction.response.send_message(f"📚 **Your Tracked Books:**\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="syncbooks", description="Manually refresh all books from your Webnovel profile")
    async def syncbooks(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        if member and not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ You don't have permission to sync someone else's books.", ephemeral=True)
            
        row = self.store.get_writer_by_discord(target.id)
        if not row:
            return await interaction.response.send_message(f"❌ {target.display_name} isn't being tracked.", ephemeral=True)
            
        await interaction.response.defer()
        snap, _ = await self._poll_one(row, announce=False)
        
        await interaction.followup.send(f"✅ Successfully synced **{len(snap.books)}** book(s) for **{target.display_name}**.")

    @app_commands.command(name="wnforce", description="Admin: Force a Webnovel poll now")
    @app_commands.default_permissions(manage_guild=True)
    async def cmd_force(self, interaction: discord.Interaction):
        await interaction.response.send_message("Running a poll now...", ephemeral=True)
        await self.poll_loop.coro(self)
        await interaction.followup.send("✅ Poll done.")

    @app_commands.command(name="wnsummary", description="Admin: Force post the daily summary now")
    @app_commands.default_permissions(manage_guild=True)
    async def cmd_summary(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._post_daily_summary()
        await interaction.followup.send("✅ Summary posted.")

    @app_commands.command(name="wnstatus", description="Admin: Check scraper backend status")
    @app_commands.default_permissions(manage_guild=True)
    async def cmd_status(self, interaction: discord.Interaction):
        scraper_line = (
            f"Self-hosted scraper: `{SCRAPER_API_URL}` ✅\n"
            if SCRAPER_API_URL
            else "Self-hosted scraper: ❌ not configured (`WN_SCRAPER_API_URL` unset)\n"
        )
        text = (
            f"**Webnovel tracker — backend status**\n"
            f"Poll interval: every {POLL_INTERVAL_HOURS}h · "
            f"Daily summary at {SUMMARY_HOUR_UTC:02d}:00 UTC\n"
            f"{scraper_line}\n"
            f"{self.rotating.status_summary()}\n\n"
            "_Backends are tried top-to-bottom. `selfhosted` uses your own "
            "Playwright service; others are paid API fallbacks._"
        )
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WebnovelTracker(bot))
