import discord
from discord import app_commands
from discord.ext import commands
import random
import json
from core.messages import meghdoot as M

TRUTHS = [
    "What's the most clichéd trope you secretly love?",
    "Have you ever abandoned a story because you fell out of love with it?",
    "What's your most embarrassing writing habit?",
]

DARES = [
    "Write a two-sentence horror story right now.",
    "Describe your WIP in only emojis.",
    "Write a haiku about your biggest writing struggle.",
]

WYR = [
    "Would you rather have perfect prose but weak plot, or a gripping plot but clunky prose?",
    "Would you rather write 10,000 words a day but forget them all, or 100 words that you remember forever?",
]

QUEST_TREE = {
    "start": {
        "text": "🗺️ You stand at the edge of the **Whispering Woods**. The path forks. To your left, a low chant from a moss-covered shrine. To your right, the smell of woodsmoke and laughter.",
        "choices": [("Approach the shrine", "shrine"), ("Follow the smoke", "camp")],
    },
    "shrine": {
        "text": "🕯️ The shrine bears a single offering bowl. A faint voice asks: *what will you give?*",
        "choices": [("Offer a memory", "memory"), ("Offer a name", "name")],
    },
    "camp": {
        "text": "🔥 Three travelers around a fire wave you over. One is sharpening a blade. One is brewing tea.",
        "choices": [("Share a story", "story_end"), ("Sharpen with the warrior", "warrior_end")],
    },
    "memory": {"text": "✨ The bowl glows. The shrine grants you **clarity**. You leave with a perfect line for your next chapter.", "end": True, "reward": 60},
    "name":   {"text": "🌑 The shrine accepts the name and gives nothing back. You leave lighter — and slightly less yourself.", "end": True, "reward": 30},
    "story_end":   {"text": "📖 The travelers laugh, weep, and crown you Storyteller for the night.", "end": True, "reward": 80},
    "warrior_end": {"text": "⚔️ The warrior teaches you a single stance. You'll remember it writing your next fight scene.", "end": True, "reward": 50},
}

class QuestView(discord.ui.View):
    def __init__(self, owner_id: int, node: str = "start"):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.node = node
        self._build()

    def _build(self):
        self.clear_items()
        node = QUEST_TREE[self.node]
        if node.get("end"): return
        for label, target in node["choices"]:
            self.add_item(QuestChoice(label, target))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This isn't your quest!", ephemeral=True)
            return False
        return True

class QuestChoice(discord.ui.Button):
    def __init__(self, label: str, target: str):
        super().__init__(style=discord.ButtonStyle.primary, label=label[:80])
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.node = self.target
        node = QUEST_TREE[view.node]
        view._build()
        embed = discord.Embed(description=node["text"], color=0x3498DB)
        if node.get("end"):
            reward = node.get("reward", 0)
            embed.set_footer(text=f"Quest complete — +{reward} 🍯")
        await interaction.response.edit_message(embed=embed, view=view)

class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="roll", description="Roll some dice (e.g. 1d20)")
    async def roll(self, interaction: discord.Interaction, dice: str = "1d6"):
        try:
            parts = dice.lower().split('d')
            n = int(parts[0]) if parts[0] else 1
            sides = int(parts[1])
            rolls = [random.randint(1, sides) for _ in range(n)]
            await interaction.response.send_message(f"🎲 **{dice}** result: {', '.join(map(str, rolls))} (Total: {sum(rolls)})")
        except:
            await interaction.response.send_message("❌ Invalid dice format. Use something like `2d6`.", ephemeral=True)

    @app_commands.command(name="flip", description="Flip a coin")
    async def flip(self, interaction: discord.Interaction):
        result = random.choice(["Heads", "Tails"])
        await interaction.response.send_message(f"🪙 The coin landed on: **{result}**!")

    @app_commands.command(name="eightball", description="Ask the magic 8-ball a question")
    async def eightball(self, interaction: discord.Interaction, question: str):
        responses = ["Yes.", "No.", "Maybe.", "Ask again later.", "Definitely.", "Very doubtful."]
        await interaction.response.send_message(f"🎱 **Question:** {question}\n**Answer:** {random.choice(responses)}")

    @app_commands.command(name="tod", description="Truth or Dare (Writer Edition)")
    async def tod(self, interaction: discord.Interaction):
        if random.random() < 0.5:
            await interaction.response.send_message(f"💬 **Truth:** {random.choice(TRUTHS)}")
        else:
            await interaction.response.send_message(f"🔥 **Dare:** {random.choice(DARES)}")

    @app_commands.command(name="wyr", description="Would You Rather (Writer Edition)")
    async def wyr(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🤔 **Would You Rather:** {random.choice(WYR)}")

    @app_commands.command(name="quest_hint", description="Get a hint for the current weekly quest")
    async def quest_hint(self, interaction: discord.Interaction):
        node = QUEST_TREE["start"]
        embed = discord.Embed(title="🐉 A Mystical Journey", description=node["text"], color=0x3498DB)
        await interaction.response.send_message(embed=embed, view=QuestView(interaction.user.id))

async def setup(bot):
    await bot.add_cog(FunCog(bot))
