import discord
from discord import app_commands
from discord.ext import commands
import random
import datetime
import logging
import json

from core import pgcompat as sqlite3
from core import messages

log = logging.getLogger("cog.bingo")

BINGO_TASKS = [
    "Write 500 words", "Daily Check-in", "Share Snippet", "Use Prompt",
    "Win Trivia", "Buy Item", "Give Honey", "Get Hug",
    "Post Lore", "Add Story", "Track Book", "Set Birthday",
    "Use Trope", "Use Hook", "Join Challenge", "Log Progress"
]

class BingoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _get_user_card(self, discord_id: int, guild_id: int):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS bingo_cards (
            discord_id INTEGER,
            guild_id INTEGER,
            card_json TEXT,
            marks_json TEXT,
            PRIMARY KEY (discord_id, guild_id)
        )""")
        
        row = c.execute("SELECT card_json, marks_json FROM bingo_cards WHERE discord_id=? AND guild_id=?", (discord_id, guild_id)).fetchone()
        
        if not row:
            # Generate new 3x3 card
            tasks = random.sample(BINGO_TASKS, 9)
            card = [tasks[0:3], tasks[3:6], tasks[6:9]]
            marks = [[0,0,0],[0,0,0],[0,0,0]]
            c.execute("INSERT INTO bingo_cards (discord_id, guild_id, card_json, marks_json) VALUES (?, ?, ?, ?)",
                      (discord_id, guild_id, json.dumps(card), json.dumps(marks)))
            conn.commit()
            return card, marks
            
        return json.loads(row[0]), json.loads(row[1])

    @app_commands.command(name="bingo", description="View your current 3x3 writing bingo card")
    async def bingo(self, interaction: discord.Interaction):
        card, marks = self._get_user_card(interaction.user.id, interaction.guild_id)
        
        desc = "```\n"
        for r in range(3):
            line = "|"
            for c in range(3):
                status = "✅" if marks[r][c] else "  "
                task = card[r][c]
                line += f" {task[:10].center(10)} {status} |"
            desc += line + "\n" + "-" * (15 * 3 + 1) + "\n"
        desc += "```"
        
        embed = messages.meghdoot.base(
            "🎯 Your Writing Bingo Card",
            f"Complete tasks to mark them! 500 🍯 for a full card.\n\n{desc}"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="bingomark", description="Admin: Manually mark a bingo square for a user")
    @app_commands.default_permissions(manage_guild=True)
    async def bingomark(self, interaction: discord.Interaction, member: discord.Member, row: int, col: int):
        if not (0 <= row <= 2 and 0 <= col <= 2):
            return await interaction.response.send_message("❌ Row/Col must be 0, 1, or 2.", ephemeral=True)
            
        _, marks = self._get_user_card(member.id, interaction.guild_id)
        marks[row][col] = 1
        
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("UPDATE bingo_cards SET marks_json=? WHERE discord_id=? AND guild_id=?",
                  (json.dumps(marks), member.id, interaction.guild_id))
        conn.commit()
        
        await interaction.response.send_message(f"✅ Marked square ({row}, {col}) for **{member.display_name}**!")

async def setup(bot):
    await bot.add_cog(BingoCog(bot))
