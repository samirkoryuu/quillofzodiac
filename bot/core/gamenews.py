"""
core/gamenews.py
================
Pure game-session tracking logic for The Hive.

No discord.py imports. Any action that would send a message instead
calls a callback passed in at construction time.

Public API
----------
GameNewsStore(conn, c, *, add_honey_cb, add_xp_cb)
    .open_session(discord_id, guild_id, game, started_at)
    .close_session(discord_id, guild_id, now) -> SessionResult | None
    .honey_for_minutes(discord_id, guild_id, minutes) -> int
    .check_player_tier(discord_id, guild_id) -> list[tuple[int, str]]
    .log_brag(discord_id, guild_id, game, achievement, image_url, verdict)
    .reap_stale_sessions(cutoff) -> list[tuple[int, int]]
    .ensure_schema()
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger("core.gamenews")

SESSION_MIN_MINUTES = int(os.environ.get("GAMENEWS_SESSION_MIN_MINUTES", "5"))
SESSION_MAX_HONEY_PER_DAY = int(os.environ.get("GAMENEWS_DAILY_CAP", "200"))
HONEY_PER_10_MINUTES = int(os.environ.get("GAMENEWS_HONEY_PER_10_MIN", "5"))

STREAK_MILESTONES = {3, 7, 14, 30, 60, 100, 200, 365}

PLAYER_LADDER = [
    (1,    "🎮 First Boot"),
    (5,    "🎮 Casual Gamer"),
    (20,   "🎮 Regular Gamer"),
    (50,   "🎮 Dedicated Gamer"),
    (100,  "🎮 Hardcore Gamer"),
    (250,  "🎮 Legendary Gamer"),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS game_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id   INTEGER NOT NULL,
    guild_id     INTEGER NOT NULL,
    game         TEXT NOT NULL,
    started_at   INTEGER NOT NULL,
    ended_at     INTEGER,
    minutes      INTEGER DEFAULT 0,
    honey_award  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_member
    ON game_sessions(discord_id, guild_id, started_at);

CREATE TABLE IF NOT EXISTS game_session_open (
    discord_id   INTEGER NOT NULL,
    guild_id     INTEGER NOT NULL,
    game         TEXT NOT NULL,
    started_at   INTEGER NOT NULL,
    PRIMARY KEY (discord_id, guild_id)
);

CREATE TABLE IF NOT EXISTS gamenews_player_tier (
    discord_id   INTEGER NOT NULL,
    guild_id     INTEGER NOT NULL,
    tier_hours   INTEGER NOT NULL,
    awarded_at   INTEGER NOT NULL,
    PRIMARY KEY (discord_id, guild_id, tier_hours)
);

CREATE TABLE IF NOT EXISTS gamenews_streak_announce (
    discord_id   INTEGER NOT NULL,
    guild_id     INTEGER NOT NULL,
    days         INTEGER NOT NULL,
    announced_at INTEGER NOT NULL,
    PRIMARY KEY (discord_id, guild_id, days)
);

CREATE TABLE IF NOT EXISTS gamenews_brag_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id   INTEGER NOT NULL,
    guild_id     INTEGER NOT NULL,
    game         TEXT,
    achievement  TEXT,
    image_url    TEXT,
    verdict      TEXT,
    posted_at    INTEGER NOT NULL
);
"""


def _today_key() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().strftime("%Y-%m-%d")


@dataclass
class SessionResult:
    """Returned by close_session when a qualifying session ends."""
    discord_id: int
    guild_id: int
    game: str
    minutes: int
    honey_earned: int


class GameNewsStore:
    """
    Pure-Python game-session store.

    Callbacks (all optional, called synchronously):
      add_honey_cb(discord_id, guild_id, amount)
      add_xp_cb(discord_id, guild_id, amount) -> (leveled_up, new_level)
    """

    def __init__(self, conn, c, *,
                 add_honey_cb: Optional[Callable] = None,
                 add_xp_cb: Optional[Callable] = None):
        self.conn = conn
        self.c = c
        self._add_honey = add_honey_cb
        self._add_xp = add_xp_cb
        self._daily: dict[tuple[int, int, str], int] = {}
        self.ensure_schema()

    def ensure_schema(self) -> None:
        self.c.executescript(_SCHEMA)
        self.conn.commit()

    def open_session(self, discord_id: int, guild_id: int, game: str, started_at: int) -> None:
        try:
            self.c.execute(
                "INSERT OR REPLACE INTO game_session_open VALUES (?,?,?,?)",
                (discord_id, guild_id, game, started_at),
            )
            self.conn.commit()
        except Exception as e:
            log.warning("open_session failed: %s", e)

    def close_session(self, discord_id: int, guild_id: int, now: Optional[int] = None) -> Optional[SessionResult]:
        """
        Close an open session. Returns a SessionResult if it qualifies
        (>= SESSION_MIN_MINUTES), or None if the session was too short
        or didn't exist.
        """
        if now is None:
            now = int(time.time())
        try:
            row = self.c.execute(
                "SELECT game, started_at FROM game_session_open "
                "WHERE discord_id=? AND guild_id=?",
                (discord_id, guild_id),
            ).fetchone()
            if not row:
                return None
            game, started_at = row[0], int(row[1])
            self.c.execute(
                "DELETE FROM game_session_open WHERE discord_id=? AND guild_id=?",
                (discord_id, guild_id),
            )
            minutes = max(0, (now - started_at) // 60)
            honey = self.honey_for_minutes(discord_id, guild_id, minutes)
            self.c.execute(
                "INSERT INTO game_sessions "
                "(discord_id, guild_id, game, started_at, ended_at, minutes, honey_award) "
                "VALUES (?,?,?,?,?,?,?)",
                (discord_id, guild_id, game, started_at, now, minutes, honey),
            )
            self.conn.commit()

            if minutes >= SESSION_MIN_MINUTES:
                if honey and self._add_honey:
                    try:
                        self._add_honey(discord_id, guild_id, honey)
                    except Exception as e:
                        log.warning("add_honey callback failed: %s", e)
                if self._add_xp:
                    try:
                        self._add_xp(discord_id, guild_id, min(60, minutes))
                    except Exception:
                        pass
                return SessionResult(
                    discord_id=discord_id,
                    guild_id=guild_id,
                    game=game,
                    minutes=minutes,
                    honey_earned=honey,
                )
        except Exception as e:
            log.warning("close_session failed: %s", e)
        return None

    def honey_for_minutes(self, discord_id: int, guild_id: int, minutes: int) -> int:
        if minutes < SESSION_MIN_MINUTES:
            return 0
        proposed = (minutes // 10) * HONEY_PER_10_MINUTES
        if proposed <= 0:
            return 0
        key = (discord_id, guild_id, _today_key())
        already = self._daily.get(key, 0)
        room = max(0, SESSION_MAX_HONEY_PER_DAY - already)
        award = min(proposed, room)
        if award > 0:
            self._daily[key] = already + award
        return award

    def check_player_tier(self, discord_id: int, guild_id: int) -> list[tuple[int, str]]:
        """
        Return list of (hours, label) for newly unlocked player tiers.
        Marks them as awarded in the DB.
        """
        total_minutes = self.c.execute(
            "SELECT COALESCE(SUM(minutes),0) FROM game_sessions "
            "WHERE discord_id=? AND guild_id=?",
            (discord_id, guild_id),
        ).fetchone()[0]
        total_hours = total_minutes // 60
        newly_unlocked = []
        for hours, label in PLAYER_LADDER:
            if total_hours < hours:
                continue
            already = self.c.execute(
                "SELECT 1 FROM gamenews_player_tier "
                "WHERE discord_id=? AND guild_id=? AND tier_hours=?",
                (discord_id, guild_id, hours),
            ).fetchone()
            if already:
                continue
            self.c.execute(
                "INSERT INTO gamenews_player_tier VALUES (?,?,?,?)",
                (discord_id, guild_id, hours, int(time.time())),
            )
            self.conn.commit()
            newly_unlocked.append((hours, label))
        return newly_unlocked

    def should_announce_streak(self, discord_id: int, guild_id: int, days: int) -> bool:
        """Return True if this streak milestone hasn't been announced yet."""
        if days not in STREAK_MILESTONES:
            return False
        seen = self.c.execute(
            "SELECT 1 FROM gamenews_streak_announce WHERE discord_id=? AND guild_id=? AND days=?",
            (discord_id, guild_id, days),
        ).fetchone()
        if seen:
            return False
        self.c.execute(
            "INSERT INTO gamenews_streak_announce VALUES (?,?,?,?)",
            (discord_id, guild_id, days, int(time.time())),
        )
        self.conn.commit()
        return True

    def log_brag(self, discord_id: int, guild_id: int, game: str,
                 achievement: str, image_url: str, verdict: str) -> None:
        try:
            self.c.execute(
                "INSERT INTO gamenews_brag_log "
                "(discord_id, guild_id, game, achievement, image_url, verdict, posted_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (discord_id, guild_id, game, achievement, image_url, verdict, int(time.time())),
            )
            self.conn.commit()
        except Exception as e:
            log.warning("log_brag failed: %s", e)

    def reap_stale_sessions(self, cutoff: int) -> list[tuple[int, int]]:
        """
        Return (discord_id, guild_id) pairs for sessions open since before cutoff.
        Caller is responsible for closing them via close_session().
        """
        rows = self.c.execute(
            "SELECT discord_id, guild_id FROM game_session_open WHERE started_at < ?",
            (cutoff,),
        ).fetchall()
        return [(int(r[0]), int(r[1])) for r in rows]
