import discord
from discord import app_commands
from discord.ext import commands
import random
import datetime
import json
import logging

from core import pgcompat as sqlite3

log = logging.getLogger("cog.verification")

class WriterVerificationStep2(discord.ui.Modal, title='Writer Verification (Step 2 of 2)'):
    why_write = discord.ui.TextInput(
        label='Why do you write?',
        style=discord.TextStyle.paragraph,
        placeholder='I write because...',
        required=True,
        max_length=500,
    )
    book_count = discord.ui.TextInput(
        label='How many official books have you written?',
        style=discord.TextStyle.short,
        placeholder='e.g., 2',
        required=True,
    )
    genres = discord.ui.TextInput(
        label='Your favorite 3 genres?',
        style=discord.TextStyle.short,
        placeholder='Fantasy, Sci-Fi, Romance',
        required=True,
    )
    top_books = discord.ui.TextInput(
        label='Your top 3 favorite reads?',
        style=discord.TextStyle.paragraph,
        placeholder='1. Lord of the Mysteries\n2. Shadow Slave\n...',
        required=True,
    )

    def __init__(self, step1_data: dict, verification_code: str):
        super().__init__()
        self.step1_data = step1_data
        self.verification_code = verification_code

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "✅ Verification complete! Please wait while the ancient spirits review your profile and scribe your introduction...",
            ephemeral=True
        )
        
        # Combine all data
        all_data = {
            **self.step1_data,
            "why_write": self.why_write.value,
            "book_count": self.book_count.value,
            "genres": self.genres.value,
            "top_books": self.top_books.value,
        }

        user_id = interaction.user.id
        guild_id = interaction.guild_id

        with sqlite3.connect() as conn:
            c = conn.cursor()
            
            # Save to writers table
            c.execute(
                "INSERT INTO writers (discord_id, webnovel_link, pen_name, code) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(discord_id) DO UPDATE SET webnovel_link=excluded.webnovel_link, pen_name=excluded.pen_name, code=excluded.code",
                (user_id, all_data['profile_link'], all_data['pen_name'], self.verification_code)
            )

            # Save birthday
            if all_data['birthday']:
                try:
                    month, day = map(int, all_data['birthday'].replace('/', '-').split('-'))
                    c.execute(
                        "INSERT INTO birthdays (discord_id, guild_id, month, day) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(discord_id, guild_id) DO UPDATE SET month=excluded.month, day=excluded.day",
                        (user_id, guild_id, month, day)
                    )
                except Exception:
                    log.warning(f"Failed to parse birthday: {all_data['birthday']}")

            # Save intro data
            c.execute(
                "INSERT INTO member_intros (user_id, guild_id, kind, answers_json, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, guild_id, kind) DO UPDATE SET "
                "answers_json=excluded.answers_json, created_at=excluded.created_at, summary=NULL",
                (user_id, guild_id, "writer", json.dumps(all_data, ensure_ascii=False), datetime.datetime.utcnow().isoformat())
            )
            conn.commit()

        # 2. Add Roles
        guild = interaction.guild
        verified_role = discord.utils.get(guild.roles, name="Verified Author")
        newbie_role = discord.utils.get(guild.roles, name="Newbie Writer")
        unverified_role = discord.utils.get(guild.roles, name="Unverified")
        
        roles_to_add = []
        if newbie_role: roles_to_add.append(newbie_role)
        
        if roles_to_add:
            try:
                await interaction.user.add_roles(*roles_to_add)
            except discord.Forbidden:
                log.error("Missing permissions to add roles.")
                
        if unverified_role:
            try:
                await interaction.user.remove_roles(unverified_role)
            except discord.Forbidden:
                pass

        # 3. Post to #verification-desk for staff tracking
        desk_channel = discord.utils.get(guild.text_channels, name="verification-desk")
        if desk_channel:
            await desk_channel.send(
                f"📝 **New Writer Verified:** {interaction.user.mention}\n"
                f"**Pen Name:** {all_data['pen_name']}\n"
                f"**Link:** <{all_data['profile_link']}>\n"
                f"**Code checked?** {all_data['code_confirmed']}"
            )

        # Note: HiveGPT (in its own background loop) will see `summary=NULL` in `member_intros`
        # and automatically generate the summary and post it to `#yourbee`.

class WriterVerificationStep1(discord.ui.Modal, title='Writer Verification (Step 1 of 2)'):
    profile_link = discord.ui.TextInput(
        label='Webnovel Profile Link',
        style=discord.TextStyle.short,
        placeholder='https://www.webnovel.com/profile/...',
        required=True,
    )
    pen_name = discord.ui.TextInput(
        label='Webnovel Pen Name',
        style=discord.TextStyle.short,
        placeholder='Your Pen Name',
        required=True,
    )
    code_confirmed = discord.ui.TextInput(
        label='Did you put the verification code in bio?',
        style=discord.TextStyle.short,
        placeholder='Type Yes after adding the code',
        required=True,
    )
    birthday = discord.ui.TextInput(
        label='Birthday (MM/DD) - Optional',
        style=discord.TextStyle.short,
        placeholder='e.g., 07/24',
        required=False,
    )

    def __init__(self, verification_code: str):
        super().__init__()
        self.verification_code = verification_code
        self.code_confirmed.label = f'Did you put code {verification_code} in bio?'

    async def on_submit(self, interaction: discord.Interaction):
        # Pass data to step 2
        data = {
            "profile_link": self.profile_link.value,
            "pen_name": self.pen_name.value,
            "code_confirmed": self.code_confirmed.value,
            "birthday": self.birthday.value,
        }
        await interaction.response.send_modal(WriterVerificationStep2(data, self.verification_code))


class GuestVerification(discord.ui.Modal, title='Guest Verification'):
    nickname = discord.ui.TextInput(
        label='What nickname should we call you?',
        style=discord.TextStyle.short,
        placeholder='Your nickname',
        required=True,
    )
    intro = discord.ui.TextInput(
        label='Tell us a bit about yourself',
        style=discord.TextStyle.paragraph,
        placeholder='Hi, I am...',
        required=True,
        max_length=400,
    )
    genres = discord.ui.TextInput(
        label='Favorite reading genres?',
        style=discord.TextStyle.short,
        placeholder='Fantasy, Sci-Fi',
        required=True,
    )
    why_joined = discord.ui.TextInput(
        label='Why did you join this server?',
        style=discord.TextStyle.paragraph,
        placeholder='I joined because...',
        required=True,
        max_length=300,
    )
    referer = discord.ui.TextInput(
        label='Who invited you? (If anyone)',
        style=discord.TextStyle.short,
        placeholder='Pen name or discord name',
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "✅ Guest verification complete! Welcome to the realm! 🐉✨",
            ephemeral=True
        )
        
        all_data = {
            "nickname": self.nickname.value,
            "intro": self.intro.value,
            "genres": self.genres.value,
            "why_joined": self.why_joined.value,
            "referer": self.referer.value,
        }

        user_id = interaction.user.id
        guild_id = interaction.guild_id

        # Change nickname
        try:
            await interaction.user.edit(nick=self.nickname.value[:32])
        except discord.Forbidden:
            pass

        # Save to DB
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute(
            "INSERT INTO member_intros (user_id, guild_id, kind, answers_json, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, guild_id, kind) DO UPDATE SET "
            "answers_json=excluded.answers_json, created_at=excluded.created_at, summary=NULL",
            (user_id, guild_id, "guest", json.dumps(all_data, ensure_ascii=False), datetime.datetime.utcnow().isoformat())
        )
        conn.commit()

        # Add Roles
        guild = interaction.guild
        guest_role = discord.utils.get(guild.roles, name="Guest")
        unverified_role = discord.utils.get(guild.roles, name="Unverified")
        
        if guest_role:
            try:
                await interaction.user.add_roles(guest_role)
            except discord.Forbidden:
                pass
                
        if unverified_role:
            try:
                await interaction.user.remove_roles(unverified_role)
            except discord.Forbidden:
                pass

        # Notify desk
        desk_channel = discord.utils.get(guild.text_channels, name="verification-desk")
        if desk_channel:
            await desk_channel.send(
                f"👥 **New Guest Verified:** {interaction.user.mention}\n"
                f"**Nickname:** {self.nickname.value}\n"
                f"**Referred By:** {self.referer.value}"
            )


class VerificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✍️ Verify as Writer", style=discord.ButtonStyle.green, custom_id="verify_writer_btn")
    async def verify_writer(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Generate a random code for them to put in their bio
        code = f"HB-{random.randint(1000,9999)}"
        await interaction.response.send_modal(WriterVerificationStep1(code))

    @discord.ui.button(label="👥 Join as Guest", style=discord.ButtonStyle.blurple, custom_id="verify_guest_btn")
    async def verify_guest(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuestVerification())


class VerificationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setup_verification", description="Admin: Setup the verification desk buttons")
    @app_commands.default_permissions(administrator=True)
    async def setup_verification(self, interaction: discord.Interaction):
        view = VerificationView()
        embed = discord.Embed(
            title="Welcome to Quill of Zodiac: The Eclipse",
            description=(
                "We're thrilled to have you here! Before you can fully explore the server and join "
                "our community, we need to get you set up.\n\n"
                "**How to Verify:**\n"
                "• **Writers:** Click the **Verify as Writer** button below to link your Webnovel profile. "
                "You'll get access to exclusive writing tools, tracking, and our writer community.\n\n"
                "• **Guests:** Just here to read, hang out, or support a friend? Click the **Join as Guest** button "
                "to get started."
            ),
            color=0x2b2d31  # Sleek dark grey/black
        )
        embed.set_footer(text="Quill of Zodiac Verification • Powered by Meghdoot 🌩️")
        embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
        
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="verify", description="Start your verification process")
    @app_commands.choices(account_type=[
        app_commands.Choice(name="Writer (I weave tales on Webnovel)", value="writer"),
        app_commands.Choice(name="Guest (I wander the realm to read/chat)", value="guest"),
    ])
    async def verify(self, interaction: discord.Interaction, account_type: str):
        if account_type == "writer":
            code = f"MD-{random.randint(1000,9999)}"
            await interaction.response.send_modal(WriterVerificationStep1(code))
        else:
            await interaction.response.send_modal(GuestVerification())

    @app_commands.command(name="reverify", description="Reset your verification status and start over")
    async def reverify(self, interaction: discord.Interaction):
        conn = sqlite3.connect()
        c = conn.cursor()
        c.execute("DELETE FROM writers WHERE discord_id=?", (interaction.user.id,))
        c.execute("DELETE FROM member_intros WHERE user_id=? AND guild_id=?", (interaction.user.id, interaction.guild_id))
        conn.commit()
        
        # Remove roles
        guild = interaction.guild
        roles_to_remove = ["Verified Author", "Newbie Writer", "Guest"]
        for rname in roles_to_remove:
            role = discord.utils.get(guild.roles, name=rname)
            if role and role in interaction.user.roles:
                try:
                    await interaction.user.remove_roles(role)
                except:
                    pass
        
        await interaction.response.send_message("♻️ Your verification data has been cleared. You can now use `/verify` or the panel to start over.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(VerificationCog(bot))
    # Add the persistent view so buttons work after restart
    bot.add_view(VerificationView())
