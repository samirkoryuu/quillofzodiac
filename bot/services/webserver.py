"""
Tiny keep-alive web server shared by NishiBee and HiveGPT.

Both bots call `webserver.keep_alive()` at import time. This module starts
a single Flask server in a background thread the first time it's called,
then is a no-op on every subsequent call. That way the merged process
(see main.py) only ever exposes one HTTP port — which is what Render's
free web service needs to consider the deploy "healthy".

The launcher (main.py) calls `register_bot(name, bot)` for each Discord
bot it boots. The /status endpoint then reports whether each bot is
currently logged in, so a glance at the URL tells you both bees are alive.
"""

import logging
import os
import threading

from flask import Flask, jsonify

log = logging.getLogger("webserver")

_app: Flask | None = None
_started: bool = False
_lock = threading.Lock()
_bots: dict = {}


def register_bot(name: str, bot) -> None:
    _bots[name] = bot


def _bot_status_dict() -> dict:
    out = {}
    for name, bot in _bots.items():
        ready = False
        try:
            ready = bool(bot.is_ready())
        except Exception:
            ready = False
        closed = True
        try:
            closed = bool(bot.is_closed())
        except Exception:
            closed = True
        user_tag = None
        user_id = None
        guild_count = None
        latency_ms = None
        try:
            if bot.user is not None:
                user_tag = str(bot.user)
                user_id = bot.user.id
        except Exception:
            pass
        try:
            guild_count = len(bot.guilds)
        except Exception:
            pass
        try:
            lat = float(bot.latency)
            if lat == lat and lat != float("inf"):
                latency_ms = round(lat * 1000, 1)
        except Exception:
            pass
        out[name] = {
            "ready": ready,
            "closed": closed,
            "user": user_tag,
            "user_id": user_id,
            "guild_count": guild_count,
            "latency_ms": latency_ms,
        }
    return out


def _build_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def home():
        bots = _bot_status_dict()
        if not bots:
            return "🐝 The Hive bots are buzzing.", 200
        rows = []
        for name, info in bots.items():
            badge = "✅ online" if info["ready"] else (
                "💤 closed" if info["closed"] else "⏳ connecting"
            )
            extra = ""
            if info["user"]:
                extra = (
                    f" — logged in as <code>{info['user']}</code>, "
                    f"in {info['guild_count']} server(s), "
                    f"latency {info['latency_ms']} ms"
                )
            rows.append(f"<li><strong>{name}</strong>: {badge}{extra}</li>")
        html = (
            "<html><head><title>The Hive — bot status</title></head><body>"
            "<h1>🐝 The Hive</h1><ul>" + "".join(rows) + "</ul>"
            "<p><a href='/status'>JSON status</a> · "
            "<a href='/healthz'>health</a></p></body></html>"
        )
        return html, 200

    @app.route("/healthz")
    def health():
        return "ok", 200

    @app.route("/status")
    def status():
        bots = _bot_status_dict()
        all_ready = bool(bots) and all(info["ready"] for info in bots.values())
        return jsonify({"ok": all_ready, "bots": bots}), (200 if all_ready else 503)

    return app


def _run(app: Flask, port: int):
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def keep_alive():
    global _app, _started
    with _lock:
        if _started:
            return
        _started = True
        _app = _build_app()
        port = int(os.environ.get("PORT", "8080"))
        t = threading.Thread(target=_run, args=(_app, port), daemon=True)
        t.start()
        log.info("Keep-alive web server listening on 0.0.0.0:%d", port)