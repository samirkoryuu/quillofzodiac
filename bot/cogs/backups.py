import discord
from discord.ext import commands, tasks
from discord import app_commands
import io
import csv
import datetime
from core import pgcompat as sqlite3

class BackupsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.weekly_backup.start()

    def cog_unload(self):
        self.weekly_backup.cancel()

    @tasks.loop(hours=168) # 7 days
    async def weekly_backup(self):
        """Runs once a week to dump DB tables to CSV and send to #backups."""
        await self.bot.wait_until_ready()
        
        for guild in self.bot.guilds:
            backup_channel = discord.utils.get(guild.text_channels, name="backups")
            if not backup_channel:
                continue
                
            try:
                files = await self.generate_backup_files()
                if files:
                    await backup_channel.send(f"📦 **Automated Weekly Backup** - {datetime.date.today().isoformat()}", files=files)
            except Exception as e:
                print(f"[BACKUP ERROR] {e}")

    @weekly_backup.before_loop
    async def before_weekly_backup(self):
        await self.bot.wait_until_ready()

    async def generate_backup_files(self):
        conn = sqlite3.connect()
        c = conn.cursor()
        
        tables = ["writers", "economy", "streaks"]
        files = []
        
        for table in tables:
            try:
                # Use scatter_gather to pull from both Shards if Dual-DB is active
                rows = sqlite3.scatter_gather(f"SELECT * FROM {table}")
                
                if not rows:
                    continue
                    
                # Hacky way to get column names: query one shard limit 0
                c.execute(f"SELECT * FROM {table} LIMIT 0")
                col_names = [desc[0] for desc in c.description] if c.description else ["col"+str(i) for i in range(len(rows[0]))]

                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(col_names)
                for row in rows:
                    writer.writerow(row)
                
                output.seek(0)
                file = discord.File(fp=io.BytesIO(output.getvalue().encode('utf-8')), filename=f"{table}_backup_{datetime.date.today().isoformat()}.csv")
                files.append(file)
            except Exception as e:
                print(f"[BACKUP] Error backing up {table}: {e}")
                
        c.close()
        return files

    @app_commands.command(name="backup_db", description="Founder: Manually trigger a CSV database backup")
    @app_commands.default_permissions(administrator=True)
    async def backup_db(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        files = await self.generate_backup_files()
        
        if files:
            # Also try to log it to the backups channel if it exists
            backup_ch = discord.utils.get(interaction.guild.text_channels, name="backups")
            if backup_ch:
                await backup_ch.send(f"📦 **Manual Backup Triggered by {interaction.user.display_name}**", files=files)
                await interaction.followup.send(f"✅ Backup generated and sent to {backup_ch.mention}.")
            else:
                await interaction.followup.send("✅ Backup generated successfully.", files=files)
        else:
            await interaction.followup.send("⚠️ Failed to generate backup files. Tables might be empty.")

async def setup(bot):
    await bot.add_cog(BackupsCog(bot))
