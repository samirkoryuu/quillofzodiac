"""
core/inkstone_ocr.py
=====================
Vision-OCR helper for Inkstone (Webnovel author dashboard) screenshots.

Pure logic module — no discord.py, no circular imports.

Call ocr_inkstone(image_url, keys, is_quota_error) where:
  keys            — list of key-dicts from HiveGPT's key pool
  is_quota_error  — callable(Exception) -> bool from hivegpt

The OCR returns a dict:
    {
        "books": [{"title": str, "chapters": int, "words": int}, ...],
        "totals": {"chapters": int, "words": int},
        "ranking": str | None,
        "raw_text": str,
    }
or None if every vision-capable key failed.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable, Protocol, runtime_checkable

log = logging.getLogger("inkstone_ocr")


# ---------------------------------------------------------------------------
# Testable key-pool protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class KeyPool(Protocol):
    """
    Interface a key-pool must satisfy to be passed to make_ocr_func().

    Real implementation: HiveGPT's runtime key list.
    Test implementation: any object with .get_keys() and .is_quota_error().

    Example mock::

        class MockKeyPool:
            def get_keys(self):
                return [{"provider": "openai", "client": mock_client}]
            def is_quota_error(self, exc):
                return "quota" in str(exc).lower()
    """
    def get_keys(self) -> list[dict]: ...
    def is_quota_error(self, exc: Exception) -> bool: ...


def make_ocr_func(pool: KeyPool) -> Callable[[str], "Coroutine[dict | None]"]:
    """
    Return an ``async (image_url: str) -> dict | None`` callable bound to pool.

    Use in production::

        ocr = make_ocr_func(hivegpt_key_pool)
        result = await ocr("https://cdn.discordapp.com/...")

    Use in tests::

        ocr = make_ocr_func(MockKeyPool())
        result = await ocr("http://example.com/fake.png")
    """
    async def _ocr(image_url: str) -> dict | None:
        return await ocr_inkstone(image_url, pool.get_keys(), pool.is_quota_error)
    return _ocr


def make_game_ocr_func(
    pool: KeyPool,
) -> Callable[["str, str"], "Coroutine[dict]"]:
    """
    Return an ``async (image_url, expected_game) -> dict`` callable bound to pool.

    Mirrors make_ocr_func() but for verify_game_screenshot().
    """
    async def _ocr(image_url: str, expected_game: str = "") -> dict:
        return await verify_game_screenshot(
            image_url, expected_game, pool.get_keys(), pool.is_quota_error
        )
    return _ocr

_DEFAULT_VISION_MODELS: dict[str, str | None] = {
    "openai":        os.environ.get("HIVEGPT_VISION_MODEL_OPENAI",     "gpt-4o-mini"),
    "gemini":        os.environ.get("HIVEGPT_VISION_MODEL_GEMINI",     "gemini-2.0-flash"),
    "openrouter":    os.environ.get("HIVEGPT_VISION_MODEL_OPENROUTER", "meta-llama/llama-3.2-11b-vision-instruct:free"),
    "groq":          os.environ.get("HIVEGPT_VISION_MODEL_GROQ",       "llama-3.2-11b-vision-preview"),
    "together":      os.environ.get("HIVEGPT_VISION_MODEL_TOGETHER",   "meta-llama/Llama-Vision-Free"),
    "mistral":       os.environ.get("HIVEGPT_VISION_MODEL_MISTRAL",    "pixtral-12b-latest"),
    "deepinfra":     os.environ.get("HIVEGPT_VISION_MODEL_DEEPINFRA",  "meta-llama/Llama-3.2-11B-Vision-Instruct"),
    "cerebras":      None,
    "replit-proxy":  os.environ.get("HIVEGPT_VISION_MODEL_OPENAI",     "gpt-4o-mini"),
}

_OCR_PROMPT = """\
You are looking at a screenshot from a Webnovel author area. It may be either:
  (A) the Inkstone "Works" dashboard — shows a list of manuscripts with
      per-book chapter and word counts, or
  (B) the Webnovel Analytics / author dashboard — shows stats for one book
      at a time: Collections, Views, Power Ranking, Chapters, Words,
      and percentage-change labels like "-100.0% since previous week".

Extract the book data and return STRICT JSON (no prose, no markdown fences).
Use this exact shape:

{
  "pen_name": "<string or null>",
  "books": [
    {"title": "<book title as shown>", "chapters": <int>, "words": <int>}
  ],
  "totals": {"chapters": <int>, "words": <int>},
  "ranking": "<any visible Webnovel ranking like 'Power Ranking No.0', 'Power Ranking #248', or 'Top 10 in Fantasy'> or null",
  "raw_text": "<your best-effort transcription of all visible numbers/labels>"
}

Rules:
- Words and chapters are ABSOLUTE counts only — ignore any percentage-change
  labels such as "-100.0% since previous day/week" or "+26.6%". Those are
  delta indicators, NOT the chapter or word count.
- Words and chapters are integers. Strip commas, "k"/"K" multipliers, and
  spaces. e.g. "12,540" -> 12540, "12.5k" -> 12500, "28.25k" -> 28250.
- For analytics dashboards (type B): the Chapters and Words values shown
  as large numbers near those labels are the absolute totals — use those.
- If you can see chapter and word counts per book, list each book
  individually under "books". If only a grand total is visible, leave
  "books" as [] and put the totals under "totals".
- If "totals" is not visible but per-book numbers are, sum them yourself
  and put the result in "totals".
- "ranking" is whatever Webnovel-side rank/badge/position is visible; null if none.
- "pen_name" is the writer name shown on the dashboard header if readable; otherwise null.
- Output ONLY the JSON object. No explanation, no code fences.
"""


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1)
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        return json.loads(text)
    except Exception as e:
        log.warning("inkstone_ocr: failed to parse JSON: %s; got %r", e, text[:200])
        return None


def _coerce_int(v) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if not isinstance(v, str):
        return 0
    s = v.strip().lower().replace(",", "").replace(" ", "")
    if not s:
        return 0
    mult = 1
    if s.endswith("k"):
        mult = 1_000
        s = s[:-1]
    elif s.endswith("m"):
        mult = 1_000_000
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except Exception:
        return 0


def _normalise(payload: dict) -> dict:
    books_in = payload.get("books") or []
    books: list[dict] = []
    for b in books_in:
        if not isinstance(b, dict):
            continue
        title = (b.get("title") or "").strip()
        ch = _coerce_int(b.get("chapters") or 0)
        wd = _coerce_int(b.get("words") or 0)
        if not title and not ch and not wd:
            continue
        books.append({"title": title or "(untitled)", "chapters": ch, "words": wd})

    totals_in = payload.get("totals") or {}
    t_ch = _coerce_int(totals_in.get("chapters") or 0)
    t_wd = _coerce_int(totals_in.get("words") or 0)
    if (not t_ch or not t_wd) and books:
        t_ch = t_ch or sum(b["chapters"] for b in books)
        t_wd = t_wd or sum(b["words"] for b in books)

    ranking = payload.get("ranking")
    if isinstance(ranking, str):
        ranking = ranking.strip() or None
    elif ranking is not None:
        ranking = str(ranking)

    pn = payload.get("pen_name")
    pn = pn.strip() if isinstance(pn, str) else None

    return {
        "pen_name": pn,
        "books": books,
        "totals": {"chapters": t_ch, "words": t_wd},
        "ranking": ranking,
        "raw_text": (payload.get("raw_text") or "")[:2000],
    }


async def ocr_inkstone(image_url: str, keys: list, is_quota_error) -> dict | None:
    """
    Run vision OCR on `image_url`.

    Parameters
    ----------
    image_url       : URL of the screenshot to analyse.
    keys            : HiveGPT's _keys list (dicts with 'provider', 'client').
    is_quota_error  : HiveGPT's _is_quota_error(exc) callable.

    Returns the normalised dict or None if all vision keys failed.
    """
    if not keys:
        log.warning("inkstone_ocr: no keys supplied — cannot OCR")
        return None

    last_err: Exception | None = None
    for entry in keys:
        provider = entry.get("provider", "")
        # Prefer the model already bound to the key in the pool
        vision_model = entry.get("model") or _DEFAULT_VISION_MODELS.get(provider)
        
        if not vision_model:
            continue
            
        client = entry.get("client")
        if client is None:
            continue

        try:
            resp = await client.chat.completions.create(
                model=vision_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _OCR_PROMPT},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }],
                max_tokens=900,
                temperature=0.0,
            )
            content = (resp.choices[0].message.content or "").strip()
            data = _extract_json(content)
            if not data:
                log.warning("inkstone_ocr: %s/%s returned non-JSON: %r", provider, vision_model, content[:200])
                continue

            res = _normalise(data)
            if not res.get("books") and res.get("totals", {}).get("words", 0) == 0:
                log.warning("inkstone_ocr: %s/%s returned valid JSON but no writer data found. Trying next...", provider, vision_model)
                continue
            return res
        except Exception as e:
            last_err = e
            if is_quota_error(e):
                log.warning("inkstone_ocr: %s key on quota/rate-limit, trying next", provider)
            else:
                log.warning("inkstone_ocr: %s/%s error (attempt next): %s", provider, vision_model, e)
            continue

    if last_err:
        log.error("inkstone_ocr: every vision key failed. Final error: %s", last_err)
    else:
        log.warning("inkstone_ocr: no vision-capable keys were even tried (check provider models).")
    return None


async def verify_game_screenshot(
    image_url: str,
    expected_game: str,
    keys: list,
    is_quota_error,
) -> dict:
    """
    Use vision OCR to confirm an image looks like a real game screenshot.

    Returns:
        {"is_game_screen": bool, "looks_like_game": str|None,
         "confidence": int, "raw_text": str}
    """
    if not keys:
        return {"is_game_screen": True, "looks_like_game": expected_game,
                "confidence": 0, "raw_text": ""}

    prompt = (
        "You are looking at an image a member submitted to a Discord "
        "writers' community to brag about a game achievement. Decide:\n"
        "1. Is this an actual screenshot from a video game (any game, "
        "PC or mobile, including Free Fire, PUBG, Valorant, Genshin, "
        "etc.)? Memes, photos, drawings, edited fakes, and writing "
        "dashboards do NOT count.\n"
        "2. If yes, which game does it look like?\n"
        "3. Confidence 0-100.\n\n"
        "Reply with STRICT JSON only:\n"
        '{"is_game_screen": <bool>, "looks_like_game": "<game name or null>", '
        '"confidence": <0-100>, "raw_text": "<1-line description>"}'
    )

    last_err = None
    for entry in keys:
        provider = entry.get("provider", "")
        vmodel = _DEFAULT_VISION_MODELS.get(provider)
        if not vmodel:
            continue
        client = entry.get("client")
        if client is None:
            continue
        try:
            resp = await client.chat.completions.create(
                model=vmodel,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }],
                max_tokens=200,
                temperature=0.0,
            )
            content = (resp.choices[0].message.content or "").strip()
            data = _extract_json(content)
            if not data:
                continue
            return {
                "is_game_screen": bool(data.get("is_game_screen")),
                "looks_like_game": data.get("looks_like_game") or None,
                "confidence": int(data.get("confidence") or 0),
                "raw_text": str(data.get("raw_text") or "")[:500],
            }
        except Exception as e:
            last_err = e
            if is_quota_error(e):
                continue
            log.warning("verify_game_screenshot via %s: %s", provider, e)
            continue

    log.warning("verify_game_screenshot: all keys exhausted. last=%s", last_err)
    return {"is_game_screen": True, "looks_like_game": expected_game,
            "confidence": 0, "raw_text": ""}
