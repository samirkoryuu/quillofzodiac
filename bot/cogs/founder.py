import discord
from discord.ext import commands
import logging

log = logging.getLogger("cog.founder")

def is_founder():
    async def predicate(ctx):
        if any(r.name == "Founder" for r in getattr(ctx.author, "roles", [])):
            return True
        await ctx.send("❌ This legacy command is restricted to the **Founder**.", delete_after=10)
        return False
    return commands.check(predicate)

class FounderCog(commands.Cog):
    """Legacy pointer cog for prefix commands."""
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="founderguide", aliases=["helpall", "allcommands", "founderhelp"])
    @is_founder()
    async def founder_guide(self, ctx):
        """!founderguide — Pointer to modern slash commands."""
        embed = discord.Embed(
            title="👑 Founder Guide: Transition to Slash Commands",
            color=discord.Color.gold(),
            description=(
                "All administrative power has been migrated to modern **Slash Commands** (`/`).\n\n"
                "**Essential Commands:**\n"
                "• `/setup_myserver` — Full server manifestation\n"
                "• `/nuke_and_setup` — Destructive rebuild (Safe Anchor)\n"
                "• `/setup_panels` — Deploy Desk Buttons\n"
                "• `/refresh_rules` — Wipe & Repost Guides\n"
                "• `/start_ticket_cycle` — Begin 3-day updates\n"
                "• `/founder_guide` — Full Slash Command details (DMed)\n\n"
                "⚠️ *The old `!cmds` have been retired to keep the Hive clean.*"
            )
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(FounderCog(bot))
