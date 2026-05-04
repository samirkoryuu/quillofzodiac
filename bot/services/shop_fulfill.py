"""
Shared Honey Shop fulfillment after honey is spent (slash /buy + HiveGPT /babysit).
Imports nishibee lazily for DB/economy helpers to avoid circular imports at startup.
"""
from __future__ import annotations

import datetime
import random
from typing import Tuple

import discord
from discord.ext import commands


async def fulfill_slash_shop_buy(
    bot: commands.Bot,
    member: discord.Member,
    guild: discord.Guild,
    item: dict,
    item_id: str,
    new_balance: int,
) -> Tuple[discord.Embed, bool]:
    """
    Run the same side-effects as slash_cog.HoneyCog._handle_buy.
    Returns (embed_to_user, ephemeral_ok_for_slash_response).
    """
    import nishibee as hb
    import messages as M

    kind = item["kind"]

    if kind == "title":
        hb.c.execute(
            "UPDATE economy SET title=? WHERE discord_id=? AND guild_id=?",
            (item["value"], member.id, guild.id),
        )
        hb.conn.commit()
        extra = f"Your new title: **{item['value']}**"
        embed = M.bee.buy_success(member, item["name"], item["price"], new_balance, extra)
        return embed, True

    if kind == "spotlight":
        ch = discord.utils.get(guild.text_channels, name="yourbee") or discord.utils.get(
            guild.text_channels, name="announcements"
        )
        receipt = M.bee.buy_success(
            member, item["name"], item["price"], new_balance, "Spotlight posted in #yourbee!"
        )
        if ch:
            await ch.send(embed=M.bee.spotlight(member))
        return receipt, True

    if kind == "featured-book":
        row = hb.c.execute(
            "SELECT book_url, book_name FROM books WHERE discord_id=? AND guild_id=? ORDER BY id DESC LIMIT 1",
            (member.id, guild.id),
        ).fetchone()
        if not row:
            hb._add_honey(member.id, guild.id, item["price"])
            err = M.bee.error(
                "No Books Found",
                "You don't have any tracked books. Use `/addbook` first. Refunded!",
            )
            return err, True
        url, name = row
        ch = discord.utils.get(guild.text_channels, name="yourbee") or discord.utils.get(
            guild.text_channels, name="announcements"
        )
        receipt = M.bee.buy_success(
            member, item["name"], item["price"], new_balance, f"Featured: **{name}**"
        )
        if ch:
            e = M.bee.base(title="📖 Featured Book")
            e.description = (
                f"{member.mention}'s book is featured today!\n\n**{name}**\n🔗 [Read it here]({url})"
            )
            await ch.send(embed=e)
        return receipt, True

    if kind == "nickname-color":
        color_roles = {
            "gold": ("Golden Scribe", discord.Color.gold()),
            "purple": ("Purple Pen", discord.Color.purple()),
            "teal": ("Teal Ink", discord.Color.teal()),
        }
        color_val = item.get("value", "gold")
        role_name, role_color = color_roles.get(color_val, ("Golden Scribe", discord.Color.gold()))
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            try:
                role = await guild.create_role(name=role_name, color=role_color, reason="Shop color role")
            except discord.Forbidden:
                err = M.bee.error(
                    "Permission Error",
                    "Couldn't create the color role. Ask a Founder to grant NishiBee Manage Roles.",
                )
                return err, True
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            err = M.bee.error(
                "Permission Error",
                "Couldn't assign the role. Ask a Founder to check bot permissions.",
            )
            return err, True
        embed = M.bee.buy_success(
            member, item["name"], item["price"], new_balance, f"Role **{role_name}** granted!"
        )
        return embed, True

    if kind == "mystery-box":
        outcomes = [
            ("💰 Jackpot!", "You hit the honey jackpot — **+200 🍯**!", 200),
            ("🍯 Honey Rain", "A small shower of honey — **+50 🍯**!", 50),
            ("🎭 Plot Twist", "The box was empty. Better luck next time!", 0),
            ("🌟 Bonus XP", "No honey but +500 XP! Keep climbing!", 0),
        ]
        outcome = random.choice(outcomes)
        title, msg, bonus = outcome
        if bonus:
            hb._add_honey(member.id, guild.id, bonus)
        embed = M.bee.success(title, msg)
        return embed, False

    if kind == "double-honey":
        until = (datetime.datetime.utcnow() + datetime.timedelta(hours=24)).isoformat()
        hb.c.execute(
            "INSERT OR REPLACE INTO double_honey_days (discord_id, guild_id, active_until) VALUES (?, ?, ?)",
            (member.id, guild.id, until),
        )
        hb.conn.commit()
        embed = M.bee.buy_success(
            member,
            item["name"],
            item["price"],
            new_balance,
            "Double honey earnings active for the next 24 hours! 🍯🍯",
        )
        return embed, True

    if kind == "streak-saver":
        hb.c.execute("UPDATE streaks SET current=current+1 WHERE discord_id=?", (member.id,))
        hb.conn.commit()
        embed = M.bee.buy_success(
            member,
            item["name"],
            item["price"],
            new_balance,
            "Your streak has been protected for today! 🛡️",
        )
        return embed, True

    if kind == "friday-drop":
        embed = M.bee.buy_success(
            member,
            item["name"],
            item["price"],
            new_balance,
            "You're entered into this Friday's honey lottery! 🍀 Good luck!",
        )
        return embed, True

    embed = M.bee.buy_success(member, item["name"], item["price"], new_balance)
    return embed, True
