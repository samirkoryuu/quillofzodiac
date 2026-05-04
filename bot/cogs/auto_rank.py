"""
cogs/auto_rank.py
=================
Discord cog wrapper for core.auto_rank.

Handles all discord.py-specific work:
  - Listening to #updatebee messages (OCR screenshot trigger)
  - !rankcheck command
  - Role swapping (add/remove discord roles)
  - Announcing rank-ups to the right channel

Pure rank evaluation logic lives in core/auto_rank.py.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import pgcompat as sqlite3
from core.auto_rank import AutoRankStore

log = logging.getLogger("cogs.auto_rank")

UPDATEBEE_CHANNEL    = os.environ.get("UPDATEBEE_CHANNEL",    "update-ocr")
RANK_ANNOUNCE_CHANNEL = os.environ.get("RANK_ANNOUNCE_CHANNEL", "yourbee")
STAFF_PING_CHANNEL   = os.environ.get("STAFF_PING_CHANNEL",   "hive-logs")
RANKCHECK_COOLDOWN_DAYS = int(os.environ.get("RANKCHECK_COOLDOWN_DAYS", "3"))


class AutoRank(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        *,
        conn,
        c,
        rank_tiers: list,
        tier_for,
        award_badge=None,
        announce_badge=None,
        ocr_func=None,
        dashboard_sync_member: Optional[Callable[[discord.Member], Awaitable[None]]] = None,
    ):
        self.bot = bot
        self.award_badge = award_badge
        self.announce_badge = announce_badge
        self.ocr_func = ocr_func
        self.dashboard_sync_member = dashboard_sync_member
        self.store = AutoRankStore(conn, c, rank_tiers, tier_for)
        self.rank_names = self.store.rank_names
        self.tier_emoji = self.store.tier_emoji
        self.tier_index = self.store.tier_index
        # Screenshot dedup: maps discord_id → (attachment_url, seen_at_unix)
        # Prevents re-OCR when a member re-uploads the same image within the
        # dedup window (e.g. accidentally posting twice).
        self._ocr_seen: dict[int, tuple[str, float]] = {}
        self._ocr_dedup_ttl: float = float(
            os.environ.get("OCR_DEDUP_TTL_SECONDS", "300")  # 5 minutes default
        )
        self.rank_recheck_loop.start()
        self.presence_check_loop.start()

    def cog_unload(self):
        self.rank_recheck_loop.cancel()
        self.presence_check_loop.cancel()

    @tasks.loop(minutes=2)
    async def presence_check_loop(self):
        """Checks for online users who have pending rank-up announcements."""
        for guild in self.bot.guilds:
            try:
                pending = self.store.list_pending_announcements(guild.id)
            except Exception:
                continue
            if not pending:
                continue
                
            for p in pending:
                member = guild.get_member(p["discord_id"])
                if not member:
                    continue
                    
                # Check status: online or idle (ignore offline/invisible)
                if member.status in (discord.Status.online, discord.Status.idle):
                    log.info("Announcing pending rank-up for %s (now %s)", member, member.status)
                    try:
                        await self._announce_rank(
                            member, guild, p["old_rank"], p["new_rank"],
                            p["total_words"], p["total_chapters"]
                        )
                        self.store.clear_pending_announcement(p["id"])
                    except Exception as e:
                        log.warning("failed to announce pending rankup: %s", e)

    # -- discord helpers -------------------------------------------------

    def _current_rank(self, member: discord.Member) -> Optional[str]:
        held = [r.name for r in member.roles if r.name in self.tier_index]
        if not held:
            return None
        held.sort(key=lambda n: self.tier_index[n])
        return held[0]

    def _wn_chapter_total(self, discord_id: int) -> tuple[int, dict[str, int]]:
        cog = self.bot.get_cog("WebnovelTracker")
        if not cog:
            return 0, {}
        try:
            books = cog.store.books_for_discord(discord_id)
        except Exception:
            return 0, {}
        per_title: dict[str, int] = {}
        for b in books:
            title = (b["title"] or "").lower().strip()
            ch = int(b["chapter_count"] or 0)
            if title:
                per_title[title] = max(per_title.get(title, 0), ch)
        return sum(per_title.values()), per_title

    async def _swap_role(self, member: discord.Member, new_rank: str) -> bool:
        guild = member.guild
        new_role = discord.utils.get(guild.roles, name=new_rank)
        if new_role is None:
            log.warning("Rank role missing on guild: %s", new_rank)
            return False
        for rn in self.rank_names:
            if rn == new_rank:
                continue
            r = discord.utils.get(guild.roles, name=rn)
            if r and r in member.roles:
                try:
                    await member.remove_roles(r, reason="auto-rank swap")
                except Exception as e:
                    log.warning("remove_roles failed: %s", e)
        try:
            await member.add_roles(new_role, reason="auto-rank")
        except Exception as e:
            log.warning("add_roles failed: %s", e)
            return False
        
        # Nickname update
        try:
            emo = self.tier_emoji.get(new_rank, "🐝")
            current_nick = member.display_name
            # Remove existing rank emojis from nick if possible
            clean_nick = re.sub(r"^[^\w\s]+\s*", "", current_nick).strip()
            new_nick = f"{emo} {clean_nick}"
            if len(new_nick) <= 32:
                await member.edit(nick=new_nick)
        except Exception as e:
            log.debug("nickname update failed (likely missing perms): %s", e)
            
        return True

    async def _announce_rank(self, member, guild, old_rank, new_rank, words, chs):
        ch = (
            discord.utils.get(guild.text_channels, name=RANK_ANNOUNCE_CHANNEL)
            or discord.utils.get(guild.text_channels, name="announcements")
            or discord.utils.get(guild.text_channels, name="general")
        )
        if not ch:
            return
        emo = self.tier_emoji.get(new_rank, "🐝")
        old_emo = self.tier_emoji.get(old_rank or "Newbie Writer", "🐝")
        embed = discord.Embed(
            title=f"{emo} Rank up!",
            description=(
                f"{member.mention} just promoted from "
                f"**{old_emo} {old_rank or 'Newbie Writer'}** to "
                f"**{emo} {new_rank}**!\n\n"
                f"📚 **{chs:,}** chapters · ✍️ **{words:,}** words"
            ),
            color=0xf1c40f,
        )
        embed.set_footer(text="Verified automatically from Webnovel + Inkstone screenshot")
        try:
            await ch.send(embed=embed)
        except Exception as e:
            log.warning("rank announce failed: %s", e)

    async def evaluate_and_rank(
        self, member: discord.Member, guild: discord.Guild,
        basis: str, *, dry_run: bool = False,
    ) -> dict:
        wn_total, wn_per_book = self._wn_chapter_total(member.id)
        verdict = self.store.eligible_rank(member.id, guild.id, wn_total, wn_per_book)
        eligible_rank = verdict["eligible_rank"]
        current_rank = self._current_rank(member)
        promoted = False

        cur_idx = self.tier_index.get(current_rank, len(self.store.RANK_TIERS))
        new_idx = self.tier_index.get(eligible_rank, len(self.store.RANK_TIERS))

        if (not dry_run) and new_idx < cur_idx and eligible_rank != current_rank:
            ok = await self._swap_role(member, eligible_rank)
            if ok:
                self.store.record_audit(
                    member.id, guild.id, current_rank, eligible_rank,
                    verdict["total_words"], verdict["total_chapters"], basis,
                )
                
                # Presence-aware announcement
                if member.status in (discord.Status.online, discord.Status.idle) or basis == "auto-screenshot":
                    # If they just uploaded a screenshot, they are obviously active
                    await self._announce_rank(
                        member, guild, current_rank, eligible_rank,
                        verdict["total_words"], verdict["total_chapters"],
                    )
                else:
                    # Queue for later when they come online
                    log.info("Queuing pending rank-up announcement for offline user: %s", member)
                    self.store.queue_pending_announcement(
                        member.id, guild.id, current_rank, eligible_rank,
                        verdict["total_words"], verdict["total_chapters"]
                    )

                if eligible_rank == "Supreme Godscribe" and self.award_badge:
                    try:
                        if self.award_badge(member.id, guild.id, "supreme") and self.announce_badge:
                            # Only announce badge if they are online (matches logic above)
                            if member.status in (discord.Status.online, discord.Status.idle):
                                await self.announce_badge(member, guild, "supreme")
                    except Exception as e:
                        log.warning("supreme badge: %s", e)
                
                # DM notification (always send immediate DM)
                try:
                    emo = self.tier_emoji.get(eligible_rank, "🐝")
                    await member.send(
                        f"🎉 **Congratulations!** You've been promoted to **{emo} {eligible_rank}** in **{guild.name}**!\n"
                        f"Your writing journey has reached a new milestone: **{verdict['total_chapters']:,}** chapters and **{verdict['total_words']:,}** words total.\n"
                        "Keep up the amazing work! 🐝🍯"
                    )
                except Exception:
                    log.debug("failed to DM rank-up to %s", member.display_name)
                    
                promoted = True

        if (not dry_run) and self.dashboard_sync_member:
            try:
                await self.dashboard_sync_member(member)
            except Exception as e:
                log.debug("dashboard sync after rank eval: %s", e)

        verdict["current_rank"] = current_rank
        verdict["promoted"] = promoted
        return verdict

    @tasks.loop(hours=24)
    async def rank_recheck_loop(self):
        """Routine check for all writers to see if they eligible for a rank up via Webnovel-only data."""
        log.info("Starting 24h rank recheck loop...")
        
        writers_to_process = []
        with sqlite3.connect() as conn:
            c = conn.cursor()
            try:
                c.execute("ALTER TABLE writers ADD COLUMN IF NOT EXISTS guild_id BIGINT")
                conn.commit()
            except:
                pass
            writers_to_process = c.execute("SELECT discord_id, guild_id FROM writers").fetchall()
        
        for row in writers_to_process:
            uid, gid = row
            if not gid: continue
            guild = self.bot.get_guild(gid)
            if not guild: continue
            member = guild.get_member(uid)
            if not member: continue
            try:
                await self.evaluate_and_rank(member, guild, basis="24h-routine")
            except Exception as e:
                log.warning("24h recheck failed for %s: %s", uid, e)
        log.info("24h rank recheck loop finished.")

    @rank_recheck_loop.before_loop
    async def before_rank_recheck(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="ranks", description="View all available writer ranks and their requirements")
    async def ranks(self, interaction: discord.Interaction):
        """Display the nicely formatted list of all 27 ranks."""
        embed = discord.Embed(
            title="🖋️ Writer Rank System",
            description=(
                "Rank up by writing more! We track your chapters via Webnovel and your "
                "word counts via Inkstone screenshots in #update-ocr.\n\n"
                "**The Rank Ladder:**"
            ),
            color=0x2ecc71
        )
        
        # Split 27 ranks into columns or chunks to fit in embed fields
        chunks = [self.store.RANK_TIERS[i:i+9] for i in range(0, len(self.store.RANK_TIERS), 9)]
        for i, chunk in enumerate(chunks):
            lines = []
            for name, ch, wd, emo, *_ in chunk:
                lines.append(f"{emo} **{name}**\n└ `{ch:,}` Ch · `{wd:,}` Words")
            embed.add_field(name="\u200b", value="\n".join(lines), inline=True)
            
        embed.set_footer(text="Higher ranks grant unique badges and perks in the Hive! 🐝")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="myrank", description="Check your current rank and progress toward the next tier")
    async def myrank(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        verdict = await self.evaluate_and_rank(target, interaction.guild, basis="myrank", dry_run=True)
        
        cur = verdict["current_rank"] or "Newbie Writer"
        cur_idx = self.tier_index.get(cur, len(self.store.RANK_TIERS) - 1)
        cur_emo = self.tier_emoji.get(cur, "🪶")
        
        next_idx = cur_idx - 1
        next_tier = self.store.RANK_TIERS[next_idx] if next_idx >= 0 else None
        
        embed = discord.Embed(title=f"📊 Rank Card: {target.display_name}", color=0xf1c40f)
        embed.set_thumbnail(url=target.display_avatar.url if target.display_avatar else None)
        
        embed.add_field(name="Current Rank", value=f"{cur_emo} **{cur}**", inline=False)
        embed.add_field(name="Total Stats", value=f"📚 **{verdict['total_chapters']:,}** chapters\n✍️ **{verdict['total_words']:,}** words", inline=True)
        
        if next_tier:
            name, min_ch, min_wd, emo, *_ = next_tier
            ch_needed = max(0, min_ch - verdict['total_chapters'])
            wd_needed = max(0, min_wd - verdict['total_words'])
            
            progress_str = f"Next: {emo} **{name}**\n"
            if ch_needed > 0: progress_str += f"└ Need **{ch_needed:,}** more chapters\n"
            if wd_needed > 0: progress_str += f"└ Need **{wd_needed:,}** more words"
            if ch_needed == 0 and wd_needed == 0:
                progress_str = f"✅ You qualify for **{emo} {name}**! Post a screenshot in #update-ocr to refresh."
                
            embed.add_field(name="Next Tier Progress", value=progress_str, inline=False)
        else:
            embed.add_field(name="Next Tier Progress", value="⭐ **SUPREME GODSCRIBE REACHED** ⭐\nYou have reached the peak of the mountain!", inline=False)
            
        await interaction.response.send_message(embed=embed)

    async def on_wn_change(self, discord_id, guild_id, new_chapters, new_books):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        member = guild.get_member(discord_id)
        if not member:
            return
        try:
            await self.evaluate_and_rank(member, guild, basis="auto-poll")
        except Exception as e:
            log.warning("on_wn_change rank check failed: %s", e)

    # -- #updatebee listener --------------------------------------------

    @commands.Cog.listener("on_message")
    async def updatebee_listener(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.channel.name != UPDATEBEE_CHANNEL:
            return
        if not message.attachments:
            try:
                await message.reply(
                    "📸 Post a **screenshot of your Webnovel author dashboard** here "
                    "and I'll auto-evaluate your rank.\n"
                    "Text-only messages are removed to keep the channel clean."
                )
                await message.delete(delay=5)
            except Exception:
                pass
            return

        image = next(
            (a for a in message.attachments
             if (a.content_type or "").startswith("image/")
             or a.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))),
            None,
        )
        if not image:
            return

        # ── Screenshot dedup ────────────────────────────────────────────────
        # If the same user posts the identical image URL within the dedup
        # window, skip re-OCR and tell them politely.
        now_ts = time.time()
        prev = self._ocr_seen.get(message.author.id)
        if prev is not None:
            prev_url, prev_ts = prev
            if prev_url == image.url and (now_ts - prev_ts) < self._ocr_dedup_ttl:
                remaining = int(self._ocr_dedup_ttl - (now_ts - prev_ts))
                try:
                    await message.reply(
                        f"🔁 I already read that exact screenshot — results are still fresh "
                        f"(dedup window expires in {remaining}s). "
                        "Post a new screenshot or wait a bit before resubmitting."
                    )
                    await message.add_reaction("♻️")
                except Exception:
                    pass
                return
        # Record this URL as the latest OCR for this user
        self._ocr_seen[message.author.id] = (image.url, now_ts)

        try:
            await message.add_reaction("👀")
        except Exception:
            pass

        if self.ocr_func is None:
            await message.reply("⚠️ OCR is not configured. Ping a Founder.")
            return

        try:
            ocr = await self.ocr_func(image.url)
        except Exception as e:
            log.warning("OCR call failed: %s", e)
            ocr = None

        if not ocr:
            try:
                log.info("OCR failed for %s using bot %s", message.author, self.bot.user)
                await message.reply(
                    "⚠️ I couldn't read that screenshot — every vision key "
                    "either failed or is out of quota. Try a clearer image, "
                    "or use `/rankcheck` to ask staff to verify manually."
                )
                await message.add_reaction("❌")
            except Exception:
                pass
            return

        snap = None
        wn = self.bot.get_cog("WebnovelTracker")
        if wn:
            try:
                snap = await wn.force_live_snapshot(message.author.id)
            except Exception as e:
                log.warning("force_live_snapshot failed: %s", e)

        pen_row = self.store.c.execute(
            "SELECT pen_name FROM writers WHERE discord_id=?",
            (message.author.id,),
        ).fetchone()
        pen_name = pen_row[0] if pen_row else None
        cross_lines = self.store.crosscheck_ocr_vs_snapshot(ocr, snap, pen_name)

        ts = int(time.time())
        books = ocr.get("books", [])
        
        # REQUIRE live check for auto-rank
        if snap is None:
            details_msg = "\n".join(lines) + "\n\n⚠️ **Rank-up Blocked:** I found your stats, but I cannot verify them against a public Webnovel page. Please use `/track <url>` first so I can sync your live stats before we apply this rank."
            try:
                await message.reply(
                    "✅ **OCR Analysis Complete!** (Public Verification Required)",
                    view=OCRResultView(message.author.id, details_msg)
                )
                await message.remove_reaction("👀", self.bot.user)
                await message.add_reaction("⚠️")
            except Exception:
                pass
            return

        if books:
            self.store.record_word_counts(message.author.id, message.guild.id, books, ts)
        else:
            tot = ocr.get("totals") or {}
            self.store.record_word_counts(
                message.author.id, message.guild.id,
                [{"title": "(totals only)",
                  "chapters": int(tot.get("chapters") or 0),
                  "words": int(tot.get("words") or 0)}],
                ts,
            )

        verdict = await self.evaluate_and_rank(
            message.author, message.guild, basis="auto-screenshot",
        )

        emo = self.tier_emoji.get(verdict["eligible_rank"], "🐝")
        cur = verdict["current_rank"] or "Newbie Writer"
        cur_emo = self.tier_emoji.get(cur, "🐝")
        lines = [
            f"📊 **Read your screenshot** — totals: "
            f"**{verdict['total_chapters']:,}** chapters · "
            f"**{verdict['total_words']:,}** words."
        ]
        if cross_lines:
            lines.append("")
            lines.append("**Live Webnovel check:**")
            lines.extend(cross_lines)
        if ocr.get("ranking"):
            lines.append(f"🏷 Webnovel ranking detected: _{ocr['ranking']}_")
        if verdict["promoted"]:
            lines.append(
                f"🎉 You qualify for **{emo} {verdict['eligible_rank']}** "
                "— role applied automatically!"
            )
        elif verdict["eligible_rank"] == cur:
            lines.append(f"You're correctly ranked as **{cur_emo} {cur}**.")
        else:
            lines.append(
                f"Your current role is **{cur_emo} {cur}** — that matches your numbers. "
                "Keep writing! 🐝"
            )

        class OCRResultView(discord.ui.View):
            def __init__(self, uploader_id, details):
                super().__init__(timeout=600)
                self.uploader_id = uploader_id
                self.details = details

            @discord.ui.button(label="🔍 View My Results", style=discord.ButtonStyle.secondary)
            async def view_results(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != self.uploader_id:
                    await interaction.response.send_message("❌ Only the uploader can view these results.", ephemeral=True)
                    return
                await interaction.response.send_message(self.details, ephemeral=True)

        details_msg = "\n".join(lines) + "\n\n_If you are eligible for a rank up, it has been applied or will be processed by the next cycle!_"
        try:
            await message.reply(
                "✅ **OCR Analysis Complete!** Only you can see the detailed counts below.",
                view=OCRResultView(message.author.id, details_msg)
            )
            await message.remove_reaction("👀", self.bot.user)
            await message.add_reaction("✅")
        except Exception:
            pass

    # -- !rankcheck -----------------------------------------------------

    @commands.command(name="rankcheck", aliases=["checkrank"])
    async def cmd_rankcheck(self, ctx: commands.Context):
        if not ctx.guild:
            return await ctx.reply("Use this command in the server, not in DM.")

        ok, wait_secs = self.store.cooldown_ok(ctx.author.id)
        if not ok:
            wait_h = wait_secs // 3600
            wait_d = wait_h // 24
            left = (
                f"{wait_d} day{'s' if wait_d != 1 else ''}"
                if wait_d else
                f"{wait_h} hour{'s' if wait_h != 1 else ''}"
            )
            return await ctx.reply(
                f"⏳ You can run `!rankcheck` again in **{left}**. "
                f"In the meantime, post a fresh Inkstone screenshot in #{UPDATEBEE_CHANNEL}."
            )

        verdict = await self.evaluate_and_rank(ctx.author, ctx.guild, basis="rankcheck")
        self.store.mark_rankcheck(ctx.author.id)

        emo = self.tier_emoji.get(verdict["eligible_rank"], "🐝")
        cur = verdict["current_rank"] or "Newbie Writer"
        cur_emo = self.tier_emoji.get(cur, "🐝")

        if verdict["promoted"]:
            return await ctx.reply(f"🎉 Promoted to **{emo} {verdict['eligible_rank']}**!")

        if verdict["eligible_rank"] == cur:
            return await ctx.reply(
                f"✅ You're already at the rank your numbers support: "
                f"**{cur_emo} {cur}** ({verdict['total_chapters']:,} chapters · "
                f"{verdict['total_words']:,} words). "
                f"Post a new Inkstone screenshot in #{UPDATEBEE_CHANNEL} once you've grown more!"
            )

        staff_ch = (
            discord.utils.get(ctx.guild.text_channels, name=STAFF_PING_CHANNEL)
            or discord.utils.get(ctx.guild.text_channels, name="hive-logs")
            or discord.utils.get(ctx.guild.text_channels, name="hivebee-logs")
        )
        if staff_ch:
            try:
                await staff_ch.send(
                    f"🛎 **Manual rank check** — {ctx.author.mention}\n"
                    f"Currently: **{cur_emo} {cur}**\n"
                    f"Eligible: **{emo} {verdict['eligible_rank']}**\n"
                    f"Totals: **{verdict['total_chapters']:,}** chapters · "
                    f"**{verdict['total_words']:,}** words"
                )
            except Exception:
                pass
        await ctx.reply(
            f"📋 Numbers checked. Your eligible rank is **{emo} {verdict['eligible_rank']}** "
            f"but the role couldn't be applied automatically. Staff have been pinged."
        )


async def setup(bot: commands.Bot) -> None:
    """Minimal setup for standalone loading (uses defaults)."""
    await bot.add_cog(AutoRank(bot, conn=None, c=None, rank_tiers=[], tier_for=lambda w, c: ("Newbie Writer", "🐝")))


async def setup_cog(
    bot: commands.Bot,
    *,
    conn,
    c,
    rank_tiers: list,
    tier_for,
    award_badge=None,
    announce_badge=None,
    ocr_func=None,
    dashboard_sync_member=None,
) -> None:
    """Full setup called from honeybee.on_ready with all dependencies wired in."""
    await bot.add_cog(AutoRank(
        bot,
        conn=conn,
        c=c,
        rank_tiers=rank_tiers,
        tier_for=tier_for,
        award_badge=award_badge,
        announce_badge=announce_badge,
        ocr_func=ocr_func,
        dashboard_sync_member=dashboard_sync_member,
    ))
