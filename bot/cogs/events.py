import discord
from discord.ext import commands
import time
import logging
import asyncio
import datetime
from core import pgcompat as sqlite3
from core import messages

log = logging.getLogger("cog.events")

XP_PER_MSG = 8
HONEY_PER_MSG = 2
XP_COOLDOWN_SEC = 60

def _level_for_xp(xp: int) -> int:
    n = 0
    while 100 * (n + 1) * (n + 2) / 2 <= xp:
        n += 1
    return n

class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_ts = {} # (user_id, guild_id) -> ts

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot: return
        guild = member.guild
        log.info(f"Member joined: {member} in {guild.name}")
        
        # 1. Assign Unverified role
        unverified_role = discord.utils.get(guild.roles, name="Unverified")
        if unverified_role:
            try:
                await member.add_roles(unverified_role)
            except discord.Forbidden:
                pass

        # 2. Welcome message in #yourbee
        yourbee_channel = discord.utils.get(guild.text_channels, name="yourbee")
        if yourbee_channel:
            try:
                embed = messages.premael.base(
                    f"🌹 Welcome to {guild.name}",
                    f"Greetings {member.mention}! I am Premael, your romantic soul and guide to this hive.\n\n"
                    "📌 Read the rules in #rules\n"
                    "🔐 Head to #verification-desk and pick:\n"
                    "   • **✍️ Verify as Writer** — if you write on Webnovel\n"
                    "   • **👥 Join as Guest** — if you're here to read and vibe\n"
                    "❓ Stuck? Ping a Heavenly/Blessed Bee in #newbee\n\n"
                    "🌹 I hope you find inspiration and love within our walls!"
                )
                if member.avatar: embed.set_thumbnail(url=member.avatar.url)
                await yourbee_channel.send(embed=embed)
            except discord.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        # XP + Honey Logic
        user_id = message.author.id
        guild_id = message.guild.id
        key = (user_id, guild_id)
        now = time.time()

        if now - self.last_ts.get(key, 0) >= XP_COOLDOWN_SEC:
            conn = sqlite3.connect()
            c = conn.cursor()
            
            # Get current economy
            c.execute("SELECT honey, xp, level FROM economy WHERE discord_id=? AND guild_id=?", (user_id, guild_id))
            row = c.fetchone()
            if not row:
                c.execute("INSERT INTO economy (discord_id, guild_id, honey, xp, level) VALUES (?, ?, 0, 0, 0)", (user_id, guild_id))
                row = (0, 0, 0)
            
            honey, xp, level = row
            
            # Check for double honey (practical shop item)
            # For now, just grant standard
            earn = HONEY_PER_MSG
            new_xp = xp + XP_PER_MSG
            new_level = _level_for_xp(new_xp)
            
            c.execute(
                "UPDATE economy SET xp=?, level=?, honey=honey+? WHERE discord_id=? AND guild_id=?",
                (new_xp, new_level, earn, user_id, guild_id)
            )
            conn.commit()
            
            self.last_ts[key] = now
            
            # Emoji feedback
            try:
                await message.add_reaction("🍯")
            except:
                pass
            
            if new_level > level:
                try:
                    embed = messages.premael.base(
                        "🌹 Level up!",
                        f"{message.author.mention} reached **Hive Level {new_level}**! +50 🍯"
                    )
                    await message.channel.send(embed=embed)
                    # Add bonus honey
                    c.execute("UPDATE economy SET honey=honey+50 WHERE discord_id=? AND guild_id=?", (user_id, guild_id))
                    conn.commit()
                except discord.Forbidden:
                    pass

        # Slacker Bee Guard
        slacker_role = discord.utils.get(message.guild.roles, name="Slacker Bee")
        if slacker_role and slacker_role in message.author.roles:
            allowed_channels = {"ticket-room", "helpbee"}
            if message.channel.name not in allowed_channels:
                try:
                    await message.delete()
                    await message.author.send(
                        "🐝 **Slacker Bee restriction** — complete your ticket first. "
                        "Post in #ticket-room only."
                    )
                except Exception:
                    pass
                return

async def setup(bot):
    await bot.add_cog(EventsCog(bot))
