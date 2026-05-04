"""
core/auto_rank.py
=================
Pure rank-evaluation logic for The Hive.

No discord.py imports. Discord-specific operations (role swapping,
announcements) are handled by callbacks passed to AutoRankStore.

Public API
----------
AutoRankStore(conn, c, rank_tiers, tier_for)
    .ensure_schema()
    .latest_screenshot(discord_id, guild_id) -> dict[str, tuple[int,int,int]]
    .compute_totals(discord_id, guild_id, wn_chapters, wn_per_book)
        -> (total_words, total_chapters, shots)
    .record_word_counts(discord_id, guild_id, books, ts)
    .record_audit(discord_id, guild_id, old_rank, new_rank, words, chs, basis)
    .cooldown_ok(discord_id) -> bool
    .mark_rankcheck(discord_id)
    .crosscheck_ocr_vs_snapshot(ocr, snap, pen_name) -> list[str]
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
from typing import Optional

log = logging.getLogger("core.auto_rank")

RANKCHECK_COOLDOWN_DAYS = int(os.environ.get("RANKCHECK_COOLDOWN_DAYS", "3"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS member_word_counts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id      INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    book_title      TEXT,
    chapter_count   INTEGER DEFAULT 0,
    word_count      INTEGER DEFAULT 0,
    source          TEXT,
    raw_payload     TEXT,
    ts              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mwc_member
    ON member_word_counts(discord_id, guild_id, ts);

CREATE TABLE IF NOT EXISTS rank_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id      INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    old_rank        TEXT,
    new_rank        TEXT,
    total_words     INTEGER,
    total_chapters  INTEGER,
    basis           TEXT,
    ts              INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rank_check_requests (
    discord_id      INTEGER PRIMARY KEY,
    last_request_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_rank_announcements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id      INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    old_rank        TEXT,
    new_rank        TEXT,
    total_words     INTEGER,
    total_chapters  INTEGER,
    ts              INTEGER NOT NULL
);
"""


def _norm_title(s: str) -> str:
    return re.sub(r"[^\w\s]", "", (s or "").lower()).strip()


class AutoRankStore:
    """
    Pure-Python rank evaluation and persistence.

    rank_tiers: list of (name, min_words, min_chapters, emoji) tuples,
                ordered highest to lowest (same as nishibee.RANK_TIERS).
    tier_for:   callable(words, chapters) -> (rank_name, emoji)
    """

    def __init__(self, conn, c, rank_tiers: list, tier_for):
        self.conn = conn
        self.c = c
        self.RANK_TIERS = rank_tiers
        self.tier_for = tier_for
        self.rank_names = [r[0] for r in rank_tiers]
        self.tier_emoji = {r[0]: r[3] for r in rank_tiers}
        self.tier_index = {r[0]: i for i, r in enumerate(rank_tiers)}
        self.ensure_schema()

    def ensure_schema(self) -> None:
        self.c.executescript(_SCHEMA)
        self.conn.commit()

    def latest_screenshot(self, discord_id: int, guild_id: int) -> dict[str, tuple[int, int, int]]:
        """Return the most recent word-count entry per book title (last 90 days)."""
        cutoff = int(time.time()) - 90 * 86400
        rows = self.c.execute(
            "SELECT book_title, chapter_count, word_count, ts "
            "FROM member_word_counts WHERE discord_id=? AND guild_id=? AND ts>=? "
            "ORDER BY ts DESC",
            (discord_id, guild_id, cutoff),
        ).fetchall()
        by_title: dict[str, tuple[int, int, int]] = {}
        for r in rows:
            title = r[0] or ""
            ch, wd, ts = int(r[1] or 0), int(r[2] or 0), int(r[3])
            if title not in by_title:
                by_title[title] = (ch, wd, ts)
        return by_title

    def compute_totals(
        self,
        discord_id: int,
        guild_id: int,
        wn_chapters_total: int,
        wn_books_chapters: Optional[dict[str, int]] = None,
    ) -> tuple[int, int, dict]:
        """
        Combine Webnovel-scrape chapter totals with Inkstone screenshot word counts.

        Returns (total_words, total_chapters, shots_by_title).
        """
        shots = self.latest_screenshot(discord_id, guild_id)
        total_words = sum(wd for (_ch, wd, _ts) in shots.values())

        if wn_books_chapters:
            merged: dict[str, int] = dict(wn_books_chapters)
            for title, (ch, _wd, _ts) in shots.items():
                key = title.lower().strip()
                merged[key] = max(int(merged.get(key, 0)), ch)
            total_chapters = sum(merged.values())
        else:
            shot_chapters = sum(ch for (ch, _wd, _ts) in shots.values())
            total_chapters = max(int(wn_chapters_total or 0), shot_chapters)

        return total_words, total_chapters, shots

    def record_word_counts(
        self,
        discord_id: int,
        guild_id: int,
        books: list[dict],
        ts: int,
    ) -> None:
        """
        Persist OCR-extracted book data.

        books: list of {"title": str, "chapters": int, "words": int}
        """
        rows = [
            (discord_id, guild_id,
             b.get("title") or "", int(b.get("chapters") or 0),
             int(b.get("words") or 0), "screenshot",
             json.dumps(b), ts)
            for b in books
        ]
        if rows:
            self.c.executemany(
                "INSERT INTO member_word_counts "
                "(discord_id, guild_id, book_title, chapter_count, word_count, "
                " source, raw_payload, ts) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            self.conn.commit()

    def record_audit(
        self,
        discord_id: int,
        guild_id: int,
        old_rank: Optional[str],
        new_rank: str,
        words: int,
        chapters: int,
        basis: str,
    ) -> None:
        self.c.execute(
            "INSERT INTO rank_audit "
            "(discord_id, guild_id, old_rank, new_rank, total_words, "
            " total_chapters, basis, ts) VALUES (?,?,?,?,?,?,?,?)",
            (discord_id, guild_id, old_rank, new_rank, words, chapters, basis, int(time.time())),
        )
        self.conn.commit()

    def cooldown_ok(self, discord_id: int) -> tuple[bool, int]:
        """
        Return (ok, wait_seconds).
        ok=True means the member may run !rankcheck now.
        """
        now = int(time.time())
        row = self.c.execute(
            "SELECT last_request_ts FROM rank_check_requests WHERE discord_id=?",
            (discord_id,),
        ).fetchone()
        if not row:
            return True, 0
        elapsed = now - int(row[0])
        cooldown = RANKCHECK_COOLDOWN_DAYS * 86400
        if elapsed >= cooldown:
            return True, 0
        return False, cooldown - elapsed

    def mark_rankcheck(self, discord_id: int) -> None:
        self.c.execute(
            "INSERT OR REPLACE INTO rank_check_requests (discord_id, last_request_ts) "
            "VALUES (?, ?)",
            (discord_id, int(time.time())),
        )
        self.conn.commit()

    def queue_pending_announcement(self, discord_id, guild_id, old_rank, new_rank, words, chs):
        self.c.execute(
            "INSERT INTO pending_rank_announcements "
            "(discord_id, guild_id, old_rank, new_rank, total_words, total_chapters, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (discord_id, guild_id, old_rank, new_rank, words, chs, int(time.time()))
        )
        self.conn.commit()

    def list_pending_announcements(self, guild_id):
        return self.c.execute(
            "SELECT * FROM pending_rank_announcements WHERE guild_id=?", (guild_id,)
        ).fetchall()

    def clear_pending_announcement(self, pending_id):
        self.c.execute("DELETE FROM pending_rank_announcements WHERE id=?", (pending_id,))
        self.conn.commit()

    def eligible_rank(self, discord_id: int, guild_id: int,
                      wn_chapters: int = 0,
                      wn_per_book: Optional[dict[str, int]] = None) -> dict:
        """
        Compute the rank a member is eligible for, based on stored data.

        Returns a verdict dict:
            {
                "eligible_rank": str,
                "total_words": int,
                "total_chapters": int,
                "shots": dict,
                "wn_chapters": int,
            }
        """
        total_words, total_chapters, shots = self.compute_totals(
            discord_id, guild_id, wn_chapters, wn_per_book
        )
        rank_name, _ = self.tier_for(total_words, total_chapters)
        return {
            "eligible_rank": rank_name,
            "total_words": total_words,
            "total_chapters": total_chapters,
            "shots": shots,
            "wn_chapters": wn_chapters,
        }

    @staticmethod
    def crosscheck_ocr_vs_snapshot(ocr: dict, snap, pen_name: Optional[str]) -> list[str]:
        """
        Compare OCR output against a live Webnovel scrape snapshot.
        Returns up to 8 human-readable validation lines.
        snap: a WriterSnapshot (or None) from webnovel_tracker.
        """
        lines: list[str] = []
        if snap is None:
            lines.append(
                "⚠️ **Tracker:** no Webnovel scrape on file — verify with `!finish` / `!track` "
                "so we can match Inkstone to live book pages."
            )
            return lines

        books_live = list(snap.books)
        if not books_live:
            lines.append("⚠️ **Live scrape:** no books under Original Works yet.")

        pn = (pen_name or "").strip()
        if pn:
            o_pn = (ocr.get("pen_name") or "").strip()
            raw = (ocr.get("raw_text") or "").lower()
            if o_pn and pn.lower() in o_pn.lower():
                lines.append(f"✅ **Pen name** `{pn}` matches Inkstone.")
            elif pn.lower() in raw:
                lines.append(f"✅ **Pen name** `{pn}` found in screenshot text.")
            else:
                lines.append(
                    f"⚠️ **Pen name check:** DB has `{pn}` — couldn't confirm it clearly "
                    "(still okay if you use a display alias)."
                )

        for ob in ocr.get("books") or []:
            ot = (ob.get("title") or "").strip()
            if not ot or ot == "(totals only)":
                continue
            best = None
            best_r = 0.0
            for b in books_live:
                r = difflib.SequenceMatcher(None, _norm_title(ot), _norm_title(b.title)).ratio()
                if r > best_r:
                    best_r = r
                    best = b
            if best is None or best_r < 0.45:
                lines.append(f"⚠️ **{ot[:40]}** — no close live book title match ({best_r:.0%}).")
                continue
            lines.append(
                f"🔗 **{ot[:55]}** ↔ live **{best.title[:55]}** "
                f"(**{best_r:.0%}**) · Views **{best.views or '?'}** · "
                f"Chapters **{int(ob.get('chapters') or 0)}** (Inkstone) vs "
                f"**{best.chapter_count}** (live)."
            )

        return lines[:8]
