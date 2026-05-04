import discord
from discord import app_commands
from discord.ext import commands
import datetime
import logging
from typing import Optional

from core import pgcompat as sqlite3

log = logging.getLogger("cog.economy")

# --- RANK SYSTEM (27 TIERS) ---
# Format: (RankName, MinChapters, MinWords, Emoji, HexColor)
RANK_TIERS = [
    ("Supreme Godscribe", 999, 1548000, "👁️🔱", "#ffffff"),
    ("Master Godscribe",  750, 1162500, "👁️✨", "#74b9ff"),
    ("Godscribe",         550, 852500,  "👁️", "#0984e3"),
    ("Transcendent",      500, 775000,  "🌌", "#00b4d8"),
    ("Divine",            450, 697500,  "🔱", "#00cec9"),
    ("Astral",            400, 620000,  "🌠", "#ffeaa7"),
    ("Celestial",         350, 542500,  "✨", "#f9ca24"),
    ("Legend",            300, 465000,  "👑", "#f1c40f"),
    ("Ethereal",          275, 426000,  "⭐", "#a29bfe"),
    ("Grandscribe",       250, 387500,  "🌙", "#6c5ce7"),
    ("Wordlord",          225, 349000,  "✍️", "#d63031"),
    ("Archscribe",        200, 310000,  "📖", "#e91e8c"),
    ("Mastermind",        175, 271000,  "🖊️", "#9b59b6"),
    ("Sage",              150, 232500,  "📜", "#8e44ad"),
    ("Scribe",            125, 194000,  "🖋️", "#c0392b"),
    ("Chronicler",        100, 155000,  "⚔️", "#e74c3c"),
    ("Wordsmith III",     90,  139500,  "🔥", "#ca6f1e"),
    ("Wordsmith II",      80,  124000,  "🔥", "#e67e22"),
    ("Wordsmith I",       70,  108500,  "🔥", "#f39c12"),
    ("Storyteller III",   60,  93000,   "📖", "#1a5276"),
    ("Storyteller II",    50,  77500,   "📖", "#2e86c1"),
    ("Storyteller I",     40,  62000,   "📖", "#5dade2"),
    ("Quillbearer III",   30,  46500,   "✒️", "#00b894"),
    ("Quillbearer II",    20,  31000,   "✒️", "#1e8449"),
    ("Quillbearer I",     10,  17000,   "✒️", "#27ae60"),
    ("Inkling",           3,   5000,    "📝", "#57836e"),
    ("Newbie Writer",     0,   0,       "🪶", "#808080"),
]

def tier_for(words: int, chapters: int) -> tuple[str, str]:
    """Find the highest rank the member is eligible for."""
    for name, min_ch, min_wd, emoji, *_ in RANK_TIERS:
        if words >= min_wd and chapters >= min_ch:
            return name, emoji
    return "Newbie Writer", "🪶"

def award_badge(discord_id: int, guild_id: int, badge_id: str) -> bool:
    """Award a badge. Returns True if it's a new earn."""
    conn = sqlite3.connect()
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO badges (discord_id, guild_id, badge_id) VALUES (?, ?, ?)",
            (discord_id, guild_id, badge_id)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

async def announce_badge(member: discord.Member, guild: discord.Guild, badge_id: str):
    """Announce a badge earn."""
    # Find badge info from AdminCog if available
    from cogs.admin import BADGES
    b = BADGES.get(badge_id)
    if not b: return
    
    ch = discord.utils.get(guild.text_channels, name="yourbee")
    if ch:
        await ch.send(f"{b['emoji']} **{member.display_name}** just earned the **{b['name']}** badge! _{b['desc']}_")

SHOP_ITEMS = {
    # SHOW-OFF
    "title-honeytouched": {"name": "🍯 Honey Touched", "price": 300, "kind": "title", "value": "🍯 Honey Touched", "category": "show-off"},
    "title-queenbee": {"name": "👑 Queen Bee", "price": 1000, "kind": "title", "value": "👑 Queen Bee", "category": "show-off"},
    "title-legendary": {"name": "🔥 Legendary Scribe", "price": 1500, "kind": "title", "value": "🔥 Legendary Scribe", "category": "show-off"},
    "title-inkblood": {"name": "🖊️ Ink & Blood", "price": 800, "kind": "title", "value": "🖊️ Ink & Blood", "category": "show-off"},
    "title-wordwitch": {"name": "🧙 Word Witch", "price": 600, "kind": "title", "value": "🧙 Word Witch", "category": "show-off"},
    "spotlight": {"name": "📣 Member Spotlight", "price": 250, "kind": "spotlight", "value": None, "category": "show-off"},
    "featured-book": {"name": "📖 Featured Book", "price": 400, "kind": "featured-book", "value": None, "category": "show-off"},
    # COSMETIC
    "color-gold": {"name": "🌟 Gold Nickname", "price": 500, "kind": "nickname-color", "value": "gold", "category": "cosmetic"},
    "color-purple": {"name": "💜 Purple Nickname", "price": 500, "kind": "nickname-color", "value": "purple", "category": "cosmetic"},
    "color-teal": {"name": "🩵 Teal Nickname", "price": 500, "kind": "nickname-color", "value": "teal", "category": "cosmetic"},
    "emoji-prefix": {"name": "✨ Secret Emoji Prefix", "price": 200, "kind": "emoji-prefix", "value": None, "category": "cosmetic"},
    "birthday-banner": {"name": "🎂 Birthday Banner", "price": 400, "kind": "birthday-banner", "value": None, "category": "cosmetic"},
    # PRACTICAL
    "streak-saver": {"name": "🛡️ Streak Saver", "price": 200, "kind": "streak-saver", "value": None, "category": "practical"},
    "double-honey": {"name": "🍯🍯 Double Honey Day", "price": 500, "kind": "double-honey", "value": None, "category": "practical"},
    "skip-ticket-item": {"name": "🎫 Skip One Ticket Item", "price": 300, "kind": "skip-ticket", "value": None, "category": "practical"},
    "pool-boost": {"name": "🚀 Pool Priority Boost", "price": 400, "kind": "pool-boost", "value": None, "category": "practical"},
    # FUN
    "mystery-box": {"name": "📦 Mystery Box", "price": 100, "kind": "mystery-box", "value": None, "category": "fun"},
    "honey-rain-24h": {"name": "🌧️ Honey Rain (24h)", "price": 750, "kind": "honey-rain", "value": None, "category": "fun"},
    "friday-drop": {"name": "📬 Friday Drop Entry", "price": 50, "kind": "friday-drop", "value": None, "category": "fun"},
    "easter-egg-hunt": {"name": "🥚 Easter Egg Hunt", "price": 150, "kind": "easter-egg", "value": None, "category": "fun"},
    # TROPHY
    "trophy-binger": {"name": "📺 Certified Binger", "price": 800, "kind": "trophy", "value": "certified-binger", "category": "trophy"},
    "trophy-plot-armor": {"name": "🛡️ Plot Armor", "price": 1000, "kind": "trophy", "value": "plot-armor", "category": "trophy"},
    "trophy-word-hoarder": {"name": "📚 Word Hoarder", "price": 600, "kind": "trophy", "value": "word-hoarder", "category": "trophy"},
    "trophy-architect": {"name": "🏛️ The Architect", "price": 1200, "kind": "trophy", "value": "architect", "category": "trophy"},
    "trophy-quill": {"name": "🪶 Golden Quill", "price": 2000, "kind": "trophy", "value": "golden-quill", "category": "trophy"},
}

def get_economy(discord_id: int, guild_id: int):
    conn = sqlite3.connect()
    c = conn.cursor()
    
    # Ensure tables exist to prevent OperationalError
    c.execute("""CREATE TABLE IF NOT EXISTS economy (
        discord_id INTEGER,
        guild_id INTEGER,
        honey INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 0,
        title TEXT,
        PRIMARY KEY (discord_id, guild_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS honey_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discord_id INTEGER,
        guild_id INTEGER,
        delta INTEGER,
        reason TEXT,
        ts TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS shop_purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discord_id INTEGER,
        guild_id INTEGER,
        item_id TEXT,
        price INTEGER,
        purchased_at TEXT
    )""")
    conn.commit()

    c.execute(
        "SELECT honey, xp, level, title FROM economy WHERE discord_id=? AND guild_id=?", 
        (discord_id, guild_id)
    )
    row = c.fetchone()
    
    # Ensure streak table exists
    c.execute("""CREATE TABLE IF NOT EXISTS streaks (
        discord_id INTEGER,
        guild_id INTEGER,
        current INTEGER DEFAULT 0,
        best INTEGER DEFAULT 0,
        last_date TEXT,
        PRIMARY KEY (discord_id, guild_id)
    )""")
    # Ensure badges table exists
    c.execute("""CREATE TABLE IF NOT EXISTS badges (
        discord_id INTEGER,
        guild_id INTEGER,
        badge_id TEXT,
        PRIMARY KEY (discord_id, guild_id, badge_id)
    )""")
    
    # Simple migration check for economy table (add xp/level if missing)
    try:
        c.execute("ALTER TABLE economy ADD COLUMN IF NOT EXISTS xp INTEGER DEFAULT 0")
        c.execute("ALTER TABLE economy ADD COLUMN IF NOT EXISTS level INTEGER DEFAULT 0")
    except:
        pass
        
    conn.commit()

    if not row:
        c.execute("INSERT INTO economy (discord_id, guild_id) VALUES (?, ?)", (discord_id, guild_id))
        conn.commit()
        return 0, 0, 0, None
    return row[0], row[1], row[2], row[3]

def add_honey(discord_id: int, guild_id: int, amount: int, reason: str):
    conn = sqlite3.connect()
    c = conn.cursor()
    c.execute(
        "UPDATE economy SET honey = honey + ? WHERE discord_id=? AND guild_id=?", 
        (amount, discord_id, guild_id)
    )
    if c.rowcount == 0:
        c.execute("INSERT INTO economy (discord_id, guild_id, honey) VALUES (?, ?, ?)", (discord_id, guild_id, amount))
    c.execute(
        "INSERT INTO honey_ledger (discord_id, guild_id, delta, reason, ts) VALUES (?, ?, ?, ?, ?)",
        (discord_id, guild_id, amount, reason, datetime.datetime.utcnow().isoformat())
    )
    conn.commit()


from discord.ext import commands, tasks

class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.friday_drop_loop.start()
        self.honey_rain_loop.start()

    def cog_unload(self):
        self.friday_drop_loop.cancel()
        self.honey_rain_loop.cancel()

    @tasks.loop(hours=168) # Every Friday
    async def friday_drop_loop(self):
        # This usually runs at a specific time. For now, every 168h.
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="yourbee")
            if channel:
                try:
                    await channel.send("📬 **The Friday Drop is here!** 🍯 Check #shop to enter for a chance to win the weekly jackpot!")
                except Exception:
                    pass

    @friday_drop_loop.before_loop
    async def before_friday_drop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24) # Random chance every day
    async def honey_rain_loop(self):
        # Simplified: random chance for rain
        import random
        if random.random() < 0.3: # 30% chance
            for guild in self.bot.guilds:
                channel = discord.utils.get(guild.text_channels, name="yourbee")
                if channel:
                    try:
                        await channel.send("🌧️ **Honey Rain!** For the next hour, everyone gets +5 🍯 per message! 🍯✨")
                    except Exception:
                        pass

    @honey_rain_loop.before_loop
    async def before_honey_rain(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="honey", description="Check your or someone else's honey balance and level")
    async def honey(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        honey, xp, level, title = get_economy(target.id, interaction.guild_id)
        
        embed = discord.Embed(title=f"🍯 Economy: {target.display_name}", color=0xFFD700)
        embed.set_thumbnail(url=target.display_avatar.url if target.display_avatar else None)
        embed.add_field(name="Honey Balance", value=f"**{honey} 🍯**", inline=True)
        embed.add_field(name="Level", value=f"**{level}** ({xp} XP)", inline=True)
        if title:
            embed.add_field(name="Equipped Title", value=title, inline=False)
            
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="give", description="Give some of your honey to another member")
    async def give(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if amount <= 0:
            return await interaction.response.send_message("❌ Amount must be greater than 0.", ephemeral=True)
        if member.bot or member.id == interaction.user.id:
            return await interaction.response.send_message("❌ You can't give honey to bots or yourself.", ephemeral=True)
            
        my_honey, _, _, _ = get_economy(interaction.user.id, interaction.guild_id)
        if my_honey < amount:
            return await interaction.response.send_message(f"❌ You don't have enough honey! Your balance: **{my_honey} 🍯**", ephemeral=True)
            
        add_honey(interaction.user.id, interaction.guild_id, -amount, f"Gifted to {member.id}")
        add_honey(member.id, interaction.guild_id, amount, f"Gifted from {interaction.user.id}")
        
        await interaction.response.send_message(f"🎁 **{interaction.user.display_name}** gifted **{amount} 🍯** to **{member.display_name}**!")

    @app_commands.command(name="shop", description="Browse the Hive Shop")
    @app_commands.choices(category=[
        app_commands.Choice(name="All Items", value="all"),
        app_commands.Choice(name="Show-Off (Titles)", value="show-off"),
        app_commands.Choice(name="Cosmetic (Colors/Emojis)", value="cosmetic"),
        app_commands.Choice(name="Practical (Boosts/Streaks)", value="practical"),
        app_commands.Choice(name="Fun (Mystery/Rain)", value="fun"),
        app_commands.Choice(name="Trophies", value="trophy"),
    ])
    async def shop(self, interaction: discord.Interaction, category: str = "all"):
        embed = discord.Embed(title="🛍️ The Hive Shop", description="Use `/buy <item_id>` to purchase an item.", color=0xFFA500)
        
        cats_to_show = ["show-off", "cosmetic", "practical", "fun", "trophy"] if category == "all" else [category]
        
        for cat in cats_to_show:
            items_str = ""
            for item_id, data in SHOP_ITEMS.items():
                if data["category"] == cat:
                    items_str += f"**`{item_id}`** — {data['name']} (`{data['price']} 🍯`)\n"
            if items_str:
                embed.add_field(name=f"[{cat.upper()}]", value=items_str, inline=False)
                
        my_honey, _, _, _ = get_economy(interaction.user.id, interaction.guild_id)
        embed.set_footer(text=f"Your Balance: {my_honey} 🍯")
        await interaction.response.send_message(embed=embed)

    async def item_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        matches = [
            app_commands.Choice(name=f"{data['name']} ({data['price']} 🍯)", value=item_id)
            for item_id, data in SHOP_ITEMS.items() if current.lower() in item_id.lower() or current.lower() in data['name'].lower()
        ]
        return matches[:25] # Discord limit is 25

    @app_commands.command(name="buy", description="Buy an item from the shop")
    @app_commands.autocomplete(item_id=item_autocomplete)
    async def buy(self, interaction: discord.Interaction, item_id: str):
        if item_id not in SHOP_ITEMS:
            return await interaction.response.send_message("❌ Unknown item ID. Use `/shop` to see what's available.", ephemeral=True)
            
        item = SHOP_ITEMS[item_id]
        my_honey, _, _, _ = get_economy(interaction.user.id, interaction.guild_id)
        
        if my_honey < item["price"]:
            return await interaction.response.send_message(f"❌ You need **{item['price']} 🍯** to buy **{item['name']}**. You only have {my_honey} 🍯.", ephemeral=True)
            
        # Deduct honey
        add_honey(interaction.user.id, interaction.guild_id, -item["price"], f"Bought {item_id}")
        
        # Log purchase
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute(
            "INSERT INTO shop_purchases (discord_id, guild_id, item_id, price, purchased_at) VALUES (?, ?, ?, ?, ?)",
            (interaction.user.id, interaction.guild_id, item_id, item["price"], datetime.datetime.utcnow().isoformat())
        )
        
        # Apply item effects
        if item["kind"] == "title":
            c.execute("UPDATE economy SET title=? WHERE discord_id=? AND guild_id=?", (item["value"], interaction.user.id, interaction.guild_id))
            conn.commit()
            await interaction.response.send_message(f"🎉 You successfully bought **{item['name']}**! Title equipped.")
            
        elif item["kind"] == "nickname-color":
            conn.commit()
            # Send to #hive-life or #yourbee to request staff/bot fulfill the role change
            # (or we could handle it programmatically here if roles match exact names)
            await interaction.response.send_message(f"🎉 You bought **{item['name']}**! Your color role will be updated momentarily.")
            
        else:
            conn.commit()
            await interaction.response.send_message(f"🎉 You successfully bought **{item['name']}**! A staff member or the system will fulfill this shortly.")

    @app_commands.command(name="honeytop", description="View the honey leaderboard")
    async def honeytop(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        rows = c.execute(
            "SELECT discord_id, honey, level FROM economy WHERE guild_id=? ORDER BY honey DESC LIMIT 10", 
            (interaction.guild_id,)
        ).fetchall()
        
        if not rows:
            return await interaction.response.send_message("No one has any honey yet!")
            
        desc = ""
        for i, (uid, honey, lvl) in enumerate(rows, 1):
            user_mention = f"<@{uid}>"
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🐝"
            desc += f"{medal} **#{i}** {user_mention} — **{honey} 🍯** (Lv. {lvl})\n"
            
        embed = discord.Embed(title="🏆 The Honey Leaderboard", description=desc, color=0xFFD700)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="checkin", description="Daily writing check-in to build your streak")
    async def checkin(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        today = datetime.date.today().isoformat()
        
        row = c.execute("SELECT current, best, last_date FROM streaks WHERE discord_id=? AND guild_id=?", 
                        (interaction.user.id, interaction.guild_id)).fetchone()
        
        if row:
            current, best, last_date = row
            if last_date == today:
                return await interaction.response.send_message("📅 You already checked in today! See you tomorrow.", ephemeral=True)
            
            yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            if last_date == yesterday:
                current += 1
            else:
                current = 1
            best = max(best, current)
            c.execute("UPDATE streaks SET current=?, best=?, last_date=? WHERE discord_id=? AND guild_id=?",
                      (current, best, today, interaction.user.id, interaction.guild_id))
        else:
            current, best = 1, 1
            c.execute("INSERT INTO streaks (discord_id, guild_id, current, best, last_date) VALUES (?, ?, ?, ?, ?)",
                      (interaction.user.id, interaction.guild_id, 1, 1, today))
        
        conn.commit()
        add_honey(interaction.user.id, interaction.guild_id, 10, "Daily Check-in")
        await interaction.response.send_message(f"🔥 **Check-in Successful!** Day **{current}** streak. +10 🍯 awarded!")

    @app_commands.command(name="streak", description="Check your current writing streak")
    async def streak(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        conn = sqlite3.connect()
        c = conn.cursor()
        row = c.execute("SELECT current, best FROM streaks WHERE discord_id=? AND guild_id=?", 
                        (target.id, interaction.guild_id)).fetchone()
        
        if not row:
            return await interaction.response.send_message(f"**{target.display_name}** hasn't started a streak yet!")
            
        await interaction.response.send_message(f"🔥 **{target.display_name}'s Streak:** {row[0]} days (Best: {row[1]})")

    @app_commands.command(name="badges", description="View your earned badges")
    async def badges(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        conn = sqlite3.connect()
        c = conn.cursor()
        rows = c.execute("SELECT badge_id FROM badges WHERE discord_id=? AND guild_id=?", 
                         (target.id, interaction.guild_id)).fetchall()
        
        if not rows:
            return await interaction.response.send_message(f"**{target.display_name}** hasn't earned any badges yet. 🏅")
            
        badge_list = ", ".join([f"`{r[0]}`" for r in rows])
        await interaction.response.send_message(f"🏅 **{target.display_name}'s Badges:**\n{badge_list}")

    @app_commands.command(name="ledger", description="View your recent honey transactions")
    async def ledger(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        rows = c.execute("SELECT delta, reason, ts FROM honey_ledger WHERE discord_id=? AND guild_id=? ORDER BY id DESC LIMIT 5",
                         (interaction.user.id, interaction.guild_id)).fetchall()
        if not rows:
            return await interaction.response.send_message("No transactions found.", ephemeral=True)
            
        lines = [f"{'+' if r[0]>0 else ''}{r[0]} 🍯 — {r[1]} (`{r[2][:10]}`)" for r in rows]
        await interaction.response.send_message(f"🧾 **Recent Transactions:**\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="mywords", description="Check your total word count stats")
    async def mywords(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        row = c.execute("SELECT words, chapters FROM writers WHERE discord_id=?", (interaction.user.id,)).fetchone()
        if not row:
            return await interaction.response.send_message("You aren't a verified writer yet! Use `/verify`.", ephemeral=True)
            
        await interaction.response.send_message(f"✍️ **Your Stats:** {row[0]:,} words across {row[1]} chapters.")

    @app_commands.command(name="hivejourney", description="View a summary of your journey in the Hive")
    async def hivejourney(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        honey, xp, level, title = get_economy(target.id, interaction.guild_id)
        
        conn = sqlite3.connect()
        c = conn.cursor()
        streak_row = c.execute("SELECT current FROM streaks WHERE discord_id=? AND guild_id=?", (target.id, interaction.guild_id)).fetchone()
        badge_count = c.execute("SELECT COUNT(*) FROM badges WHERE discord_id=? AND guild_id=?", (target.id, interaction.guild_id)).fetchone()[0]
        
        streak = streak_row[0] if streak_row else 0
        
        embed = discord.Embed(title=f"📜 The Hive Journey: {target.display_name}", color=0x3498db)
        embed.add_field(name="Level", value=f"**{level}** ({xp} XP)", inline=True)
        embed.add_field(name="Honey", value=f"**{honey} 🍯**", inline=True)
        embed.add_field(name="Current Streak", value=f"**{streak} days** 🔥", inline=True)
        embed.add_field(name="Badges Earned", value=f"**{badge_count}** 🏅", inline=True)
        if title:
            embed.add_field(name="Current Title", value=f"_{title}_", inline=False)
            
        embed.set_footer(text="Your story is still being written...")
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
