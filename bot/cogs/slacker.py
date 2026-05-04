"""
slacker.py
==========
Slacker Bee enforcement for The Hive.

When a writer misses a Blessed Bee ticket deadline they get hit with the
Slacker Bee role. Per the Hive's vision, that means:

  1. Their writer rank role is TEMPORARILY swapped down by one tier
     (Verified Author → Newbie Writer; Newbie Writer keeps Newbie).
     Their previous rank is remembered in `slacker_state` so we can
     restore it instantly when they catch up.
  2. EVERY channel except #ticket-room becomes read-only for them.
     They can SEE everything (no hidden channels), they just can't
     speak or react. Implemented by adding the Slacker Bee role to a
     guild-wide @everyone-style overwrite that denies send_messages,
     add_reactions, create_public_threads, send_messages_in_threads,
     and use_application_commands. #ticket-room re-allows all of these.
  3. Honey, XP, level, badges, books, streaks, prior submissions —
     ALL preserved. Nothing is wiped. Slacker is a pause, not a reset.
  4. HiveGPT must confirm submitted parts in the ticket channel
     before they count toward catching up. (HiveGPT calls
     `mark_back_in_good_standing(...)` here once it's satisfied.)

Public surface
--------------
    await mark_as_slacker(member, *, conn, c, ticket_id=None)
    await lift_slacker(member, *, conn, c)
    apply_slacker_overwrites_to_guild(guild, *, dry_run=False)
       — call once on bootstrap to ensure every existing channel has
         the right overwrite. Idempotent.
    apply_slacker_overwrites_to_channel(channel)
       — hook into on_guild_channel_create.
"""
from __future__ import annotations

import logging
from core import pgcompat as sqlite3
import time
from typing import Optional

import discord

log = logging.getLogger("slacker")

SLACKER_ROLE_NAME = "Slacker Bee"
TICKET_CHANNEL_NAME = "ticket-room"

# rank ladder, highest → lowest. Keep in sync with honeybee.RANK_TIERS.
RANK_LADDER = [
    "Supreme Godscribe",
    "Master Godscribe",
    "Godscribe",
    "Transcendent",
    "Divine",
    "Astral",
    "Celestial",
    "Legend",
    "Ethereal",
    "Grandscribe",
    "Wordlord",
    "Archscribe",
    "Mastermind",
    "Sage",
    "Scribe",
    "Chronicler",
    "Wordsmith III",
    "Wordsmith II",
    "Wordsmith I",
    "Storyteller III",
    "Storyteller II",
    "Storyteller I",
    "Quillbearer III",
    "Quillbearer II",
    "Quillbearer I",
    "Inkling",
    "Newbie Writer",
]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS slacker_state (
    discord_id      INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    original_rank   TEXT,
    demoted_to      TEXT,
    started_at      INTEGER NOT NULL,
    ticket_id       INTEGER,
    PRIMARY KEY (discord_id, guild_id)
);
"""


def ensure_schema(c: sqlite3.Cursor, conn: sqlite3.Connection) -> None:
    c.executescript(_SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# Role helpers
# ---------------------------------------------------------------------------

def _current_rank_role(member: discord.Member) -> Optional[discord.Role]:
    """Return the highest writer-rank role this member currently holds."""
    held = {r.name: r for r in member.roles}
    for name in RANK_LADDER:
        if name in held:
            return held[name]
    return None


def _next_rank_below(rank_name: str) -> str:
    """One tier lower; Newbie Writer stays Newbie."""
    try:
        i = RANK_LADDER.index(rank_name)
    except ValueError:
        return "Newbie Writer"
    if i >= len(RANK_LADDER) - 1:
        return "Newbie Writer"
    return RANK_LADDER[i + 1]


def _slacker_overwrite(guild: discord.Guild) -> Optional[discord.Role]:
    return discord.utils.get(guild.roles, name=SLACKER_ROLE_NAME)


# ---------------------------------------------------------------------------
# Channel-overwrite enforcement
# ---------------------------------------------------------------------------

_DENY_KW = dict(
    send_messages=False,
    send_messages_in_threads=False,
    create_public_threads=False,
    create_private_threads=False,
    add_reactions=False,
    use_application_commands=False,
)

_ALLOW_KW = dict(
    send_messages=True,
    send_messages_in_threads=True,
    create_public_threads=True,
    create_private_threads=True,
    add_reactions=True,
    use_application_commands=True,
)


async def apply_slacker_overwrites_to_channel(
    channel: discord.abc.GuildChannel,
) -> None:
    """Enforce 'Slacker Bee can't post here' on a single channel.

    Idempotent. If the channel is the ticket room (or one of the
    explicitly-allowed channels), allow posting instead.
    """
    if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel,
                                discord.CategoryChannel,
                                discord.ForumChannel, discord.Thread)):
        return
    role = _slacker_overwrite(channel.guild)
    if role is None:
        return
    name = (channel.name or "").lower()
    is_allowed = name in (TICKET_CHANNEL_NAME, "helpbee")
    overwrite = discord.PermissionOverwrite(**(_ALLOW_KW if is_allowed else _DENY_KW))
    # view_channel is intentionally untouched — slackers should still SEE
    # everything to feel the FOMO.
    overwrite.view_channel = True
    try:
        current = channel.overwrites_for(role)
        # avoid an API call if nothing actually changed
        if (current.send_messages == overwrite.send_messages
            and current.add_reactions == overwrite.add_reactions
            and current.use_application_commands == overwrite.use_application_commands
            and current.view_channel is True):
            return
        await channel.set_permissions(role, overwrite=overwrite,
                                      reason="Slacker Bee enforcement")
    except discord.Forbidden:
        log.warning("slacker: missing perms to overwrite #%s", channel.name)
    except Exception as e:
        log.warning("slacker: overwrite failed for #%s: %s", channel.name, e)


async def apply_slacker_overwrites_to_guild(
    guild: discord.Guild, *, dry_run: bool = False,
) -> int:
    """Apply slacker overwrites across every channel in the guild.

    Returns the number of channels touched. Safe to call on bootstrap.
    """
    role = _slacker_overwrite(guild)
    if role is None:
        log.info("slacker: no '%s' role in guild %s — skipping",
                 SLACKER_ROLE_NAME, guild.name)
        return 0
    if dry_run:
        return len(guild.channels)
    n = 0
    for ch in guild.channels:
        await apply_slacker_overwrites_to_channel(ch)
        n += 1
    log.info("slacker: applied overwrites to %d channels in %s", n, guild.name)
    return n


# ---------------------------------------------------------------------------
# Mark / lift API
# ---------------------------------------------------------------------------

async def mark_as_slacker(
    member: discord.Member, *,
    conn: sqlite3.Connection,
    c: sqlite3.Cursor,
    ticket_id: Optional[int] = None,
) -> dict:
    """Demote `member` to Slacker Bee with one-rank role-down.

    Returns: {"ok": bool, "old_rank": str, "demoted_to": str, "msg": str}
    """
    ensure_schema(c, conn)
    guild = member.guild
    slacker_role = _slacker_overwrite(guild)
    if slacker_role is None:
        return {"ok": False, "msg": f"No '{SLACKER_ROLE_NAME}' role on this guild"}

    cur_rank_role = _current_rank_role(member)
    cur_rank = cur_rank_role.name if cur_rank_role else "Newbie Writer"
    target_rank = _next_rank_below(cur_rank)
    target_role = discord.utils.get(guild.roles, name=target_rank)

    # 1) Remember the original rank so we can restore it later.
    c.execute(
        "INSERT OR REPLACE INTO slacker_state "
        "(discord_id, guild_id, original_rank, demoted_to, started_at, ticket_id) "
        "VALUES (?,?,?,?,?,?)",
        (member.id, guild.id, cur_rank, target_rank, int(time.time()), ticket_id),
    )
    conn.commit()

    # 2) Apply role swap.
    try:
        if cur_rank_role and cur_rank_role.name != target_rank:
            await member.remove_roles(cur_rank_role, reason="Slacker Bee demotion")
        if target_role and target_role not in member.roles:
            await member.add_roles(target_role, reason="Slacker Bee demotion")
        if slacker_role not in member.roles:
            await member.add_roles(slacker_role, reason="Slacker Bee penalty")
    except discord.Forbidden:
        return {"ok": False, "msg": "Bot is missing role-management permissions."}
    except Exception as e:
        log.warning("slacker mark failed: %s", e)
        return {"ok": False, "msg": str(e)}

    # 3) Make sure overwrites are in place (cheap; idempotent).
    await apply_slacker_overwrites_to_guild(guild)

    return {
        "ok": True, "old_rank": cur_rank, "demoted_to": target_rank,
        "msg": (f"🐝 You've been marked **Slacker Bee**. Your rank is "
                f"temporarily **{cur_rank} → {target_rank}** until you "
                f"submit your missed ticket parts. Your honey, badges, "
                f"books and streaks stay safe. Head to #ticket-room — "
                f"that's the only channel you can post in until you "
                f"catch up.")
    }


async def lift_slacker(
    member: discord.Member, *,
    conn: sqlite3.Connection,
    c: sqlite3.Cursor,
) -> dict:
    """Restore the member's original rank and clear Slacker Bee."""
    ensure_schema(c, conn)
    guild = member.guild
    row = c.execute(
        "SELECT original_rank, demoted_to FROM slacker_state "
        "WHERE discord_id=? AND guild_id=?",
        (member.id, guild.id),
    ).fetchone()
    if not row:
        return {"ok": False, "msg": "Member is not currently a Slacker Bee."}

    original_rank, demoted_to = row[0], row[1]
    slacker_role = _slacker_overwrite(guild)
    orig_role = discord.utils.get(guild.roles, name=original_rank) if original_rank else None
    demoted_role = discord.utils.get(guild.roles, name=demoted_to) if demoted_to else None

    try:
        if slacker_role and slacker_role in member.roles:
            await member.remove_roles(slacker_role, reason="Slacker Bee lifted")
        if demoted_role and demoted_role in member.roles and original_rank != demoted_to:
            await member.remove_roles(demoted_role, reason="Restoring original rank")
        if orig_role and orig_role not in member.roles:
            await member.add_roles(orig_role, reason="Restoring original rank")
    except discord.Forbidden:
        return {"ok": False, "msg": "Bot is missing role-management permissions."}
    except Exception as e:
        log.warning("slacker lift failed: %s", e)
        return {"ok": False, "msg": str(e)}

    c.execute(
        "DELETE FROM slacker_state WHERE discord_id=? AND guild_id=?",
        (member.id, guild.id),
    )
    conn.commit()

    return {"ok": True, "restored_rank": original_rank,
            "msg": (f"🌅 Welcome back, **{original_rank}**! "
                    f"Slacker Bee status lifted. All your honey + badges "
                    f"are right where you left them. 🍯")}


def is_slacker(member: discord.Member) -> bool:
    return any(r.name == SLACKER_ROLE_NAME for r in member.roles)

async def setup(bot):
    # This cog is primarily helper-based, but we can register it for future use
    pass
