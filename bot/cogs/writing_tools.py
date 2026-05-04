import discord
from discord import app_commands
from discord.ext import commands, tasks
import random
import datetime
import logging

from core import pgcompat as sqlite3

log = logging.getLogger("cog.writing_tools")

PROMPTS = [
    "A character discovers that their shadow has been replaced by someone else's.",
    "Write about a world where the currency is memories.",
    "The hero finally reaches the villain's lair, only to find it's a cozy bakery.",
    "You wake up with the ability to hear the thoughts of inanimate objects.",
    "A dragon is terrified of heights and must find a way to fly to save its friends.",
    "In a city of eternal night, someone finds a glowing seed that brings sunlight.",
]

QUOTES = [
    '"The first draft is just you telling yourself the story." - Terry Pratchett',
    '"You can always edit a bad page. You can’t edit a blank page." - Jodi Picoult',
    '"Start writing, no matter what. The water does not flow until the faucet is turned on." - Louis L’Amour',
    '"To produce a mighty book, you must choose a mighty theme." - Herman Melville',
    '"A word after a word after a word is power." - Margaret Atwood',
]

TROPES = [
    "The chosen one who refuses the call.",
    "A villain who genuinely believes they are the hero.",
    "Enemies to lovers, but they are both spies for opposing sides.",
]

HOOKS = [
    "The clock struck thirteen.",
    "The letter arrived three days after he died.",
    "I never intended to become a god.",
]

CHAR_NAMES = [
    "Aethelgard", "Lyra Whisperwind", "Kaelen Vane", "Seraphina",
]

WOTD = [
    {"word": "Ephemeral", "def": "Lasting for a very short time."},
    {"word": "Petrichor", "def": "The pleasant, earthy smell after rain."},
    {"word": "Mellifluous", "def": "Sweet or musical; pleasant to hear."},
    {"word": "Defenestration", "def": "The act of throwing someone out of a window."},
    {"word": "Serendipity", "def": "The occurrence of events by chance in a happy or beneficial way."},
]

class WritingToolsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.daily_postings.start()

    def cog_unload(self):
        self.daily_postings.cancel()

    @tasks.loop(hours=24)
    async def daily_postings(self):
        # This runs every 24 hours. In a real server, we might want it at a specific time.
        # Find #yourbee channel
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="yourbee")
            if channel:
                try:
                    # Alternating prompt/quote/wotd
                    kind = random.choice(["prompt", "quote", "wotd"])
                    if kind == "prompt":
                        item = random.choice(PROMPTS)
                        embed = discord.Embed(title="✍️ Daily Writing Prompt", description=item, color=0x3498db)
                    elif kind == "quote":
                        item = random.choice(QUOTES)
                        embed = discord.Embed(title="💡 Daily Inspiration", description=item, color=0xf1c40f)
                    else:
                        item = random.choice(WOTD)
                        embed = discord.Embed(title=f"📚 Word of the Day: {item['word']}", description=item['def'], color=0x9b59b6)
                    
                    if channel.permissions_for(guild.me).send_messages:
                        await channel.send(embed=embed)
                except Exception as e:
                    log.error(f"Failed to post daily in {guild.name}: {e}")

    @daily_postings.before_loop
    async def before_daily_postings(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="prompt", description="Get a random writing prompt")
    async def prompt(self, interaction: discord.Interaction):
        prompt = random.choice(PROMPTS)
        embed = discord.Embed(title="✍️ Writing Prompt", description=prompt, color=0x3498db)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="quote", description="Get an inspiring writing quote")
    async def quote(self, interaction: discord.Interaction):
        quote = random.choice(QUOTES)
        embed = discord.Embed(title="💡 Inspiration", description=quote, color=0xf1c40f)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="wotd", description="Get the Word of the Day")
    async def wotd(self, interaction: discord.Interaction):
        # In a real app, this might be tied to the actual date to be the same all day
        item = random.choice(WOTD)
        embed = discord.Embed(title=f"📚 Word of the Day: {item['word']}", description=item['def'], color=0x9b59b6)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="challenge", description="Start a timed writing challenge")
    @app_commands.describe(minutes="How many minutes to write for", word_goal="Your target word count")
    async def challenge(self, interaction: discord.Interaction, minutes: int, word_goal: int):
        if minutes < 1 or minutes > 120:
            return await interaction.response.send_message("❌ Minutes must be between 1 and 120.", ephemeral=True)
            
        end_time = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        timestamp = int(end_time.timestamp())
        
        await interaction.response.send_message(
            f"⏱️ **Writing Challenge Started!**\n"
            f"**Goal:** {word_goal} words\n"
            f"**Ends:** <t:{timestamp}:R>\n\n"
            f"Good luck, {interaction.user.mention}! Get to writing!"
        )


    @app_commands.command(name="trope", description="Get a random writing trope")
    async def trope(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🎬 **Random Trope:** {random.choice(TROPES)}")

    @app_commands.command(name="hook", description="Get a random opening hook")
    async def hook(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🎣 **Opening Hook:** {random.choice(HOOKS)}")

    @app_commands.command(name="charname", description="Get a random character name")
    async def charname(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🎭 **Character Name:** {random.choice(CHAR_NAMES)}")

    @app_commands.command(name="pool_contribute", description="Add your own prompt/quote to the community pool")
    async def pool_contribute(self, interaction: discord.Interaction, kind: str, text: str):
        if kind.lower() not in ["prompt", "quote", "trope", "hook"]:
            return await interaction.response.send_message("❌ Kind must be `prompt`, `quote`, `trope`, or `hook`.", ephemeral=True)
            
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS content_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            text TEXT,
            author_id INTEGER,
            guild_id INTEGER
        )""")
        c.execute("INSERT INTO content_pool (kind, text, author_id, guild_id) VALUES (?, ?, ?, ?)",
                  (kind.lower(), text, interaction.user.id, interaction.guild_id))
        conn.commit()
        await interaction.response.send_message("✅ Your contribution has been added to the community pool! +15 🍯", ephemeral=True)


async def setup(bot):
    await bot.add_cog(WritingToolsCog(bot))
