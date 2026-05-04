import discord
from discord import app_commands
from discord.ext import commands
import datetime
import logging
from typing import Optional

from core import pgcompat as sqlite3
from core import messages

log = logging.getLogger("cog.challenges")

class ChallengesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="challenges", description="List all active server word-count challenges")
    async def challenges(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS server_challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            name TEXT,
            goal INTEGER,
            ends_at TEXT,
            status TEXT DEFAULT 'active'
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS challenge_participants (
            challenge_id INTEGER,
            discord_id INTEGER,
            progress INTEGER DEFAULT 0,
            PRIMARY KEY (challenge_id, discord_id)
        )""")
        
        now = datetime.datetime.utcnow().isoformat()
        rows = c.execute("SELECT id, name, goal, ends_at FROM server_challenges WHERE guild_id=? AND ends_at > ? AND status='active'",
                         (interaction.guild_id, now)).fetchall()
        
        if not rows:
            return await interaction.response.send_message("🏁 No active challenges right now. Ask a Blessed Bee to start one!")
            
        embed = messages.meghdoot.base("🏁 Active Hive Challenges")
        for cid, name, goal, ends in rows:
            ends_dt = datetime.datetime.fromisoformat(ends)
            embed.add_field(
                name=f"#{cid}: {name}",
                value=f"🎯 **Goal:** {goal:,} words\n⏳ **Ends:** <t:{int(ends_dt.timestamp())}:R>\nUse `/challengejoin {cid}`",
                inline=False
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="challengejoin", description="Join a word-count challenge")
    async def challengejoin(self, interaction: discord.Interaction, challenge_id: int):
        conn = sqlite3.connect()
        c = conn.cursor()
        row = c.execute("SELECT name FROM server_challenges WHERE id=?", (challenge_id,)).fetchone()
        if not row:
            return await interaction.response.send_message("❌ Challenge not found.", ephemeral=True)
            
        try:
            c.execute("INSERT INTO challenge_participants (challenge_id, discord_id) VALUES (?, ?)", (challenge_id, interaction.user.id))
            conn.commit()
            await interaction.response.send_message(f"✅ You've joined **{row[0]}**! Get those words in! ✍️")
        except sqlite3.IntegrityError:
            await interaction.response.send_message("❌ You're already in this challenge!", ephemeral=True)

    @app_commands.command(name="challengelog", description="Log words for your current challenge")
    async def challengelog(self, interaction: discord.Interaction, challenge_id: int, words: int):
        if words <= 0:
            return await interaction.response.send_message("❌ You must log at least 1 word!", ephemeral=True)
            
        conn = sqlite3.connect()
        c = conn.cursor()
        row = c.execute("SELECT progress FROM challenge_participants WHERE challenge_id=? AND discord_id=?", 
                        (challenge_id, interaction.user.id)).fetchone()
        
        if not row:
            return await interaction.response.send_message("❌ You haven't joined this challenge!", ephemeral=True)
            
        new_progress = row[0] + words
        c.execute("UPDATE challenge_participants SET progress=? WHERE challenge_id=? AND discord_id=?",
                  (new_progress, challenge_id, interaction.user.id))
        
        # Check if goal reached for rewards
        goal_row = c.execute("SELECT goal, name FROM server_challenges WHERE id=?", (challenge_id,)).fetchone()
        conn.commit()
        
        msg = f"✍️ Logged **{words:,}** words! Total: **{new_progress:,}**."
        if goal_row and new_progress >= goal_row[0] and row[0] < goal_row[0]:
            from cogs.economy import add_honey
            add_honey(interaction.user.id, interaction.guild_id, 500, f"Completed Challenge #{challenge_id}")
            msg += f"\n\n🏆 **CONGRATULATIONS!** You reached the goal for **{goal_row[1]}**! +500 🍯 awarded."
            
        await interaction.response.send_message(msg)

    @app_commands.command(name="challengecreate", description="Admin: Create a new server-wide challenge")
    @app_commands.default_permissions(manage_guild=True)
    async def challengecreate(self, interaction: discord.Interaction, name: str, goal: int, days: int):
        conn = sqlite3.connect()
        c = conn.cursor()
        ends_at = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat()
        c.execute("INSERT INTO server_challenges (guild_id, name, goal, ends_at) VALUES (?, ?, ?, ?)",
                  (interaction.guild_id, name, goal, ends_at))
        conn.commit()
        await interaction.response.send_message(f"🏁 Created challenge **{name}** with a {goal:,} word goal for {days} days!")

async def setup(bot):
    await bot.add_cog(ChallengesCog(bot))
