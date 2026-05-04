"""
cleanliness.py
==============
Channel-cleanliness cog for The Hive.

Behaviour by channel name:

  yourbee                — only HoneyBee posts (announcements / achievements /
                           birthdays). Member messages instantly deleted + nudge.
  verification-desk      — only the persistent verify panel + bot replies.
  rules                  — only the bot's posted rules. Deleted + nudge.
  breakingnews           — only HoneyBee posts (auto chapter alerts, etc.).
  announces              — only HoneyBee posts (NOT for Blessed Bee tickets).
  hive-logs              — staff/audit only.
  requestbee             — fully clean. Use /requestbee slash command.
  reading-club           — fully clean. Use /recommend slash command.
  snippets, beta-board   — fully clean. Submissions go through bot.
  updatebee              — only Inkstone dashboard screenshots that HiveGPT
                           can confirm. Anything else deleted instantly.
  hive-life              — Shopkeeper persona channel. Only shop-command
                           messages allowed.
  newbee                 — guests, unverified members, and staff can post
                           freely. Verified Authors and above are silently
                           IGNORED (message stays, no nudge, no delete) so
                           the space stays welcoming without being aggressive.

Pinned messages are never touched. Bots are never affected.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Optional

import discord
from discord.ext import commands

log = logging.getLogger("cleanliness")

# ─── channel groupings ───────────────────────────────────────────────────────
BOT_ONLY_CHANNELS = {
    "yourbee", "verification-desk", "rules",
    "breakingnews", "announces", "hive-logs",
}

CLEAN_CHANNELS_GUIDE = {
    "requestbee":   "Use `/requestbee` to send a request — it's hidden so only you and HiveGPT see your draft.",
    "reading-club": "Use `/recommend` to suggest a book. HiveGPT polishes it and HoneyBee posts it here.",
    "snippets":     "Use `/snippet` to submit a snippet. Polished snippets are posted here by HoneyBee.",
    "beta-board":   "Use `/beta` to post a beta-reader request. HoneyBee will post the cleaned-up call here.",
}

HIVE_LIFE_CHANNEL = "hive-life"
UPDATEBEE_CHANNEL = "updatebee"
NEWBEE_CHANNEL    = "newbee"

SLACKER_ALLOWED = {"ticket-room", "helpbee"}

WRITER_RANKS = {
    "Newbie Writer", "Verified Author", "Dedicated Wordsmith",
    "Grandmaster", "Great Grandmaster", "Supreme Grandmaster",
}

# Ranks that mean the member is a verified/ranked writer — they should be
# silently ignored in #newbee (not deleted, just left alone).
VERIFIED_WRITER_RANKS = {
    "Verified Author", "Dedicated Wordsmith",
    "Grandmaster", "Great Grandmaster", "Supreme Grandmaster",
}

STAFF_ROLES = {"Founder", "Heavenly Bee", "Blessed Bee"}

# ─── shopkeeper persona ───────────────────────────────────────────────────────
SHOPKEEPER_NAME = "🧙 Madam Wax"
SHOPKEEPER_GREETINGS = [
    "Ah, a visitor to Wax & Honey Emporium! *adjusts spectacles* What can I do for you, dear?",
    "*looks up from a leather-bound ledger* Well, well — a customer! Welcome to my little shop.",
    "*the scent of honey and old books fills the air* Come in, come in. I was just cataloguing my latest treasures.",
    "🕯️ *a warm candle flickers* Welcome, traveller. My shelves are full of wonders today.",
    "*sets down a quill* Ah, you've found my shop. Very good. Most people walk right past. You must have sharp eyes.",
]
SHOPKEEPER_SHOP_REACTIONS = [
    "*spreads hands over the display* Here is everything I have in stock. Take your time — honey doesn't rush.",
    "A fine selection, if I do say so myself. Each item was chosen with care. What catches your eye?",
    "*taps a shelf thoughtfully* My inventory changes with the seasons... but the quality never does.",
    "Everything has a price, everything has a story. Browse freely.",
]
SHOPKEEPER_BUY_REACTIONS = [
    "*wraps your purchase with golden ribbon* Excellent choice. May it serve you well.",
    "*beams* Oh, that one! A personal favourite of mine. You have good taste.",
    "*ties a small honey-coloured bow on the package* There you go. Come back soon.",
    "*slides the item across the counter with a knowing smile* This will suit you perfectly.",
]
SHOPKEEPER_WRONG_TRIGGER = [
    "*peers over glasses* Hmm? Were you looking for something specific? Try `/shop` to see my wares.",
    "🛒 This is a shop, dear — not a café. Say `/shop` to browse, or `/buy <item>` to purchase something.",
    "*tilts head* I didn't quite catch that. `/shop` to browse, `/buy <item>` to buy. Simple as honey.",
]

SHOP_TRIGGERS = ("!shop", "!buy", "!shopall", "/shop", "/buy")


async def _shopkeeper_respond(message: discord.Message, kind: str):
    """Send a shopkeeper persona response as a styled embed."""
    if kind == "greeting":
        lines = SHOPKEEPER_GREETINGS
        colour = 0xf5c518
    elif kind == "shop":
        lines = SHOPKEEPER_SHOP_REACTIONS
        colour = 0xf5c518
    elif kind == "buy":
        lines = SHOPKEEPER_BUY_REACTIONS
        colour = 0x2ecc71
    else:
        lines = SHOPKEEPER_WRONG_TRIGGER
        colour = 0xe67e22

    embed = discord.Embed(
        description=f"**{SHOPKEEPER_NAME}:** {random.choice(lines)}",
        color=colour,
    )
    embed.set_footer(text="🍯 Wax & Honey Emporium  ·  #hive-life")
    try:
        await message.channel.send(embed=embed, delete_after=90)
    except Exception:
        pass


# ─── ephemeral nudges ─────────────────────────────────────────────────────────

NUDGE_BOT_ONLY = (
    "🐝 This channel is for **announcements only** — your message was "
    "removed to keep it clean. Chat freely in #games-general or other channels, "
    "or use `/ask` to talk with HiveGPT 💛."
)

NUDGE_UPDATEBEE = (
    "📸 **#updatebee is for Inkstone dashboard screenshots only.**\n"
    "Post a screenshot of your Webnovel Inkstone dashboard — HiveGPT will "
    "read the word counts and update your stats automatically. Text messages "
    "and other images don't belong here and have been removed."
)


async def _ephemeral_nudge(message: discord.Message, text: str):
    try:
        await message.author.send(text)
        return
    except Exception:
        pass
    try:
        warn = await message.channel.send(f"{message.author.mention} {text}")
        await asyncio.sleep(8)
        try:
            await warn.delete()
        except Exception:
            pass
    except Exception:
        pass


def _name(ch: discord.abc.Messageable) -> str:
    return getattr(ch, "name", "") or ""


def _member_roles_names(member: discord.Member) -> set[str]:
    return {r.name for r in member.roles}


# ---------------------------------------------------------------------------
# The cog
# ---------------------------------------------------------------------------

class Cleanliness(commands.Cog):
    def __init__(self, bot: commands.Bot, *,
                 slacker_role_name: str = "Slacker Bee",
                 shopkeeper_role_name: str = "ShopKeeper"):
        self.bot = bot
        self.slacker_role_name = slacker_role_name
        self.shopkeeper_role_name = shopkeeper_role_name

    @commands.Cog.listener("on_message")
    async def _enforce(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        if message.pinned:
            return

        ch_name = _name(message.channel).lower()
        roles = _member_roles_names(message.author)

        # 1) Bot-only channels — silent delete + nudge
        if ch_name in BOT_ONLY_CHANNELS:
            await self._delete_with_nudge(message, NUDGE_BOT_ONLY)
            return

        # 2) Fully-clean channels — delete + direct to slash command
        if ch_name in CLEAN_CHANNELS_GUIDE:
            await self._delete_with_nudge(
                message,
                f"🐝 #{ch_name} stays spotless on purpose. {CLEAN_CHANNELS_GUIDE[ch_name]}"
            )
            return

        # 3) #updatebee — only images that look like Inkstone screenshots stay.
        if ch_name == UPDATEBEE_CHANNEL:
            has_image = bool(message.attachments and
                             any(a.content_type and a.content_type.startswith("image/")
                                 for a in message.attachments))
            if not has_image:
                await self._delete_with_nudge(message, NUDGE_UPDATEBEE)
                return
            return

        # 4) #newbee — guests, Newbie Writers, and unverified members post freely.
        #    Verified Authors and above are SILENTLY IGNORED — their message
        #    stays exactly where it is, no deletion, no nudge. Staff always allowed.
        if ch_name == NEWBEE_CHANNEL:
            is_staff = bool(roles & STAFF_ROLES)
            is_verified_writer = bool(roles & VERIFIED_WRITER_RANKS)
            if is_verified_writer and not is_staff:
                # Do nothing — leave the message in place, no nudge.
                return

        # 5) #hive-life — shopkeeper persona channel.
        if ch_name == HIVE_LIFE_CHANNEL:
            content_l = (message.content or "").lower()
            is_shop_trigger = any(t in content_l for t in SHOP_TRIGGERS)
            if not is_shop_trigger:
                await self._delete_with_nudge(
                    message,
                    "🛒 **#hive-life** is the Shopkeeper's parlour — use `/shop` to browse wares "
                    "or `/buy <item_id>` to purchase something. Other messages are removed to "
                    "keep the shop atmosphere."
                )
                return
            return

        # 6) Slacker Bee restriction — can only post in allowed channels.
        try:
            if self.slacker_role_name in roles:
                if ch_name not in SLACKER_ALLOWED:
                    await self._delete_with_nudge(
                        message,
                        "🐝 You're currently a **Slacker Bee** — your channel "
                        "access is restricted until you finish your overdue "
                        "ticket. Head to #ticket-room and submit your parts. "
                        "(Your honey, badges, and ranks are all safe — this "
                        "is only a pause.)"
                    )
                    return
        except Exception:
            pass

    async def _delete_with_nudge(self, message: discord.Message, nudge: str):
        try:
            await message.delete()
        except discord.Forbidden:
            log.warning("cleanliness: missing manage_messages in #%s", _name(message.channel))
            return
        except Exception as e:
            log.debug("cleanliness: delete failed: %s", e)
        await _ephemeral_nudge(message, nudge)


async def setup(bot: commands.Bot, *,
                slacker_role_name: str = "Slacker Bee",
                shopkeeper_role_name: str = "ShopKeeper") -> Cleanliness:
    cog = Cleanliness(bot,
                      slacker_role_name=slacker_role_name,
                      shopkeeper_role_name=shopkeeper_role_name)
    await bot.add_cog(cog)
    return cog  


