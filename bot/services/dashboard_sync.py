"""
dashboard_sync.py — Neon PostgreSQL dashboard sync for NishiBee + HiveGPT.

Replaces any SQLite-only data flow. Call these functions from NishiBee
wherever you update honey, XP, rank, or members, and they will mirror
the data to the Neon dashboard so the website stays live.

Setup:
    1. Set DASHBOARD_API_URL in Render (e.g. https://your-dashboard.example.com)
    2. Set BOT_API_KEY to the dashboard's BOT_API_KEY secret (machine secret for the API — not your admin login password)
    3. import dashboard_sync at the top of nishibee.py / hivegpt.py
    4. Call the async functions with `await` wherever relevant.

All functions are safe to call even if the dashboard is unreachable —
they catch exceptions and log warnings, so your bot won't crash.
"""

import asyncio
import logging
import os
from typing import Optional

import aiohttp

log = logging.getLogger("dashboard_sync")

DASHBOARD_API_URL = os.environ.get("DASHBOARD_API_URL", "").rstrip("/")
BOT_API_KEY       = os.environ.get("BOT_API_KEY", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {BOT_API_KEY}",
        "Content-Type": "application/json",
    }


def _ok() -> bool:
    if not DASHBOARD_API_URL:
        log.debug("DASHBOARD_API_URL not set — dashboard sync skipped.")
        return False
    if not BOT_API_KEY:
        log.warning("BOT_API_KEY not set — dashboard sync disabled.")
        return False
    return True


# ── Member upsert ────────────────────────────────────────────────────────────

async def sync_member(
    discord_id: int | str,
    guild_id: int | str,
    username: str,
    *,
    display_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    honey: Optional[int] = None,
    xp: Optional[int] = None,
    rank: Optional[str] = None,
) -> bool:
    """
    Upsert a member into the dashboard DB.
    Call on join, on rank change, on honey/xp change.
    Returns True on success.
    """
    if not _ok():
        return False
    payload = {
        "discordId": str(discord_id),
        "guildId": str(guild_id),
        "username": username,
    }
    if display_name is not None:
        payload["displayName"] = display_name
    if avatar_url is not None:
        payload["avatarUrl"] = avatar_url
    if honey is not None:
        payload["honey"] = honey
    if xp is not None:
        payload["xp"] = xp
    if rank is not None:
        payload["rank"] = rank

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{DASHBOARD_API_URL}/api/members",
                json=payload,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status not in (200, 201, 204):
                    text = await r.text()
                    log.warning("sync_member failed %d: %s", r.status, text[:200])
                    return False
                return True
    except Exception as exc:
        log.warning("sync_member error: %s", exc)
        return False


# ── Magic link generation for /website command ────────────────────────────────

async def generate_magic_link(
    discord_id: int | str,
    display_name: str,
    username: str,
    avatar_url: Optional[str] = None,
) -> Optional[str]:
    """
    Ask the dashboard API to create a magic login token.
    Returns the full URL string (e.g. https://dashboard/auth/login?token=XXX)
    or None on failure.
    """
    if not _ok():
        return None
    payload = {
        "discordId": str(discord_id),
        "displayName": display_name,
        "username": username,
    }
    if avatar_url:
        payload["avatarUrl"] = avatar_url

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{DASHBOARD_API_URL}/api/auth/token",
                json=payload,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("url")
                text = await r.text()
                log.warning("generate_magic_link failed %d: %s", r.status, text[:200])
                return None
    except Exception as exc:
        log.warning("generate_magic_link error: %s", exc)
        return None


# ── Audit log ────────────────────────────────────────────────────────────────

async def log_action(
    action_type: str,
    actor_id: int | str,
    *,
    actor_name: Optional[str] = None,
    target_id: Optional[int | str] = None,
    target_name: Optional[str] = None,
    guild_id: Optional[int | str] = None,
    details: Optional[str] = None,
) -> bool:
    """
    Post an audit log entry to the dashboard.
    action_type examples: 'give_honey', 'rank_up', 'buy_item', 'warn_member', etc.
    """
    if not _ok():
        return False
    payload = {
        "actionType": action_type,
        "actorId": str(actor_id),
    }
    if actor_name:
        payload["actorName"] = actor_name
    if target_id:
        payload["targetId"] = str(target_id)
    if target_name:
        payload["targetName"] = target_name
    if guild_id:
        payload["guildId"] = str(guild_id)
    if details:
        payload["details"] = details

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{DASHBOARD_API_URL}/api/audit-log",
                json=payload,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                return r.status in (200, 201, 204)
    except Exception as exc:
        log.debug("log_action error (non-critical): %s", exc)
        return False


# ── Bot heartbeat ─────────────────────────────────────────────────────────────

async def heartbeat(bot_name: str, guild_count: int = 0) -> bool:
    """
    Send a heartbeat so the dashboard shows each bot as online.
    Call this from a tasks.loop() every ~3 minutes.
    """
    if not _ok():
        return False
    payload = {
        "botName": bot_name,
        "status": "online",
        "guildCount": guild_count,
    }
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{DASHBOARD_API_URL}/api/bot-status",
                json=payload,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                return r.status in (200, 201, 204)
    except Exception as exc:
        log.debug("heartbeat error: %s", exc)
        return False


# ── Shop sync ─────────────────────────────────────────────────────────────────

async def sync_shop_item(
    name: str,
    price: int,
    *,
    description: Optional[str] = None,
    emoji: Optional[str] = None,
    item_id: Optional[int] = None,
) -> bool:
    """
    Create or update a shop item on the dashboard.
    """
    if not _ok():
        return False
    payload = {"name": name, "price": price}
    if description:
        payload["description"] = description
    if emoji:
        payload["emoji"] = emoji

    try:
        async with aiohttp.ClientSession() as sess:
            if item_id:
                async with sess.put(
                    f"{DASHBOARD_API_URL}/api/admin/shop/{item_id}",
                    json=payload,
                    headers=_headers(),
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as r:
                    return r.status in (200, 201, 204)
            else:
                async with sess.post(
                    f"{DASHBOARD_API_URL}/api/admin/shop",
                    json=payload,
                    headers=_headers(),
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as r:
                    return r.status in (200, 201, 204)
    except Exception as exc:
        log.warning("sync_shop_item error: %s", exc)
        return False
