import discord
from discord import app_commands
from discord.ext import commands
import datetime
from core import pgcompat as sqlite3
from core.messages import bee as M

SHOP_ITEMS = {
    "mystery-box": {"name": "Mystery Box", "price": 100, "category": "fun", "desc": "A random surprise from Madam Wax."},
    "honey-rain":  {"name": "Honey Rain",  "price": 500, "category": "fun", "desc": "Shower the hive with honey!"},
    "streak-saver":{"name": "Streak Saver","price": 200, "category": "practical", "desc": "Protect your streak from a missed day."},
    "custom-title":{"name": "Custom Title","price": 1000,"category": "show-off", "desc": "A unique title on your profile card."},
}

class ShopCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="shop", description="Browse the Honey Shop")
    async def shop(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("SELECT honey FROM economy WHERE discord_id=? AND guild_id=?", (interaction.user.id, interaction.guild_id))
        row = c.fetchone()
        balance = row[0] if row else 0
        
        embed = discord.Embed(title="🛒 NishiBee's Honey Shop", description="Use `/buy <item-id>` to purchase.", color=0xFFC300)
        for iid, item in SHOP_ITEMS.items():
            embed.add_field(name=f"{item['name']} — 🍯 {item['price']}", value=f"`{iid}`\n{item['desc']}", inline=False)
        embed.set_footer(text=f"Your Balance: 🍯 {balance}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="buy", description="Purchase an item from the shop")
    async def buy(self, interaction: discord.Interaction, item_id: str):
        item = SHOP_ITEMS.get(item_id.lower())
        if not item:
            return await interaction.response.send_message("❌ Unknown item ID. Check `/shop`.", ephemeral=True)
            
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("SELECT honey FROM economy WHERE discord_id=? AND guild_id=?", (interaction.user.id, interaction.guild_id))
        row = c.fetchone()
        balance = row[0] if row else 0
        
        if balance < item["price"]:
            return await interaction.response.send_message(f"❌ You need **{item['price'] - balance} 🍯** more honey!", ephemeral=True)
            
        new_balance = balance - item["price"]
        c.execute("UPDATE economy SET honey=? WHERE discord_id=? AND guild_id=?", (new_balance, interaction.user.id, interaction.guild_id))
        conn.commit()
        
        await interaction.response.send_message(f"🛍️ You bought **{item['name']}**! New balance: **{new_balance} 🍯**", ephemeral=True)
        # Fulfill logic would go here
        if item_id == "honey-rain":
            await interaction.channel.send(f"🌧️ **{interaction.user.display_name}** triggered a **Honey Rain**! Everyone gains a little honey! 🐝")

async def setup(bot):
    await bot.add_cog(ShopCog(bot))
