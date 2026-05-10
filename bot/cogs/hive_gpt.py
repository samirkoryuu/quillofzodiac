import discord
from discord.ext import commands
from discord import app_commands
import os
from typing import Optional
from supabase import create_client, Client
from hive_hub.ai_manager import summarize_rule

# Supabase Configuration
BOTSUBA_URL = os.environ.get("BOTSUBA_URL", "")
BOTSUBA_KEY = os.environ.get("BOTSUBA_KEY", "")

class RuleConfirmView(discord.ui.View):
    def __init__(self, rule_content: str, raw_prompt: str, founder_id: int):
        super().__init__(timeout=60)
        self.rule_content = rule_content
        self.raw_prompt = raw_prompt
        self.founder_id = founder_id
        self.supabase: Client = create_client(BOTSUBA_URL, BOTSUBA_KEY)

    @discord.ui.button(label="Confirm Rule", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.founder_id:
            return await interaction.response.send_message("Only the Founder can authorize this directive.", ephemeral=True)
        
        try:
            res = self.supabase.table("extra_rules").insert({
                "content": self.rule_content,
                "raw_prompt": self.raw_prompt,
                "created_by": str(self.founder_id)
            }).execute()
            
            if res.data:
                await interaction.response.edit_message(content=f"✅ **Rule Authorized.** Hive GPT has etched this into the extra_rules archive.\n\n> {self.rule_content}", view=None)
            else:
                await interaction.response.edit_message(content="❌ Failed to save rule to Supabase.", view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"❌ Sync Error: {e}", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.grey, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✖️ Directive discarded.", view=None)

class HiveGPTCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="hivegpt", description="Consult the Hive's neural overseer (Founder Only)")
    @app_commands.describe(directive="The rule or command you wish to issue to the Hive")
    async def hive_gpt(self, interaction: discord.Interaction, directive: str):
        # In a real scenario, check if user is Founder
        # if interaction.user.id != FOUNDER_ID: ...

        await interaction.response.defer()
        
        # 1. AI Summarization
        summary = await summarize_rule(directive)
        if not summary:
            return await interaction.followup.send("❌ The neural pathways are blocked. Gemini failed to interpret the directive.")

        # 2. confirmation Flow
        embed = discord.Embed(
            title="🧠 Hive GPT: Directive Analysis",
            description=f"I have summarized your request into a clean directive. Should I authorize this for the Hive?",
            color=0x9b59b6
        )
        embed.add_field(name="Raw Input", value=f"*{directive}*", inline=False)
        embed.add_field(name="Proposed Rule", value=f"**{summary}**", inline=False)
        embed.set_footer(text="Awaiting Founder authorization...")

        view = RuleConfirmView(summary, directive, interaction.user.id)
        await interaction.followup.send(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(HiveGPTCog(bot))
