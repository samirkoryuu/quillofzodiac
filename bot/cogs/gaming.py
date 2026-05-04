import discord
from discord import app_commands
from discord.ext import commands
import datetime
from core import pgcompat as sqlite3

class LFGView(discord.ui.View):
    def __init__(self, host_id, game, slots):
        super().__init__(timeout=None)
        self.host_id = host_id
        self.game = game
        self.slots = slots
        self.members = [host_id]

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.members:
            return await interaction.response.send_message("You're already in!", ephemeral=True)
        if len(self.members) >= self.slots:
            return await interaction.response.send_message("This session is full!", ephemeral=True)
        
        self.members.append(interaction.user.id)
        await self.update_message(interaction)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.members:
            return await interaction.response.send_message("You're not in this session!", ephemeral=True)
        if interaction.user.id == self.host_id:
            return await interaction.response.send_message("Hosts cannot leave! Cancel the LFG instead.", ephemeral=True)
        
        self.members.remove(interaction.user.id)
        await self.update_message(interaction)

    async def update_message(self, interaction):
        members_text = "\n".join(f"• <@{m}>" for m in self.members)
        embed = discord.Embed(
            title=f"🎮 LFG — {self.game}",
            description=f"Host: <@{self.host_id}>\nSlots: **{len(self.members)}/{self.slots}**",
            color=0x2ecc71 if len(self.members) >= self.slots else 0xf1c40f
        )
        embed.add_field(name="Roster", value=members_text)
        await interaction.response.edit_message(embed=embed, view=self)

class GamingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="lfg", description="Find players for a game")
    async def lfg(self, interaction: discord.Interaction, game: str, slots: int = 4):
        if slots < 2 or slots > 20:
            return await interaction.response.send_message("❌ Slots must be between 2 and 20.", ephemeral=True)
        
        embed = discord.Embed(
            title=f"🎮 LFG — {game}",
            description=f"Host: {interaction.user.mention}\nSlots: **1/{slots}**",
            color=0xf1c40f
        )
        embed.add_field(name="Roster", value=f"• {interaction.user.mention}")
        await interaction.response.send_message(embed=embed, view=LFGView(interaction.user.id, game, slots))

    @app_commands.command(name="playing", description="Set your now-playing status")
    async def playing(self, interaction: discord.Interaction, game: str):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS now_playing (
            discord_id INTEGER,
            guild_id INTEGER,
            game TEXT,
            updated_at TEXT,
            PRIMARY KEY (discord_id, guild_id)
        )""")
        c.execute("INSERT INTO now_playing (discord_id, guild_id, game, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(discord_id, guild_id) DO UPDATE SET game=excluded.game, updated_at=excluded.updated_at",
                  (interaction.user.id, interaction.guild_id, game, datetime.datetime.utcnow().isoformat()))
        conn.commit()
        await interaction.response.send_message(f"🎮 You are now playing **{game}**!", ephemeral=True)

    @app_commands.command(name="whoplays", description="See who is playing what")
    async def whoplays(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        try:
            rows = c.execute("SELECT discord_id, game FROM now_playing WHERE guild_id=?", (interaction.guild_id,)).fetchall()
        except:
            return await interaction.response.send_message("No one is playing anything yet!")
            
        if not rows:
            return await interaction.response.send_message("No one is playing anything right now.")
            
        lines = [f"<@{uid}> → **{game}**" for uid, game in rows]
        await interaction.response.send_message(f"🎮 **Now Playing in the Hive:**\n" + "\n".join(lines))

async def setup(bot):
    await bot.add_cog(GamingCog(bot))
