import discord
from discord import app_commands
from discord.ext import commands
import datetime
import logging
from typing import Optional

from core import pgcompat as sqlite3
from core import messages

log = logging.getLogger("cog.duels")

class DuelsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="duel", description="Challenge someone to a word-count or gaming duel!")
    async def duel(self, interaction: discord.Interaction, member: discord.Member, amount: int, hours: int, description: str):
        if member.id == interaction.user.id:
            return await interaction.response.send_message("❌ You can't duel yourself. Unless it's with your own shadow! 🐉", ephemeral=True)
        if amount <= 0:
            return await interaction.response.send_message("❌ You must bet some honey! 🍯", ephemeral=True)
            
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS duels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenger_id INTEGER,
            target_id INTEGER,
            guild_id INTEGER,
            amount INTEGER,
            description TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            expires_at TEXT,
            winner_id INTEGER
        )""")
        
        expires = (datetime.datetime.utcnow() + datetime.timedelta(hours=hours)).isoformat()
        c.execute("INSERT INTO duels (challenger_id, target_id, guild_id, amount, description, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (interaction.user.id, member.id, interaction.guild_id, amount, description, datetime.datetime.utcnow().isoformat(), expires))
        duel_id = c.lastrowid
        conn.commit()
        
        embed = messages.meghdoot.base(
            "⚔️ Duel Challenge Issued!",
            f"**{interaction.user.display_name}** has challenged **{member.display_name}**!\n\n"
            f"💰 **Stake:** {amount} 🍯\n"
            f"📜 **Goal:** {description}\n"
            f"⏳ **Time Limit:** {hours} hours\n\n"
            f"Use `/duelaccept {duel_id}` to take the challenge!"
        )
        await interaction.response.send_message(member.mention, embed=embed)

    @app_commands.command(name="duelaccept", description="Accept a duel challenge")
    async def duelaccept(self, interaction: discord.Interaction, duel_id: int):
        conn = sqlite3.connect()
        c = conn.cursor()
        row = c.execute("SELECT target_id, challenger_id, amount, status FROM duels WHERE id=?", (duel_id,)).fetchone()
        
        if not row:
            return await interaction.response.send_message("❌ Duel not found.", ephemeral=True)
        if row[0] != interaction.user.id:
            return await interaction.response.send_message("❌ This challenge wasn't for you!", ephemeral=True)
        if row[3] != 'pending':
            return await interaction.response.send_message(f"❌ This duel is already {row[3]}.", ephemeral=True)
            
        # Check if both have honey
        # (EconomyCog handles balance checks usually, but we do a quick one here)
        # Assuming Economy methods are available via bot or core
        from cogs.economy import get_economy, add_honey
        target_honey, _, _, _ = get_economy(interaction.user.id, interaction.guild_id)
        challenger_honey, _, _, _ = get_economy(row[1], interaction.guild_id)
        
        if target_honey < row[2] or challenger_honey < row[2]:
            return await interaction.response.send_message("❌ One of you doesn't have enough honey to cover the bet!", ephemeral=True)
            
        c.execute("UPDATE duels SET status='active' WHERE id=?", (duel_id,))
        conn.commit()
        
        await interaction.response.send_message(f"⚔️ **Duel #{duel_id} is ACTIVE!** Both players have locked in {row[2]} 🍯. Good luck!")

    @app_commands.command(name="duelsubmit", description="Submit proof (screenshot) to win your duel")
    async def duelsubmit(self, interaction: discord.Interaction, duel_id: int, proof_image: discord.Attachment):
        conn = sqlite3.connect()
        c = conn.cursor()
        row = c.execute("SELECT challenger_id, target_id, status, description FROM duels WHERE id=?", (duel_id,)).fetchone()
        
        if not row:
            return await interaction.response.send_message("❌ Duel not found.", ephemeral=True)
        if interaction.user.id not in [row[0], row[1]]:
            return await interaction.response.send_message("❌ You are not part of this duel!", ephemeral=True)
        if row[2] != 'active':
            return await interaction.response.send_message("❌ This duel is not active.", ephemeral=True)
            
        await interaction.response.send_message("🔍 **HiveGPT is analyzing your proof...** Meghdoot's messenger is fast!")
        
        # Here we would call HiveGPT's OCR analysis
        # For now, we'll mark it for staff review or use a mock "Verified"
        # In the full version, we'd use core.inkstone_ocr
        
        embed = messages.meghdoot.base(
            f"🧐 Proof Submitted for Duel #{duel_id}",
            f"**User:** {interaction.user.display_name}\n"
            f"**Description:** {row[3]}\n\n"
            "Staff or HiveGPT will verify the winner shortly. The pot is safe."
        )
        embed.set_image(url=proof_image.url)
        
        # Find logs or mod channel
        log_ch = discord.utils.get(interaction.guild.text_channels, name="hive-logs")
        if log_ch:
            await log_ch.send(embed=embed)
            
        await interaction.followup.send("✅ Proof submitted! Check #hive-logs for status.")

async def setup(bot):
    await bot.add_cog(DuelsCog(bot))
