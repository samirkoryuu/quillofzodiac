import discord
from discord import app_commands
from discord.ext import commands
import datetime
from core import pgcompat as sqlite3

class BirthdayCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    birthday_group = app_commands.Group(name="birthday", description="Birthday commands")

    @birthday_group.command(name="set", description="Set your birthday (MM/DD)")
    async def set_birthday(self, interaction: discord.Interaction, date: str):
        try:
            parts = date.replace('-', '/').split('/')
            month = int(parts[0])
            day = int(parts[1])
            # Validate date
            datetime.date(2000, month, day)
        except:
            return await interaction.response.send_message("❌ Invalid format. Please use **MM/DD**, e.g., `06/21`.", ephemeral=True)
            
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS birthdays (
            discord_id INTEGER,
            guild_id INTEGER,
            month INTEGER,
            day INTEGER,
            PRIMARY KEY (discord_id, guild_id)
        )""")
        c.execute("INSERT INTO birthdays (discord_id, guild_id, month, day) VALUES (?, ?, ?, ?) ON CONFLICT(discord_id, guild_id) DO UPDATE SET month=excluded.month, day=excluded.day",
                  (interaction.user.id, interaction.guild_id, month, day))
        conn.commit()
        await interaction.response.send_message(f"🎂 Your birthday has been set to **{month:02d}/{day:02d}**!", ephemeral=True)

    @birthday_group.command(name="list", description="Show upcoming birthdays")
    async def list_birthdays(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        try:
            rows = c.execute("SELECT discord_id, month, day FROM birthdays WHERE guild_id=?", (interaction.guild_id,)).fetchall()
        except:
            return await interaction.response.send_message("No birthdays set yet!")
            
        if not rows:
            return await interaction.response.send_message("No birthdays found.")
            
        # Simple sort logic
        rows.sort(key=lambda x: (x[1], x[2]))
        lines = [f"🎂 <@{uid}> — **{mo:02d}/{da:02d}**" for uid, mo, da in rows]
        await interaction.response.send_message("📅 **Upcoming Birthdays:**\n" + "\n".join(lines))

async def setup(bot):
    await bot.add_cog(BirthdayCog(bot))
