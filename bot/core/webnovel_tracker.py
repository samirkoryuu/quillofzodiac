"""
core/webnovel_tracker.py
========================
Pure Webnovel scraping logic — no discord.py.

Contains:
  - Scraper backends (ScrapingAnt, ScrapFly, ScraperAPI, ScrapeOps, Playwright)
  - RotatingClient  — tries backends in priority order
  - WebnovelClient  — high-level profile + book scraper
  - TrackerStore    — SQLite/Postgres persistence
  - Book, WriterSnapshot dataclasses

The Discord cog that registers commands and tasks lives in cogs/webnovel_tracker.py
and imports from here.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup

from core import pgcompat as sqlite3

log = logging.getLogger("core.webnovel_tracker")
log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ANNOUNCE_CHANNEL_ID  = int(os.environ.get("WN_ANNOUNCE_CHANNEL_ID", "0") or 0)
SUMMARY_CHANNEL_ID   = int(os.environ.get("WN_SUMMARY_CHANNEL_ID",  "0") or 0) or ANNOUNCE_CHANNEL_ID
SUMMARY_HOUR_UTC     = int(os.environ.get("WN_SUMMARY_HOUR_UTC", "13"))
POLL_INTERVAL_HOURS  = max(1, int(os.environ.get("WN_POLL_INTERVAL_HOURS", "11")))
REDISCOVER_HOURS     = max(POLL_INTERVAL_HOURS,
                           int(os.environ.get("WN_REDISCOVER_HOURS", "168")))
DB_PATH              = os.environ.get("WN_DB_PATH", "webnovel_tracker.sqlite3")
BACKEND_ORDER        = [s.strip().lower() for s in os.environ.get(
    "WN_BACKEND_ORDER",
    "selfhosted,scrapingant,scrapfly,scraperapi,scrapeops,playwright"
).split(",") if s.strip()]
USE_PLAYWRIGHT       = os.environ.get("WN_USE_PLAYWRIGHT", "").strip() in ("1", "true", "yes")
SCRAPER_API_URL      = os.environ.get("WN_SCRAPER_API_URL", "").strip().rstrip("/")
SCRAPER_API_KEY      = os.environ.get("WN_SCRAPER_API_KEY", "").strip()

PROFILE_RE   = re.compile(r"webnovel\.com/profile/(\d+)", re.IGNORECASE)
BOOK_HREF_RE = re.compile(r"/book/([^/?#]+?)_(\d+)")
URL_SAFE_CHARS = ":/?#[]@!$&'()*+,;=%~"
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=120)


def _split_keys(env_name: str) -> list[str]:
    seen: list[str] = []
    bundle = os.environ.get(env_name, "")
    if bundle:
        for k in bundle.split(","):
            k = k.strip()
            if k and k not in seen:
                seen.append(k)
    for i in range(2, 21):
        v = os.environ.get(f"{env_name}_{i}", "").strip()
        if v and v not in seen:
            seen.append(v)
    return seen


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class QuotaExceeded(Exception):
    pass


class BackendUnavailable(Exception):
    pass


@dataclass
class BackendStats:
    name: str
    calls: int = 0
    successes: int = 0
    failures: int = 0
    quota_exhausted_keys: set[str] = field(default_factory=set)


class ScraperBackend:
    name: str = "base"
    max_concurrent: int = 1

    def __init__(self, keys: Optional[list[str]] = None):
        self.keys: list[str] = keys or []
        self._cycle = itertools.cycle(self.keys) if self.keys else None
        self.stats = BackendStats(name=self.name)
        env_var = f"WN_{self.name.upper()}_CONCURRENCY"
        n = int(os.environ.get(env_var, str(self.max_concurrent)))
        self._sem = asyncio.Semaphore(max(1, n))

    @property
    def available(self) -> bool:
        if not self.keys:
            return False
        return any(k not in self.stats.quota_exhausted_keys for k in self.keys)

    async def fetch(self, session: aiohttp.ClientSession, url: str) -> str:
        if not self.keys:
            raise BackendUnavailable(f"{self.name}: no API key configured")
        tried = 0
        last_err: Optional[Exception] = None
        for _ in range(len(self.keys)):
            key = next(self._cycle)
            if key in self.stats.quota_exhausted_keys:
                continue
            tried += 1
            self.stats.calls += 1
            try:
                async with self._sem:
                    html = await self._fetch_with_key(session, url, key)
                self.stats.successes += 1
                return html
            except QuotaExceeded:
                self.stats.quota_exhausted_keys.add(key)
                last_err = QuotaExceeded(f"{self.name}: key exhausted")
                log.warning("%s: API key exhausted (one of %d)", self.name, len(self.keys))
                continue
            except Exception as e:
                self.stats.failures += 1
                last_err = e
                log.warning("%s fetch failed: %s", self.name, e)
                continue
        if tried == 0:
            raise QuotaExceeded(f"{self.name}: all keys exhausted")
        raise last_err if last_err else RuntimeError(f"{self.name}: unknown failure")

    async def _fetch_with_key(self, session, url, key) -> str:
        raise NotImplementedError


class ScraperAPIBackend(ScraperBackend):
    name = "scraperapi"

    async def _fetch_with_key(self, session, url, key):
        api = (f"http://api.scraperapi.com/?api_key={key}"
               f"&url={quote(url, safe=URL_SAFE_CHARS)}")
        async with session.get(api) as r:
            if r.status in (401, 402, 403):
                body = await r.text()
                if "credit" in body.lower() or r.status == 401:
                    raise QuotaExceeded(f"scraperapi: {r.status}")
            r.raise_for_status()
            return await r.text()


class ScrapeOpsBackend(ScraperBackend):
    name = "scrapeops"

    async def _fetch_with_key(self, session, url, key):
        api = (f"https://proxy.scrapeops.io/v1/?api_key={key}"
               f"&url={quote(url, safe=URL_SAFE_CHARS)}")
        async with session.get(api) as r:
            if r.status in (401, 402, 429):
                raise QuotaExceeded(f"scrapeops: {r.status}")
            r.raise_for_status()
            return await r.text()


class ScrapFlyBackend(ScraperBackend):
    name = "scrapfly"

    async def _fetch_with_key(self, session, url, key):
        api = (
            f"https://api.scrapfly.io/scrape"
            f"?key={key}"
            f"&url={quote(url, safe=URL_SAFE_CHARS)}"
            f"&asp=true&render_js=true&wait=3000&country=us"
        )
        async with session.get(api, timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status in (401, 402, 429):
                raise QuotaExceeded(f"scrapfly: {r.status}")
            if r.status >= 400:
                body = await r.text()
                if "quota" in body.lower() or "credit" in body.lower():
                    raise QuotaExceeded(f"scrapfly: {r.status}")
                r.raise_for_status()
            data = await r.json()
            content = (data.get("result") or {}).get("content")
            if not content:
                raise RuntimeError(f"scrapfly: missing content: {str(data)[:200]}")
            return content


class ScrapingAntBackend(ScraperBackend):
    name = "scrapingant"

    async def _fetch_with_key(self, session, url, key):
        api = (
            f"https://api.scrapingant.com/v2/general"
            f"?url={quote(url, safe=URL_SAFE_CHARS)}"
            f"&browser=true&proxy_type=datacenter"
            f"&wait_for_selector=%23tabWorks%2C+.det-hd-detail%2C+h1"
        )
        headers = {"x-api-key": key}
        async with session.get(api, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status in (401, 402, 403, 423, 429):
                raise QuotaExceeded(f"scrapingant: {r.status}")
            if r.status >= 400:
                body = await r.text()
                if any(w in body.lower() for w in ("quota", "credit", "limit")):
                    raise QuotaExceeded(f"scrapingant: {r.status}")
                r.raise_for_status()
            return await r.text()


class SelfHostedPlaywrightBackend(ScraperBackend):
    """
    Calls the separate self-hosted Playwright scraper API deployed on Render.

    The API is a FastAPI service (scraper-api/) running a stealth-configured
    Chromium browser in its own Docker container — isolated from the bot process
    so crashes and memory spikes don't affect the Discord bot.

    Set WN_SCRAPER_API_URL to the Render service URL, e.g.:
        WN_SCRAPER_API_URL=https://hiveslave-scraper.onrender.com

    Set WN_SCRAPER_API_KEY to match the SCRAPER_API_KEY on the API service.
    Leave unset on both sides to skip authentication.
    """
    name = "selfhosted"

    def __init__(self, api_url: str, api_key: str = ""):
        super().__init__(keys=["__selfhosted__"] if api_url else [])
        self._api_url = api_url
        self._api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self._api_url)

    async def fetch(self, session: aiohttp.ClientSession, url: str) -> str:
        if not self._api_url:
            raise BackendUnavailable("selfhosted: WN_SCRAPER_API_URL not set")
        self.stats.calls += 1
        headers = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        try:
            async with self._sem:
                async with session.post(
                    f"{self._api_url}/scrape",
                    json={"url": url},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as r:
                    if r.status == 401:
                        raise BackendUnavailable("selfhosted: invalid API key")
                    if r.status == 429:
                        raise QuotaExceeded("selfhosted: rate limited")
                    if r.status >= 500:
                        body = await r.text()
                        raise RuntimeError(f"selfhosted: server error {r.status}: {body[:200]}")
                    r.raise_for_status()
                    data = await r.json()
                    html = data.get("html") or data.get("content", "")
                    if not html:
                        raise RuntimeError("selfhosted: empty HTML in response")
                    self.stats.successes += 1
                    return html
        except (BackendUnavailable, QuotaExceeded):
            raise
        except Exception as e:
            self.stats.failures += 1
            log.warning("selfhosted backend failed for %s: %s", url, e)
            raise


class PlaywrightBackend(ScraperBackend):
    name = "playwright"

    def __init__(self):
        super().__init__(keys=["__local__"])
        self._browser = None
        self._pw = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return USE_PLAYWRIGHT

    async def _ensure_browser(self):
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise BackendUnavailable(
                "playwright not installed. Add 'playwright' to requirements.txt "
                "and run 'playwright install chromium'."
            ) from e
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

    async def _fetch_with_key(self, session, url, key):
        async with self._lock:
            await self._ensure_browser()
            assert self._browser is not None
            ctx = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_selector(
                        "#tabWorks, .det-hd-detail, h1", timeout=15000
                    )
                except Exception:
                    pass
                html = await page.content()
            finally:
                await ctx.close()
        return html

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._browser = None
        self._pw = None


# ---------------------------------------------------------------------------
# Rotating client
# ---------------------------------------------------------------------------

class RotatingClient:
    def __init__(self, backends: list[ScraperBackend],
                 session: Optional[aiohttp.ClientSession] = None):
        self.backends = backends
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self):
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=HTTP_TIMEOUT)
        return self

    async def __aexit__(self, *exc):
        if self._owns_session and self._session:
            await self._session.close()
        for b in self.backends:
            close = getattr(b, "close", None)
            if close:
                try:
                    await close()
                except Exception:
                    pass

    async def fetch(self, url: str) -> str:
        assert self._session is not None
        last_err: Optional[Exception] = None
        for b in self.backends:
            if not b.available:
                continue
            try:
                return await b.fetch(self._session, url)
            except QuotaExceeded as e:
                last_err = e
                log.warning("Backend %s exhausted, trying next", b.name)
                continue
            except BackendUnavailable as e:
                last_err = e
                continue
            except Exception as e:
                last_err = e
                log.warning("Backend %s errored on %s: %s", b.name, url, e)
                continue
        raise RuntimeError(
            f"All scraping backends failed or exhausted. Last error: {last_err}"
        )

    def status_summary(self) -> str:
        lines = []
        for b in self.backends:
            if b.name in ("playwright", "selfhosted"):
                ok = "✅" if b.available else "❌"
                lines.append(
                    f"{ok} **{b.name}** — "
                    f"calls: {b.stats.calls} (✓{b.stats.successes} ✗{b.stats.failures})"
                )
            else:
                total_keys = len(b.keys)
                exhausted = len(b.stats.quota_exhausted_keys)
                available_keys = total_keys - exhausted
                ok = "✅" if b.available and available_keys > 0 else "❌"
                lines.append(
                    f"{ok} **{b.name}** — keys: {available_keys}/{total_keys} live · "
                    f"calls: {b.stats.calls} (✓{b.stats.successes} ✗{b.stats.failures})"
                )
        return "\n".join(lines) or "_(no backends configured)_"


def build_default_client(session: Optional[aiohttp.ClientSession] = None) -> RotatingClient:
    candidates: dict[str, ScraperBackend] = {
        "selfhosted":  SelfHostedPlaywrightBackend(SCRAPER_API_URL, SCRAPER_API_KEY),
        "scraperapi":  ScraperAPIBackend(_split_keys("SCRAPERAPI_KEY")),
        "scrapeops":   ScrapeOpsBackend(_split_keys("SCRAPEOPS_KEY")),
        "scrapfly":    ScrapFlyBackend(_split_keys("SCRAPFLY_KEY")),
        "scrapingant": ScrapingAntBackend(_split_keys("SCRAPINGANT_KEY")),
        "playwright":  PlaywrightBackend(),
    }
    ordered: list[ScraperBackend] = []
    for name in BACKEND_ORDER:
        b = candidates.get(name)
        if b is None:
            continue
        if b.name == "playwright" and not USE_PLAYWRIGHT:
            continue
        if b.name == "selfhosted" and not SCRAPER_API_URL:
            continue
        if b.name not in ("playwright", "selfhosted") and not b.keys:
            continue
        ordered.append(b)
    if not ordered:
        raise RuntimeError(
            "No scraping backend configured. Set WN_SCRAPER_API_URL (recommended), "
            "or set SCRAPERAPI_KEY / SCRAPFLY_KEY / SCRAPINGANT_KEY / SCRAPEOPS_KEY, "
            "or set WN_USE_PLAYWRIGHT=1."
        )
    if SCRAPER_API_URL:
        log.info("Self-hosted scraper backend active: %s", SCRAPER_API_URL)
    return RotatingClient(ordered, session=session)


# ---------------------------------------------------------------------------
# Data model + parser
# ---------------------------------------------------------------------------

@dataclass
class Book:
    book_id: str
    title: str
    url: str
    chapter_count: int = 0
    views: str = ""
    genre: str = ""


@dataclass
class WriterSnapshot:
    profile_id: str
    profile_url: str
    books: list[Book]


class WebnovelClient:
    def __init__(self, rotating: RotatingClient):
        self.rotating = rotating

    @staticmethod
    def parse_profile_id(profile_url_or_id: str) -> Optional[str]:
        s = (profile_url_or_id or "").strip()
        if s.isdigit():
            return s
        m = PROFILE_RE.search(s)
        return m.group(1) if m else None

    async def fetch_writer_snapshot(self, profile_id: str) -> WriterSnapshot:
        profile_url = f"https://www.webnovel.com/profile/{profile_id}"
        
        # 1. Try the stealth scraper's bulk parsing API
        self_hosted = next((b for b in self.rotating.backends if b.name == "selfhosted"), None)
        if self_hosted and self_hosted.available:
            try:
                headers = {"X-API-Key": self_hosted._api_key} if self_hosted._api_key else {}
                async with self.rotating._session.post(
                    f"{self_hosted._api_url}/custom",
                    json={"action": "parse_profile", "url": profile_url},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        if "works" in data:
                            books = []
                            for w in data["works"]:
                                m = BOOK_HREF_RE.search(w["url"])
                                bid = m.group(2) if m else w["url"]
                                books.append(Book(
                                    book_id=bid, title=w["title"], url=w["url"],
                                    chapter_count=w["chapters"], views=w["views"], genre=w["genre"]
                                ))
                            return WriterSnapshot(profile_id=profile_id, profile_url=profile_url, books=books)
            except Exception as e:
                log.debug("scraper-api custom parsing failed: %s", e)

        # 2. Fallback to raw fetch + local parsing
        html = await self.rotating.fetch(profile_url)
        soup = BeautifulSoup(html, "lxml")

        works_pane = soup.select_one("#tabWorks") or soup.select_one(".det-tab-pane")
        if works_pane is None:
            log.warning("Profile %s has no #tabWorks pane", profile_id)
            return WriterSnapshot(profile_id=profile_id, profile_url=profile_url, books=[])

        books: dict[str, Book] = {}
        items = works_pane.select(".m-stories > li") or works_pane.select(".j_workWrap li")
        for li in items:
            a = li.find("a", href=BOOK_HREF_RE)
            if not a: continue
            
            href = a.get("href", "")
            if not href.startswith("http"): href = "https://www.webnovel.com" + href
            
            m = BOOK_HREF_RE.search(href)
            if not m: continue
            bid = m.group(2)
            
            h = li.find(["h2", "h3", "h4"])
            title = (h.get_text(strip=True) if h else a.get_text(" ", strip=True)).strip()
            title = re.sub(r"^\s*original\s+", "", title, flags=re.I).strip()
            if not title: continue
            
            # Metadata extraction
            stats_text = li.get_text(separator=" ", strip=True)
            chapters = 0
            ch_match = re.search(r"(\d+)\s*Chapters", stats_text, re.I)
            if ch_match: chapters = int(ch_match.group(1))
            
            vw_match = re.search(r"([\d\.]+k?|[\d\.]+m?)\s*Views", stats_text, re.I)
            views = vw_match.group(1) if vw_match else "0"
            
            genre_tag = li.select_one(".w-genre") or li.select_one(".genre")
            genre = genre_tag.get_text(strip=True) if genre_tag else "Unknown"

            books[bid] = Book(book_id=bid, title=title, url=href, 
                              chapter_count=chapters, views=views, genre=genre)

        # Still run enrichment for missing data if needed (optional)
        # await asyncio.gather(*(self._enrich_book(b) for b in books.values()), return_exceptions=False)
        
        return WriterSnapshot(profile_id=profile_id, profile_url=profile_url,
                              books=sorted(books.values(), key=lambda b: b.title.lower()))

    async def refresh_known_books(self, profile_id: str,
                                  known_books: list[Book]) -> WriterSnapshot:
        profile_url = f"https://www.webnovel.com/profile/{profile_id}"
        books = [Book(book_id=b.book_id, title=b.title, url=b.url,
                      chapter_count=b.chapter_count, views=b.views, genre=b.genre)
                 for b in known_books]
        await asyncio.gather(*(self._enrich_book(b) for b in books), return_exceptions=False)
        return WriterSnapshot(profile_id=profile_id, profile_url=profile_url,
                              books=sorted(books, key=lambda b: b.title.lower()))

    async def _enrich_book(self, book: Book) -> None:
        try:
            html = await self.rotating.fetch(book.url)
        except Exception as e:
            log.warning("Failed to fetch book %s (%s): %s", book.book_id, book.title, e)
            return
        soup = BeautifulSoup(html, "lxml")
        h1 = soup.find("h1")
        if h1:
            t = h1.get_text(strip=True)
            if t:
                book.title = t
        m = (re.search(r'"chapterNum":(\d+)', html)
             or re.search(r'"totalChapterNum":(\d+)', html))
        if m:
            book.chapter_count = int(m.group(1))
        else:
            mt = re.search(r'>([\d,]+)\s+Chapters?<', html)
            if mt:
                book.chapter_count = int(mt.group(1).replace(",", ""))
        mv = re.search(r'>([\d.,KMB]+)\s+Views?<', html)
        if mv:
            book.views = mv.group(1)
        det = soup.select_one(".det-hd-detail")
        if det:
            txt = det.get_text(" ", strip=True)
            mg = re.match(r"([A-Za-z][A-Za-z &/]+?)\s+\d", txt)
            if mg:
                book.genre = mg.group(1).strip()


# ---------------------------------------------------------------------------
# DB store (pure, no discord)
# ---------------------------------------------------------------------------

@dataclass
class ChapterChangeEvent:
    """
    Emitted by TrackerStore.update_books() when a tracked book gains chapters.

    The Discord cog receives a list of these instead of raw (Book, prev_count)
    tuples, so it never needs to import or know about the internal tuple shape.

    Testability
    -----------
    Because this is a plain dataclass (no discord), tests can fabricate events
    and pass them straight into the cog's announcement callback:

        evt = ChapterChangeEvent(book=b, prev_chapters=5, new_chapters=8)
        assert evt.added == 3
    """
    book: "Book"
    prev_chapters: int
    new_chapters: int

    @property
    def added(self) -> int:
        """Number of chapters gained since the last poll."""
        return self.new_chapters - self.prev_chapters


class TrackerStore:
    """
    SQLite/Postgres persistence for the Webnovel tracker.

    Parameters
    ----------
    path
        SQLite file path (ignored when pgcompat is in Postgres mode).
    conn_factory
        Optional callable that returns a connection.  Pass this in tests to
        inject a mock or in-memory SQLite connection:

            store = TrackerStore(conn_factory=lambda: fake_conn)

        When omitted the default pgcompat.connect() is used.
    """

    def __init__(
        self,
        path: str = DB_PATH,
        *,
        conn_factory: "Optional[Callable[[], Any]]" = None,
    ) -> None:
        self.path = path
        self._conn_factory = conn_factory
        self._init()

    def _conn(self):
        if self._conn_factory is not None:
            return self._conn_factory()
        return sqlite3.connect()

    def _init(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS wn_writers (
                    profile_id          TEXT PRIMARY KEY,
                    discord_id          INTEGER NOT NULL,
                    guild_id            INTEGER NOT NULL,
                    display_name        TEXT,
                    added_at            INTEGER NOT NULL,
                    last_discovered_at  INTEGER DEFAULT 0
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS wn_books (
                    book_id        TEXT NOT NULL,
                    profile_id     TEXT NOT NULL,
                    title          TEXT,
                    url            TEXT,
                    chapter_count  INTEGER DEFAULT 0,
                    views          TEXT,
                    genre          TEXT,
                    last_change_at INTEGER,
                    PRIMARY KEY (book_id, profile_id)
                )
            """)
            try:
                c.execute(
                    "ALTER TABLE wn_writers ADD COLUMN IF NOT EXISTS "
                    "last_discovered_at INTEGER DEFAULT 0"
                )
            except Exception:
                pass

    def mark_discovered(self, profile_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE wn_writers SET last_discovered_at=? WHERE profile_id=?",
                      (int(time.time()), profile_id))

    def add_writer(self, profile_id, discord_id, guild_id, display_name):
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO wn_writers"
                "(profile_id, discord_id, guild_id, display_name, added_at) "
                "VALUES (?,?,?,?,?)",
                (profile_id, discord_id, guild_id, display_name, int(time.time())),
            )

    def remove_writer_by_profile(self, profile_id):
        with self._conn() as c:
            cur = c.execute("DELETE FROM wn_writers WHERE profile_id=?", (profile_id,))
            c.execute("DELETE FROM wn_books WHERE profile_id=?", (profile_id,))
            return cur.rowcount

    def remove_writer_by_discord(self, discord_id):
        with self._conn() as c:
            for r in c.execute("SELECT profile_id FROM wn_writers WHERE discord_id=?",
                               (discord_id,)).fetchall():
                c.execute("DELETE FROM wn_books WHERE profile_id=?", (r["profile_id"],))
            cur = c.execute("DELETE FROM wn_writers WHERE discord_id=?", (discord_id,))
            return cur.rowcount

    def list_writers(self, guild_id=None):
        with self._conn() as c:
            if guild_id is None:
                return c.execute("SELECT * FROM wn_writers ORDER BY added_at").fetchall()
            return c.execute(
                "SELECT * FROM wn_writers WHERE guild_id=? ORDER BY added_at",
                (guild_id,),
            ).fetchall()

    def get_writer_by_discord(self, discord_id):
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM wn_writers WHERE discord_id=?", (discord_id,)
            ).fetchone()

    def list_books(self, profile_id):
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM wn_books WHERE profile_id=? ORDER BY title",
                (profile_id,),
            ).fetchall()

    def upsert_book(self, profile_id, book: Book) -> tuple[int, int, bool]:
        with self._conn() as c:
            row = c.execute(
                "SELECT chapter_count FROM wn_books WHERE book_id=? AND profile_id=?",
                (book.book_id, profile_id),
            ).fetchone()
            was_new = row is None
            prev = int(row["chapter_count"]) if row else 0
            now = int(time.time())
            c.execute(
                "INSERT INTO wn_books"
                "(book_id, profile_id, title, url, chapter_count, views, genre, last_change_at) "
                "VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(book_id, profile_id) DO UPDATE SET "
                "title=excluded.title, url=excluded.url, "
                "chapter_count=excluded.chapter_count, "
                "views=excluded.views, genre=excluded.genre, "
                "last_change_at=CASE WHEN wn_books.chapter_count<>excluded.chapter_count "
                "                    THEN excluded.last_change_at ELSE wn_books.last_change_at END",
                (book.book_id, profile_id, book.title, book.url, book.chapter_count,
                 book.views, book.genre, now),
            )
            return prev, book.chapter_count, was_new

    def total_chapters(self, profile_id: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(chapter_count), 0) FROM wn_books WHERE profile_id=?",
                (profile_id,),
            ).fetchone()
            return int(row[0]) if row else 0

    def total_chapters_for_discord(self, discord_id: int) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(b.chapter_count), 0) "
                "FROM wn_books b JOIN wn_writers w ON b.profile_id = w.profile_id "
                "WHERE w.discord_id = ?",
                (discord_id,),
            ).fetchone()
            return int(row[0]) if row else 0

    def books_for_discord(self, discord_id: int) -> list:
        with self._conn() as c:
            return c.execute(
                "SELECT b.book_id, b.title, b.url, b.chapter_count, b.views, b.genre "
                "FROM wn_books b JOIN wn_writers w ON b.profile_id = w.profile_id "
                "WHERE w.discord_id = ? ORDER BY b.title",
                (discord_id,),
            ).fetchall()
