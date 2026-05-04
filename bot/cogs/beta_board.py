import discord
from discord import app_commands
from discord.ext import commands
import datetime
import logging
from typing import Optional

from core import pgcompat as sqlite3
from core import messages

log = logging.getLogger("cog.beta_board")

class BetaBoardCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="needbeta", description="Post a 'looking for beta' request to the community")
    async def needbeta(self, interaction: discord.Interaction, details: str):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS beta_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER,
            guild_id INTEGER,
            kind TEXT,
            details TEXT,
            created_at TEXT
        )""")
        
        c.execute("INSERT INTO beta_posts (author_id, guild_id, kind, details, created_at) VALUES (?, ?, 'need', ?, ?)",
                  (interaction.user.id, interaction.guild_id, details, datetime.datetime.utcnow().isoformat()))
        post_id = c.lastrowid
        conn.commit()
        
        channel = discord.utils.get(interaction.guild.text_channels, name="beta-board")
        if channel:
            embed = messages.premael.base(
                f"🆘 Beta Needed — #{post_id}",
                f"**Request from:** {interaction.user.mention}\n\n{details}"
            )
            embed.set_footer(text="DM them if you can help! 🌹")
            await channel.send(embed=embed)
            await interaction.response.send_message(f"✅ Request posted to {channel.mention}!", ephemeral=True)
        else:
            await interaction.response.send_message(f"✅ Request #{post_id} saved, but #beta-board channel was not found.", ephemeral=True)

    @app_commands.command(name="offerbeta", description="Offer your beta-reading services to the hive")
    async def offerbeta(self, interaction: discord.Interaction, details: str):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("INSERT INTO beta_posts (author_id, guild_id, kind, details, created_at) VALUES (?, ?, 'offer', ?, ?)",
                  (interaction.user.id, interaction.guild_id, details, datetime.datetime.utcnow().isoformat()))
        post_id = c.lastrowid
        conn.commit()
        
        channel = discord.utils.get(interaction.guild.text_channels, name="beta-board")
        if channel:
            embed = messages.premael.base(
                f"🤝 Beta Offered — #{post_id}",
                f"**Available reader:** {interaction.user.mention}\n\n{details}"
            )
            embed.set_footer(text="DM them to discuss your project! 🌹")
            await channel.send(embed=embed)
            await interaction.response.send_message(f"✅ Offer posted to {channel.mention}!", ephemeral=True)
        else:
            await interaction.response.send_message(f"✅ Offer #{post_id} saved, but #beta-board channel was not found.", ephemeral=True)

    @app_commands.command(name="betaboard", description="View the most recent beta board posts")
    async def betaboard(self, interaction: discord.Interaction, page: int = 1):
        conn = sqlite3.connect()
        c = conn.cursor()
        try:
            rows = c.execute("SELECT id, kind, author_id, details FROM beta_posts WHERE guild_id=? ORDER BY id DESC LIMIT 5 OFFSET ?",
                             (interaction.guild_id, (page-1)*5)).fetchall()
        except:
            return await interaction.response.send_message("❌ No beta board posts found.")
            
        if not rows:
            return await interaction.response.send_message("❌ End of the board.")
            
        embed = messages.premael.base(f"📋 Beta Board (Page {page})")
        for bid, kind, aid, details in rows:
            icon = "🆘" if kind == "need" else "🤝"
            label = "Need Beta" if kind == "need" else "Offer Beta"
            snippet = (details[:100] + "...") if len(details) > 100 else details
            embed.add_field(name=f"{icon} #{bid}: {label}", value=f"**User:** <@{aid}>\n{snippet}", inline=False)
            
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(BetaBoardCog(bot))
