import discord
from discord.ext import commands, tasks
import json
import logging
import asyncio
from datetime import datetime, timezone

from core import pgcompat as sqlite3
from core import messages

log = logging.getLogger("cog.ai_scribe")

class AIScribeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.intro_scribe_loop.start()

    def cog_unload(self):
        self.intro_scribe_loop.cancel()

    @tasks.loop(minutes=5)
    async def intro_scribe_loop(self):
        """
        Scans for new member introductions that haven't been summarized yet,
        generates a creative summary using HiveGPT, and posts to #yourbee.
        """
        await self.bot.wait_until_ready()
        
        # We scatter_gather to find NULL summaries across all shards
        # But wait, scatter_gather returns raw rows. We need to process them.
        rows = sqlite3.scatter_gather(
            "SELECT user_id, guild_id, kind, answers_json, created_at "
            "FROM member_intros WHERE summary IS NULL LIMIT 5"
        )
        
        if not rows:
            return

        for row in rows:
            user_id, guild_id, kind, answers_json, created_at = row
            guild = self.bot.get_guild(guild_id)
            if not guild: continue
            
            member = guild.get_member(user_id)
            if not member: continue

            try:
                answers = json.loads(answers_json)
            except Exception:
                log.error(f"Failed to parse answers for user {user_id}")
                continue

            # Generate summary with HiveGPT
            summary = await self.generate_intro_summary(member, kind, answers)
            
            if summary:
                # Update DB (Specific shard)
                with sqlite3.connect(owner_id=user_id) as conn:
                    c = conn.cursor()
                    c.execute(
                        "UPDATE member_intros SET summary=? WHERE user_id=? AND guild_id=? AND kind=?",
                        (summary, user_id, guild_id, kind)
                    )
                    conn.commit()

                # Post to #yourbee
                channel = discord.utils.get(guild.text_channels, name="yourbee")
                if channel:
                    title = "🌩️ A New Scribe Joins the Archive" if kind == "writer" else "✨ A New Wanderer Arrives"
                    embed = messages.meghdoot.base(title, summary)
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await channel.send(content=f"Welcome, {member.mention}!", embed=embed)

    async def generate_intro_summary(self, member: discord.Member, kind: str, answers: dict) -> str:
        """Calls HiveGPT to turn raw form answers into a professional, creative summary."""
        hivegpt = self.bot.get_cog("HiveGPT")
        if not hivegpt:
            # Fallback to a basic summary if HiveGPT is missing
            return f"**{member.display_name}** has joined as a {kind}. They love {answers.get('genres', 'writing')}!"

        prompt = (
            f"You are Meghdoot, the High Scribe of a writing community called The Hive. "
            f"You are introducing a new member to the community based on their verification answers.\n\n"
            f"Member: {member.display_name}\n"
            f"Type: {kind}\n"
            f"Answers: {json.dumps(answers)}\n\n"
            f"Write a professional, slightly epic, and welcoming introduction summary (2-3 paragraphs). "
            f"Use a scholarly and authoritative but warm tone. Do not use markdown headers, just bold text."
        )
        
        try:
            # Assuming hivegpt has an ask method or similar
            response = await hivegpt.ai_ask(prompt, persona="Meghdoot")
            return response
        except Exception as e:
            log.error(f"HiveGPT summary generation failed: {e}")
            return None

async def setup(bot):
    await bot.add_cog(AIScribeCog(bot))
