"""
requestbee.py
=============
The new universal request hub for The Hive.

Per the Hive's vision, #requestbee is now FULLY clean: every member
message in the channel is deleted on sight (handled by cleanliness.py).
The only way for a member to file a request is the `/requestbee` slash
command — its responses are ALL ephemeral, so the channel stays spotless
for everyone else.

Flow
----
1. Member runs `/requestbee what:"please recheck my rank, I think I'm at
   verified level now"`.
2. HiveGPT classifies the request (rank-recheck, new-chapter, new-book,
   missed-watcher, manual-add, generic) and asks the member ONE
   clarifying question if needed (e.g. "which book?").
3. Member confirms in DM (or via the follow-up ephemeral form).
4. The polished, structured request is dropped into the staff `#requests`
   channel as an embed with a "claim", "approve", "decline" trio of
   buttons. HoneyBee tags whichever staff role applies.
5. The member gets a hidden confirmation in the channel saying their
   request is in the queue.

Nothing the member writes ever appears in #requestbee publicly.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("requestbee")

REQUESTS_CHANNEL = os.environ.get("REQUESTS_CHANNEL", "requests")
REQUESTBEE_CHANNEL = os.environ.get("REQUESTBEE_CHANNEL", "requestbee")
STAFF_ROLE = os.environ.get("REQUESTS_STAFF_ROLE", "Heavenly Bee")


_CLASSIFY_PROMPT = """\
You are HiveGPT, classifying a member's free-text request to the staff
of The Hive (a Discord writers' community).

The known request kinds are:
  rank-recheck       — "I think I qualify for a higher rank, please look at me"
  new-chapter        — "the bot missed my new chapter on book X"
  new-book           — "the bot doesn't know about my new book yet"
  missed-watcher     — "the 12-hour watcher missed my latest update"
  manual-add         — "please manually add this thing for me"
  generic            — anything else

Reply with STRICT JSON only (no prose, no code fences):

{
  "kind": "<one of the kinds above>",
  "summary": "<one polished English sentence describing what they want>",
  "needs_clarification": "<a SHORT question to ask the member, or null if you have everything>",
  "extracted": {
    "book_title": "<book title if mentioned, else null>",
    "chapter_title": "<chapter title if mentioned, else null>",
    "rank_target": "<rank name they think they deserve, or null>"
  }
}

Be conservative. If the member's request is incoherent, return
needs_clarification with a one-sentence question.
"""


# ---------------------------------------------------------------------------
# Persistent buttons for the staff request card
# ---------------------------------------------------------------------------

class StaffActionView(discord.ui.View):
    def __init__(self, member_id: int, request_id: int):
        super().__init__(timeout=None)
        self.member_id = member_id
        self.request_id = request_id

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary,
                       custom_id="rb_claim")
    async def claim(self, inter: discord.Interaction, _btn: discord.ui.Button):
        if not _is_staff(inter.user):
            await inter.response.send_message("Staff only.", ephemeral=True)
            return
        await inter.response.send_message(
            f"✋ Claimed by {inter.user.mention}.", ephemeral=False
        )

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success,
                       custom_id="rb_approve")
    async def approve(self, inter: discord.Interaction, _btn: discord.ui.Button):
        if not _is_staff(inter.user):
            await inter.response.send_message("Staff only.", ephemeral=True)
            return
        # Try to DM the requester.
        try:
            user = await inter.client.fetch_user(self.member_id)
            await user.send(
                f"✅ Your request was approved by {inter.user.display_name} "
                f"in #{inter.channel.name}. The hive moves forward 💛"
            )
        except Exception:
            pass
        await inter.response.send_message(
            f"✅ Approved by {inter.user.mention}.", ephemeral=False
        )

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger,
                       custom_id="rb_decline")
    async def decline(self, inter: discord.Interaction, _btn: discord.ui.Button):
        if not _is_staff(inter.user):
            await inter.response.send_message("Staff only.", ephemeral=True)
            return
        try:
            user = await inter.client.fetch_user(self.member_id)
            await user.send(
                f"⚠️ Your request was declined by {inter.user.display_name}. "
                f"Feel free to ask HiveGPT 💛 to help you rephrase and try again."
            )
        except Exception:
            pass
        await inter.response.send_message(
            f"⛔ Declined by {inter.user.mention}.", ephemeral=False
        )


def _is_staff(user) -> bool:
    member = user if isinstance(user, discord.Member) else None
    if member is None:
        return False
    return any(r.name in (STAFF_ROLE, "Founder", "Heavenly Bee") for r in member.roles)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class RequestBee(commands.Cog):
    def __init__(self, bot: commands.Bot, *, llm_chat=None):
        self.bot = bot
        self._llm_chat = llm_chat

    async def cog_load(self):
        # Persist the staff button view across restarts.
        try:
            self.bot.add_view(StaffActionView(member_id=0, request_id=0))
        except Exception:
            pass

    @app_commands.command(
        name="requestbee",
        description="Send a hidden request to staff in plain English. HiveGPT 💛 will polish it.",
    )
    @app_commands.describe(what="What do you want to ask the hive staff for?")
    async def requestbee(self, inter: discord.Interaction, what: str):
        await inter.response.defer(ephemeral=True, thinking=True)
        if inter.guild is None:
            await inter.followup.send("🐝 `/requestbee` only works in a server.",
                                      ephemeral=True)
            return

        # Classify with HiveGPT
        verdict = await self._classify(what)
        if verdict.get("needs_clarification"):
            # Queue the half-finished request as a follow-up form.
            view = ClarifyView(self, verdict, what)
            await inter.followup.send(
                f"🐝 *(only you can see this)* — {verdict['needs_clarification']}",
                view=view, ephemeral=True,
            )
            return

        await self._post_to_staff(inter, what, verdict)
        await inter.followup.send(
            "🍯 Your request is in the queue — staff have been pinged in "
            f"#{REQUESTS_CHANNEL}. Nothing of yours was posted publicly. "
            "HiveGPT 💛 will DM you the moment it's resolved.",
            ephemeral=True,
        )

    async def _classify(self, text: str) -> dict:
        if not self._llm_chat:
            return {
                "kind": "generic",
                "summary": text.strip()[:280],
                "needs_clarification": None,
                "extracted": {},
            }
        try:
            raw = await self._llm_chat(
                [
                    {"role": "system", "content": _CLASSIFY_PROMPT},
                    {"role": "user", "content": text.strip()},
                ],
                max_tokens=300,
                temperature=0.1,
            )
            raw = (raw or "").strip()
            if raw.startswith("```"):
                import re
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw,
                             flags=re.MULTILINE).strip()
            data = json.loads(raw)
            data.setdefault("kind", "generic")
            data.setdefault("summary", text.strip()[:280])
            data.setdefault("needs_clarification", None)
            data.setdefault("extracted", {})
            return data
        except Exception as e:
            log.warning("requestbee classify failed: %s", e)
            return {
                "kind": "generic",
                "summary": text.strip()[:280],
                "needs_clarification": None,
                "extracted": {},
            }

    async def _post_to_staff(
        self, inter: discord.Interaction, original: str, verdict: dict,
    ):
        guild = inter.guild
        ch = (discord.utils.get(guild.text_channels, name=REQUESTS_CHANNEL)
              or discord.utils.get(guild.text_channels, name="requests-from-members")
              or discord.utils.get(guild.text_channels, name="hive-logs"))
        if not ch:
            log.warning("requestbee: no #%s or fallback channel", REQUESTS_CHANNEL)
            return
        e = discord.Embed(
            title=f"📨 New request — {verdict.get('kind', 'generic')}",
            description=verdict.get("summary") or original,
            color=0x3498db,
        )
        e.add_field(name="From", value=inter.user.mention, inline=True)
        extracted = verdict.get("extracted") or {}
        if extracted:
            kv = "\n".join(f"**{k}:** {v}" for k, v in extracted.items() if v)
            if kv:
                e.add_field(name="Details", value=kv, inline=False)
        e.add_field(name="Original wording", value=f"> {original[:900]}",
                    inline=False)
        e.set_footer(text="Polished by HiveGPT 💛 · routed by HoneyBee 🍯")
        view = StaffActionView(member_id=inter.user.id, request_id=int(time.time()))
        try:
            staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE)
            content = staff_role.mention if staff_role else None
            await ch.send(content=content, embed=e, view=view,
                          allowed_mentions=discord.AllowedMentions(roles=True))
        except Exception as ex:
            log.warning("requestbee: post to staff failed: %s", ex)


# ---------------------------------------------------------------------------
# The follow-up clarify view
# ---------------------------------------------------------------------------

class ClarifyModal(discord.ui.Modal, title="Clarify your request"):
    answer = discord.ui.TextInput(
        label="Your answer",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    def __init__(self, cog: RequestBee, verdict: dict, original_text: str):
        super().__init__()
        self.cog = cog
        self.verdict = verdict
        self.original_text = original_text

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        merged_text = self.original_text + "\n\nClarification: " + str(self.answer)
        # Re-classify with the new info.
        verdict2 = await self.cog._classify(merged_text)
        await self.cog._post_to_staff(inter, merged_text, verdict2)
        await inter.followup.send(
            "🍯 Got it — staff have been pinged. Nothing of yours was posted publicly. "
            "HiveGPT 💛 will DM you when it's done.",
            ephemeral=True,
        )


class ClarifyView(discord.ui.View):
    def __init__(self, cog: RequestBee, verdict: dict, original_text: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.verdict = verdict
        self.original_text = original_text

    @discord.ui.button(label="Answer", style=discord.ButtonStyle.primary)
    async def answer_btn(self, inter: discord.Interaction, _btn: discord.ui.Button):
        await inter.response.send_modal(
            ClarifyModal(self.cog, self.verdict, self.original_text)
        )


async def setup(bot: commands.Bot, *, llm_chat=None) -> RequestBee:
    cog = RequestBee(bot, llm_chat=llm_chat)
    await bot.add_cog(cog)
    return cog
