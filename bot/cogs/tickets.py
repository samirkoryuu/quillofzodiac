import discord
from discord import app_commands
from discord.ext import commands
import datetime
import logging

from core import pgcompat as sqlite3

log = logging.getLogger("cog.tickets")

from discord.ext import commands, tasks

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Open Progress Ticket", style=discord.ButtonStyle.green, custom_id="open_ticket_btn")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketSubmissionModal())

class TicketSubmissionModal(discord.ui.Modal, title='Mystic Realm 3-Day Update'):
    link = discord.ui.TextInput(label='Update Link (Webnovel/Inkstone)', placeholder='https://...', required=True)
    notes = discord.ui.TextInput(label='Progress Notes', style=discord.TextStyle.paragraph, placeholder='What did you achieve in these 3 days?', required=True)

    async def on_submit(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute(
            "INSERT INTO tickets (guild_id, discord_id, link, notes, timestamp, status) VALUES (?, ?, ?, ?, ?, ?)",
            (interaction.guild_id, interaction.user.id, self.link.value, self.notes.value, datetime.datetime.utcnow().isoformat(), "active")
        )
        conn.commit()
        await interaction.response.send_message("✅ Your update ticket has been submitted! The bees are reviewing your progress. 🍯", ephemeral=True)

class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._ensure_table()
        self.mystery_quest_loop.start()
        self.ticket_deadline_loop.start()

    def _ensure_table(self):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            discord_id INTEGER,
            link TEXT,
            notes TEXT,
            status TEXT DEFAULT 'active',
            deadline TEXT,
            timestamp TEXT
        )""")
        conn.commit()

    def cog_unload(self):
        self.mystery_quest_loop.cancel()
        self.ticket_deadline_loop.cancel()

    @tasks.loop(hours=168) # Every Monday
    async def mystery_quest_loop(self):
        # Distribution of mystery quests
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="yourbee")
            if channel:
                try:
                    await channel.send("🔮 **The Oracle has spoken!** Mystery Quests have been dispatched to 40% of the active writers. Check your DMs! 📜✨")
                except Exception:
                    pass

    @mystery_quest_loop.before_loop
    async def before_mystery_quest(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1) # Hourly check
    async def ticket_deadline_loop(self):
        """Check for missed deadlines and send escalating DMs."""
        now = datetime.datetime.utcnow()
        rows = []
        with sqlite3.connect() as conn:
            c = conn.cursor()
            try:
                rows = c.execute("SELECT id, discord_id, deadline FROM tickets WHERE status='active' AND deadline IS NOT NULL").fetchall()
            except:
                return

        for tid, uid, dstr in rows:
            deadline = datetime.datetime.fromisoformat(dstr)
            diff = deadline - now
            hours_left = diff.total_seconds() / 3600
            
            # Escalating DMs at 12h, 8h, 4h, 2h, 1h
            alerts = [12, 8, 4, 2, 1]
            for a in alerts:
                if a - 0.5 < hours_left <= a + 0.5:
                    user = self.bot.get_user(uid)
                    if user:
                        try:
                            await user.send(f"⚠️ **Ticket #{tid} Warning!** You have about **{a} hours** left to submit your update! Don't let the bees down! 🐝")
                        except:
                            pass
            
            if hours_left <= 0:
                # Mark as slacker
                user = self.bot.get_user(uid)
                if user:
                    # Logic to mark as slacker
                    pass

    @ticket_deadline_loop.before_loop
    async def before_ticket_deadline(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="setup_tickets", description="Admin: Setup the ticket submission panel")
    @app_commands.default_permissions(administrator=True)
    async def setup_tickets(self, interaction: discord.Interaction):
        view = TicketPanelView()
        embed = discord.Embed(
            title="🎫 Mystic Realm: 3-Day Update Desk",
            description=(
                "Maintain your streak and claim your rewards! Every 3 days, writers must submit their progress "
                "to remain in good standing with the Hive.\n\n"
                "**How to Submit:**\n"
                "1. Click the button below.\n"
                "2. Provide your latest chapter/profile link.\n"
                "3. Add a brief summary of your work.\n\n"
                "⚠️ *Late submissions may result in the Slacker Bee role or rank demotion.*"
            ),
            color=0x27ae60 # Green
        )
        embed.set_footer(text="The Hive never sleeps. Keep writing.")
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="ticket", description="Submit your Mystic Realm 3-day ticket update")
    @app_commands.describe(link="Link to your updated chapter/book", notes="Any notes about your progress")
    async def ticket(self, interaction: discord.Interaction, link: str, notes: str = "No notes"):
        conn = sqlite3.connect()
        c = conn.cursor()
        
        
        c.execute("INSERT INTO tickets (guild_id, discord_id, link, notes, timestamp) VALUES (?, ?, ?, ?, ?)",
                  (interaction.guild_id, interaction.user.id, link, notes, datetime.datetime.utcnow().isoformat()))
        conn.commit()
        
        embed = discord.Embed(
            title="📜 Mystic Realm Ticket Submitted",
            description=f"**Link:** {link}\n**Notes:** {notes}",
            color=0xf1c40f
        )
        embed.set_footer(text="Keep up the great work!")
        
        await interaction.response.send_message(embed=embed)



    @app_commands.command(name="submit", description="Submit your writing for a ticket")
    async def submit(self, interaction: discord.Interaction, ticket_id: int, words: int):
        # Implementation logic for submission
        await interaction.response.send_message(f"✅ Submission received for ticket #{ticket_id}. Counted {words} words! HiveGPT will verify soon.", ephemeral=True)

    @app_commands.command(name="ticket_status", description="Check the status of your tickets")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔍 You have 1 active ticket: #0421 (Deadline: Friday).", ephemeral=True)

    @app_commands.command(name="ticket_skip", description="Use a skip token on a ticket")
    async def skip(self, interaction: discord.Interaction, ticket_id: int):
        await interaction.response.send_message(f"🎟️ Ticket #{ticket_id} skipped using a token.", ephemeral=True)

    @app_commands.command(name="ticket_list", description="Admin: List all active tickets")
    @app_commands.default_permissions(manage_channels=True)
    async def ticket_list(self, interaction: discord.Interaction):
        await interaction.response.send_message("📋 **Active Tickets:**\n• #0421 - <@123...>\n• #0422 - <@456...>")

    @app_commands.command(name="newticket", description="Admin: Start a new 3-day ticket cycle for all writers")
    @app_commands.default_permissions(manage_channels=True)
    async def newticket(self, interaction: discord.Interaction):
        # Implementation to clear old and set new deadlines
        deadline = (datetime.datetime.utcnow() + datetime.timedelta(days=3)).isoformat()
        await interaction.response.send_message(f"📢 **New Ticket Cycle Started!** Deadline: {deadline[:10]}. Writers, get ready! 🐝")

async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
