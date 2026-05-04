import discord
from discord import app_commands
from discord.ext import commands, tasks
import random
import datetime
import logging

from core import pgcompat as sqlite3
from core import messages

log = logging.getLogger("cog.social")

HUGS = [
    "gives you a warm bear hug! 🐻",
    "wraps you in a cozy blanket of hugs! 🛋️",
    "gives you a quick, friendly hug! 👋",
    "tackles you with a giant hug! 🏈",
    "sends a virtual hug your way! 💻",
]

PATS = [
    "gives you a gentle head pat. *pat pat*",
    "ruffles your hair affectionately.",
    "gives you an encouraging pat on the back.",
    "pats your head. You did your best!",
]

COMPLIMENTS = [
    "Your prose is like a warm summer breeze.",
    "The way you handle character development is masterful.",
    "Your world-building is incredibly immersive.",
    "You have a real gift for dialogue.",
    "Your romance scenes make my heart flutter! 💖",
]

class SocialCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.weekly_buddy_pairing.start()

    def cog_unload(self):
        self.weekly_buddy_pairing.cancel()

    @app_commands.command(name="compliment", description="Send a heartfelt writing compliment to someone")
    async def compliment(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.send_message(f"💖 {member.mention}, {random.choice(COMPLIMENTS)}")

    @app_commands.command(name="hug", description="Give someone a hug")
    async def hug(self, interaction: discord.Interaction, member: discord.Member):
        if member.id == interaction.user.id:
            await interaction.response.send_message(f"**{interaction.user.display_name}** hugs themselves! Self-love is important! 🫂")
        else:
            msg = random.choice(HUGS)
            await interaction.response.send_message(f"**{interaction.user.display_name}** {msg} **{member.display_name}**! 🫂")

    @app_commands.command(name="pat", description="Give someone a pat")
    async def pat(self, interaction: discord.Interaction, member: discord.Member):
        if member.id == interaction.user.id:
            await interaction.response.send_message(f"**{interaction.user.display_name}** pats themselves. There, there. 🥺")
        else:
            msg = random.choice(PATS)
            await interaction.response.send_message(f"**{interaction.user.display_name}** {msg} **{member.display_name}**!")

    @app_commands.command(name="writingbuddy", description="Find or view your writing buddy pairing")
    async def writingbuddy(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS buddy_pairs (
            user_a INTEGER,
            user_b INTEGER,
            guild_id INTEGER,
            paired_at TEXT,
            PRIMARY KEY (user_a, guild_id)
        )""")
        
        row = c.execute("SELECT user_b FROM buddy_pairs WHERE user_a=? AND guild_id=?", (interaction.user.id, interaction.guild_id)).fetchone()
        if not row:
            row = c.execute("SELECT user_a FROM buddy_pairs WHERE user_b=? AND guild_id=?", (interaction.user.id, interaction.guild_id)).fetchone()
            
        if not row:
            # Romance banter mention
            return await interaction.response.send_message(
                "🌹 You don't have a writing buddy yet! I'll pair you up in the next cycle. "
                "Maybe you'll find a connection as deep as mine and my mystic 🐉!", 
                ephemeral=True
            )
            
        buddy = f"<@{row[0]}>"
        await interaction.response.send_message(f"🌹 Your writing buddy is {buddy}! Go encourage them! 📝💖")

    @tasks.loop(hours=168) # Every week
    async def weekly_buddy_pairing(self):
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="yourbee")
            if channel:
                try:
                    await channel.send("👯 **Weekly Buddy Pairing!** Premael is weaving new connections. Check your DMs shortly! 🌹")
                except Exception:
                    pass

    @weekly_buddy_pairing.before_loop
    async def before_weekly_pairing(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="snippet", description="Share a writing snippet to #snippets")
    async def snippet(self, interaction: discord.Interaction, text: str):
        if len(text) < 30:
            return await interaction.response.send_message("❌ Snippets must be at least 30 characters long.", ephemeral=True)
        
        channel = discord.utils.get(interaction.guild.text_channels, name="snippets")
        if not channel:
            return await interaction.response.send_message("❌ #snippets channel not found.", ephemeral=True)
            
        embed = messages.premael.base(f"✍️ Snippet from {interaction.user.display_name}", text)
        msg = await channel.send(embed=embed)
        await msg.add_reaction("🔥")
        await msg.add_reaction("💖")
        await msg.add_reaction("📌")
        
        await interaction.response.send_message(f"✅ Snippet shared to {channel.mention}!", ephemeral=True)

    @app_commands.command(name="recommend", description="Recommend a book to the reading club")
    async def recommend(self, interaction: discord.Interaction, book_title: str, author: str, note: str):
        channel = discord.utils.get(interaction.guild.text_channels, name="reading-club")
        if not channel:
            return await interaction.response.send_message("❌ #reading-club channel not found.", ephemeral=True)
            
        embed = messages.premael.base("📚 Book Recommendation")
        embed.add_field(name="Book", value=book_title, inline=False)
        embed.add_field(name="Author", value=author, inline=True)
        embed.add_field(name="Note", value=note, inline=False)
        embed.set_footer(text=f"Recommended by {interaction.user.display_name} 🌹")
        
        await channel.send(embed=embed)
        await interaction.response.send_message(f"✅ Recommendation shared to {channel.mention}!", ephemeral=True)

    @app_commands.command(name="confess", description="Submit an anonymous confession to #confessions")
    async def confess(self, interaction: discord.Interaction, text: str):
        channel = discord.utils.get(interaction.guild.text_channels, name="confessions")
        if not channel:
            return await interaction.response.send_message("❌ #confessions channel not found.", ephemeral=True)
            
        embed = messages.premael.base("🤫 Anonymous Confession", text)
        embed.color = 0x2c3e50 # Darker for secrecy
        embed.set_footer(text="Staff reviewed (Moderated) • Submit using /confess")
        
        await channel.send(embed=embed)
        await interaction.response.send_message("🤫 Your confession has been sent to the void.", ephemeral=True)

    @app_commands.command(name="lore", description="Add an entry to the server lore")
    async def lore(self, interaction: discord.Interaction, title: str, entry: str):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS server_lore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER, author_id INTEGER, title TEXT, entry TEXT, timestamp TEXT
        )""")
        c.execute("INSERT INTO server_lore (guild_id, author_id, title, entry, timestamp) VALUES (?, ?, ?, ?, ?)",
                  (interaction.guild_id, interaction.user.id, title, entry, datetime.datetime.utcnow().isoformat()))
        conn.commit()
        await interaction.response.send_message(f"📜 **Lore Entry Added:** `{title}`\n\n> {entry}")

    @app_commands.command(name="story", description="Contribute to the never-ending community story")
    async def story(self, interaction: discord.Interaction, sentence: str):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS community_story (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER, author_id INTEGER, sentence TEXT, timestamp TEXT
        )""")
        c.execute("INSERT INTO community_story (guild_id, author_id, sentence, timestamp) VALUES (?, ?, ?, ?)",
                  (interaction.guild_id, interaction.user.id, sentence, datetime.datetime.utcnow().isoformat()))
        conn.commit()
        await interaction.response.send_message(f"📖 **Story Updated!**\n*{sentence}*\n\n_(Use /readstory to see the full tale)_")

    @app_commands.command(name="readstory", description="Read the community story so far")
    async def readstory(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        try:
            rows = c.execute("SELECT sentence FROM community_story WHERE guild_id=? ORDER BY id ASC", (interaction.guild_id,)).fetchall()
        except:
            return await interaction.response.send_message("No story has been started yet!")
            
        if not rows:
            return await interaction.response.send_message("The story is blank. Start it with `/story`!")
            
        story_text = " ".join([r[0] for r in rows])
        if len(story_text) > 4000:
            story_text = "..." + story_text[-3900:]
            
        embed = messages.premael.base("📖 The Never-Ending Story", story_text)
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(SocialCog(bot))
