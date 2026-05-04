import discord
from discord.ext import commands, tasks
import datetime
import logging
import random

from core import pgcompat as sqlite3
from core import messages

log = logging.getLogger("cog.scheduler")

class SchedulerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.daily_loop.start()
        self.weekly_loop.start()
        self.monthly_loop.start()
        self.birthday_loop.start()

    def cog_unload(self):
        self.daily_loop.cancel()
        self.weekly_loop.cancel()
        self.monthly_loop.cancel()
        self.birthday_loop.cancel()

    @tasks.loop(hours=24)
    async def daily_loop(self):
        """Posts daily prompt/quote/wotd from community pool."""
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="yourbee")
            if not channel: continue
            
            conn = sqlite3.connect()
            c = conn.cursor()
            
            # Pick a type
            kind = random.choice(["prompt", "quote", "wotd"])
            
            # Try to get from pool
            row = c.execute("SELECT text, author_id FROM content_pool WHERE kind=? ORDER BY RANDOM() LIMIT 1", (kind,)).fetchone()
            
            if row:
                text, author_id = row
                credit = f"\n\n— Submitted by <@{author_id}>" if author_id else ""
                
                # Reward author again (Original bot gave 20 honey when used)
                if author_id:
                    from cogs.economy import add_honey
                    add_honey(author_id, guild.id, 20, f"Pool Submission Used ({kind})")
                
                title = "📝 Daily Prompt" if kind == "prompt" else "💬 Daily Quote" if kind == "quote" else "📚 Word of the Day"
                embed = messages.premael.base(title, f"{text}{credit}")
                if channel.permissions_for(channel.guild.me).send_messages:
                    await channel.send(embed=embed)
            else:
                # Fallback to hardcoded
                from core.content_defaults import PROMPTS, QUOTES, WOTD
                if kind == "prompt":
                    text = random.choice(PROMPTS)
                    title = "📝 Daily Prompt"
                elif kind == "quote":
                    text = random.choice(QUOTES)
                    title = "💬 Daily Quote"
                else:
                    item = random.choice(WOTD)
                    text = f"**{item['word']}**: {item['def']}"
                    title = "📚 Word of the Day"
                
                embed = messages.premael.base(title, text)
                if channel.permissions_for(channel.guild.me).send_messages:
                    await channel.send(embed=embed)

    @tasks.loop(hours=24)
    async def weekly_loop(self):
        """Sunday Hot Chapter of the Week tallying."""
        now = datetime.datetime.utcnow()
        if now.weekday() != 6: return # Only run on Sunday
        
        # Check marker to avoid double run
        marker = f"weekly_hot_{now.strftime('%Y%W')}"
        # (Assuming a simple file-based marker or similar for now)
        
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="breakingnews")
            if not channel: continue
            
            announce_ch = discord.utils.get(guild.text_channels, name="yourbee")
            if not announce_ch: continue

            # Tally reactions from last 7 days
            after = now - datetime.timedelta(days=7)
            messages_list = []
            async for message in channel.history(after=after, limit=200):
                if message.author.bot:
                    # Count reactions
                    count = 0
                    for reaction in message.reactions:
                        if str(reaction.emoji) in ["🔥", "💖", "🍯", "✅"]:
                            count += reaction.count - (1 if reaction.me else 0)
                    if count > 0:
                        messages_list.append((message, count))
            
            if not messages_list: continue
            
            # Sort by reaction count
            messages_list.sort(key=lambda x: x[1], reverse=True)
            winner_msg, winner_count = messages_list[0]
            
            # Extract author if possible from embed
            author_id = None
            if winner_msg.embeds:
                desc = winner_msg.embeds[0].description
                # Search for bold name pattern or similar
                import re
                m = re.search(r"\*\*(.*?)\*\*", desc)
                if m:
                    # Try to find member by display name
                    member = discord.utils.get(guild.members, display_name=m.group(1))
                    if member: author_id = member.id

            if author_id:
                from cogs.economy import add_honey
                add_honey(author_id, guild.id, 200, "Hot Chapter of the Week")
                
                # Give badge
                c = sqlite3.connect().cursor()
                c.execute("INSERT OR IGNORE INTO member_badges (discord_id, guild_id, badge_id, earned_at) VALUES (?, ?, ?, ?)",
                          (author_id, guild.id, "hotchapter", datetime.datetime.utcnow().isoformat()))
                sqlite3.connect().commit()
                
                embed = messages.premael.base(
                    "🔥 Hot Chapter of the Week!",
                    f"🌹 The readers have spoken! **<@{author_id}>**'s latest update on **{winner_msg.embeds[0].title if winner_msg.embeds else 'their book'}** "
                    f"is the Hot Chapter of the Week with **{winner_count}** reactions!\n\n"
                    "They have been awarded **200 🍯** and the **Hot Chapter** badge! ✨"
                )
                await announce_ch.send(embed=embed)

    @tasks.loop(hours=24)
    async def monthly_loop(self):
        """1st of the month awards."""
        now = datetime.datetime.utcnow()
        if now.day != 1: return
        
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="yourbee")
            if not channel: continue
            
            conn = sqlite3.connect()
            c = conn.cursor()
            
            # Top honey earners (last 30 days)
            thirty_days_ago = (now - datetime.timedelta(days=30)).isoformat()
            rows = c.execute(
                "SELECT discord_id, SUM(delta) as total FROM honey_ledger "
                "WHERE guild_id=? AND ts > ? AND delta > 0 AND reason NOT LIKE 'Admin%' "
                "GROUP BY discord_id ORDER BY total DESC LIMIT 3",
                (guild.id, thirty_days_ago)
            ).fetchall()
            
            if not rows: continue
            
            desc = "🌹 As the new moon rises, we honor those who have been most active in the hive this past month!\n\n"
            desc += "**🏆 Top Honey Earners:**\n"
            medals = ["🥇", "🥈", "🥉"]
            for i, (uid, total) in enumerate(rows):
                desc += f"{medals[i]} <@{uid}> — **{total} 🍯**\n"
                # Reward them?
                # from cogs.economy import add_honey
                # add_honey(uid, guild.id, (3-i)*100, "Monthly Top Earner")

            embed = messages.premael.base("🌙 Monthly Hive Awards", desc)
            await channel.send(embed=embed)

    @tasks.loop(hours=24)
    async def birthday_loop(self):
        """Checks for birthdays and wishes them."""
        now = datetime.datetime.utcnow()
        month, day = now.month, now.day
        
        conn = sqlite3.connect()
        c = conn.cursor()
        
        # Ensure birthdays table exists (redundant but safe)
        c.execute("""CREATE TABLE IF NOT EXISTS birthdays (
            discord_id INTEGER,
            guild_id INTEGER,
            month INTEGER,
            day INTEGER,
            PRIMARY KEY (discord_id, guild_id)
        )""")
        
        rows = c.execute("SELECT discord_id FROM birthdays WHERE month=? AND day=?", (month, day)).fetchall()
        
        for (uid,) in rows:
            for guild in self.bot.guilds:
                member = guild.get_member(uid)
                if member:
                    channel = discord.utils.get(guild.text_channels, name="yourbee")
                    if channel:
                        from cogs.economy import add_honey
                        add_honey(uid, guild.id, 100, "Birthday Gift")
                        
                        embed = messages.premael.base(
                            "🎂 Happy Birthday!",
                            f"🌹 Wishing a very happy birthday to {member.mention}! "
                            "May your day be filled with love and many written words. +100 🍯 gift! ✨"
                        )
                        await channel.send(embed=embed)

    @birthday_loop.before_loop
    @daily_loop.before_loop
    @weekly_loop.before_loop
    @monthly_loop.before_loop
    async def before_loops(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(SchedulerCog(bot))
