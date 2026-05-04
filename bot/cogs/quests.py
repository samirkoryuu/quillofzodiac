import discord
from discord import app_commands
from discord.ext import commands
import random
import datetime
import logging

from core import pgcompat as sqlite3
from core import messages

log = logging.getLogger("cog.quests")

QUESTS = [
    "Write 200 words from the POV of a minor character.",
    "Include a description of a sound that triggers a memory.",
    "Have two characters share a meal that one of them hates.",
    "Describe a room where the temperature is unusually high.",
    "Write a scene that takes place in a library after hours.",
    "End your next chapter with a question that isn't answered.",
]

MYSTERY_QUESTS = [
    {"task": "Write 1,500 words this week.", "reward": 200},
    {"task": "Complete 3 daily check-ins in a row.", "reward": 150},
    {"task": "Post 2 writing snippets in #snippets.", "reward": 100},
    {"task": "Interact with a writing buddy pairing.", "reward": 50},
]

class QuestsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="quest", description="Get a random writing mini-quest for today")
    async def quest(self, interaction: discord.Interaction):
        quest = random.choice(QUESTS)
        embed = messages.meghdoot.base(
            "📜 Mystic Mini-Quest",
            f"Greetings, traveler. Here is your challenge for today:\n\n**\"{quest}\"**\n\nComplete this to sharpen your quill! 🐉"
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="mysteryquest", description="Check or complete your weekly mystery quest")
    async def mysteryquest(self, interaction: discord.Interaction, action: str = "check"):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS mystery_quests (
            discord_id INTEGER,
            guild_id INTEGER,
            quest_id INTEGER,
            status TEXT DEFAULT 'active',
            assigned_at TEXT,
            PRIMARY KEY (discord_id, guild_id)
        )""")
        
        row = c.execute("SELECT quest_id, status FROM mystery_quests WHERE discord_id=? AND guild_id=?", 
                        (interaction.user.id, interaction.guild_id)).fetchone()
        
        if action.lower() == "check":
            if not row:
                # 40% chance to assign a new one if not assigned
                if random.random() < 0.4:
                    qid = random.randint(0, len(MYSTERY_QUESTS)-1)
                    c.execute("INSERT INTO mystery_quests (discord_id, guild_id, quest_id, assigned_at) VALUES (?, ?, ?, ?)",
                              (interaction.user.id, interaction.guild_id, qid, datetime.datetime.utcnow().isoformat()))
                    conn.commit()
                    row = (qid, 'active')
                else:
                    return await interaction.response.send_message("🌑 The mists are thick. No mystery quest has been assigned to you this week. Try again later! 🐉")
            
            quest_data = MYSTERY_QUESTS[row[0]]
            status_text = "🟢 Active" if row[1] == 'active' else "✅ Completed"
            embed = messages.meghdoot.base(
                "🔮 Your Weekly Mystery Quest",
                f"**Task:** {quest_data['task']}\n**Status:** {status_text}\n**Reward:** {quest_data['reward']} 🍯\n\n"
                "Use `/mysteryquest action:done` when you've finished!"
            )
            await interaction.response.send_message(embed=embed)
            
        elif action.lower() == "done":
            if not row or row[1] != 'active':
                return await interaction.response.send_message("❌ You don't have an active mystery quest to complete!", ephemeral=True)
            
            quest_data = MYSTERY_QUESTS[row[0]]
            c.execute("UPDATE mystery_quests SET status='completed' WHERE discord_id=? AND guild_id=?", 
                      (interaction.user.id, interaction.guild_id))
            conn.commit()
            
            from cogs.economy import add_honey
            add_honey(interaction.user.id, interaction.guild_id, quest_data['reward'], "Completed Mystery Quest")
            
            await interaction.response.send_message(f"🏆 **Quest Completed!** You've earned **{quest_data['reward']} 🍯**. The mists clear slightly... 🐉")

async def setup(bot):
    await bot.add_cog(QuestsCog(bot))
