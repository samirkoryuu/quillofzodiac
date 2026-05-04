import datetime
import discord

# ── BRAND COLOURS ─────────────────────────────────────────────────────────────
BEE_GOLD   = 0xFFC300   # NishiBee — amber honey gold
BEE_ERROR  = 0xE74C3C   # NishiBee errors / warnings
GPT_VIOLET = 0x7B2FBE   # HiveGPT  — royal violet
GPT_ERROR  = 0xC0392B   # HiveGPT errors
MEGHDOOT_BLUE = 0x3498DB # Meghdoot — mystical blue
PREMAEL_PINK   = 0xE91E63 # Premael  — romantic rose pink

SERVER_NAME = "Quill of Zodiac: The Eclipse"
BEE_NAME    = "NishiBee"
GPT_NAME    = "HiveGPT"
MEGHDOOT_NAME = "Meghdoot"
PREMAEL_NAME  = "Premael"

def _now_str() -> str:
    return datetime.datetime.utcnow().strftime("%d %b %Y • %H:%M UTC")

class _Bee:
    """Factory for NishiBee embeds."""
    def base(self, title: str = "", description: str = "") -> discord.Embed:
        e = discord.Embed(title=title, description=description, color=BEE_GOLD)
        e.set_footer(text=f"🐝 {BEE_NAME} • {SERVER_NAME}  •  {_now_str()}")
        return e

    def error(self, title: str, description: str) -> discord.Embed:
        e = discord.Embed(title=f"⚠️ {title}", description=description, color=BEE_ERROR)
        e.set_footer(text=f"🐝 {BEE_NAME} • {_now_str()}")
        return e

    def success(self, title: str, description: str = "") -> discord.Embed:
        e = discord.Embed(title=f"✅ {title}", description=description, color=BEE_GOLD)
        e.set_footer(text=f"🐝 {BEE_NAME} • {SERVER_NAME}  •  {_now_str()}")
        return e

    def honey_card(self, member: discord.Member, honey: int, xp: int, level: int, title: str | None, badge_str: str, next_xp: int) -> discord.Embed:
        filled = int((xp / max(next_xp, 1)) * 10)
        bar = "🟡" * filled + "⬜" * (10 - filled)
        e = discord.Embed(title=f"🍯 {member.display_name}'s Honey Card {badge_str}", color=BEE_GOLD)
        if title: e.description = f"*{title}*"
        e.add_field(name="🍯 Honey", value=f"**{honey:,}**", inline=True)
        e.add_field(name="🐝 Level", value=f"**{level}**", inline=True)
        e.add_field(name="⚡ XP", value=f"**{xp:,}** / {next_xp:,}", inline=True)
        e.add_field(name="Progress", value=bar, inline=False)
        if member.avatar: e.set_thumbnail(url=member.avatar.url)
        return e

class _Meghdoot:
    """Factory for Meghdoot embeds."""
    def base(self, title: str = "", description: str = "") -> discord.Embed:
        e = discord.Embed(title=title, description=description, color=MEGHDOOT_BLUE)
        e.set_footer(text=f"🐉 {MEGHDOOT_NAME} • {SERVER_NAME}  •  {_now_str()}")
        return e

    def error(self, title: str, description: str) -> discord.Embed:
        e = discord.Embed(title=f"⚠️ {title}", description=description, color=BEE_ERROR)
        e.set_footer(text=f"🐉 {MEGHDOOT_NAME} • {_now_str()}")
        return e

class _GPT:
    """Factory for HiveGPT embeds."""
    def base(self, title: str = "", description: str = "") -> discord.Embed:
        e = discord.Embed(title=title, description=description, color=GPT_VIOLET)
        e.set_footer(text=f"✨ {GPT_NAME} • {SERVER_NAME}  •  {_now_str()}")
        return e

class _Premael:
    """Factory for Premael embeds."""
    def base(self, title: str = "", description: str = "") -> discord.Embed:
        e = discord.Embed(title=title, description=description, color=PREMAEL_PINK)
        e.set_footer(text=f"🌹 {PREMAEL_NAME} • {SERVER_NAME}  •  {_now_str()}")
        return e

    def error(self, title: str, description: str) -> discord.Embed:
        e = discord.Embed(title=f"⚠️ {title}", description=description, color=BEE_ERROR)
        e.set_footer(text=f"🌹 {PREMAEL_NAME} • {_now_str()}")
        return e

bee = _Bee()
meghdoot = _Meghdoot()
premael = _Premael()
gpt = _GPT()
