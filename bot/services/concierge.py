"""
Founder Concierge — natural-language admin for HiveGPT.

The user (Founder) DMs HiveGPT in plain English, e.g.:

    add a 500-honey item called "Bee Plushie" to the shop
    announce in #general that submissions reopen Friday
    give @nina 200 honey for the bingo win
    ban @troll for repeated harassment

HiveGPT routes the message through the LLM intent parser, picks a registered
action, and replies with a Preview embed showing exactly what will happen.
The Founder taps **Confirm** (or **Cancel**), the action runs, and an audit
entry lands in #hive-logs with an **Undo** button valid for 60 seconds.

Destructive actions (ban, take_honey, etc.) require a *second* tap before the
"Confirm" button arms — so a fat-finger never bans someone.

Architecture notes
------------------
* Self-contained: only public surface is `setup(bot, *, llm_chat, conn, …)`,
  called once from `hivegpt.py` after the bot object exists.
* Actions are registered with `@action(...)` decorators inside this module.
  Adding a new admin verb = adding one decorated coroutine. No glue code.
* Lives inside the merged NishiBee + HiveGPT process, so it can poke
  `nishibee.SHOP_ITEMS` / `nishibee.BADGES` and the shared SQLite `conn`
  directly — no IPC needed.
* Founder check: must hold the Discord role "Founder" in at least one guild
  the bots are in. (Heavenly Bee can audit but not execute, see ROLE_AUDIT.)
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import discord
from discord.ui import Button, View

log = logging.getLogger("concierge")

# ─── module-level state injected by setup() ──────────────────────────────────
_bot: Optional[discord.ext.commands.Bot] = None  # type: ignore[name-defined]
_llm_chat: Optional[Callable[..., Awaitable[str]]] = None
_conn = None  # sqlite3.Connection
_db = None    # sqlite3.Cursor
_log_channel_name: str = "hive-logs"
_founder_role_name: str = "Founder"

# Roles that can EXECUTE concierge actions.
ROLE_EXECUTE = {"Founder"}
# Roles that can SEE concierge previews / audit log but not execute.
ROLE_AUDIT = {"Founder", "Heavenly Bee"}

# Confirm/Undo TTLs.
PREVIEW_TTL_SECONDS = 300       # 5 min to make up your mind
UNDO_TTL_SECONDS = 60           # 1 min to take it back


# ============================================================================
# ACTION REGISTRY
# ============================================================================

ParamSchema = dict[str, dict]
"""
Param schema looks like:
    {
      "channel": {"type": "string", "required": True,  "desc": "Channel name without #"},
      "amount":  {"type": "integer", "required": False, "default": 0,  "desc": "Honey to grant"},
    }
"required": True params with no default must be supplied or the parser asks
for clarification.
"""


@dataclass
class Action:
    name: str
    description: str
    params: ParamSchema
    executor: Callable[..., Awaitable[dict]]
    undo: Optional[Callable[[dict], Awaitable[None]]] = None
    destructive: bool = False
    examples: list[str] = field(default_factory=list)

    def schema_for_llm(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "destructive": self.destructive,
            "params": {
                k: {
                    "type": v.get("type", "string"),
                    "required": v.get("required", False),
                    "desc": v.get("desc", ""),
                }
                for k, v in self.params.items()
            },
            "examples": self.examples,
        }


_REGISTRY: dict[str, Action] = {}


def action(
    name: str,
    *,
    description: str,
    params: ParamSchema,
    destructive: bool = False,
    examples: Optional[list[str]] = None,
):
    """Register an admin action. Decorated function becomes the executor."""
    def deco(fn: Callable[..., Awaitable[dict]]):
        _REGISTRY[name] = Action(
            name=name,
            description=description,
            params=params,
            executor=fn,
            destructive=destructive,
            examples=examples or [],
        )
        return fn
    return deco


def undo_for(name: str):
    """Attach an undo coroutine to a registered action."""
    def deco(fn: Callable[[dict], Awaitable[None]]):
        if name not in _REGISTRY:
            raise RuntimeError(
                f"undo_for({name!r}): no such action — register the action first."
            )
        _REGISTRY[name].undo = fn
        return fn
    return deco


# ============================================================================
# DB SCHEMA
# ============================================================================

def _ensure_schema() -> None:
    _db.execute("""
        CREATE TABLE IF NOT EXISTS concierge_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            initiator_id   INTEGER NOT NULL,
            initiator_tag  TEXT,
            action         TEXT NOT NULL,
            params_json    TEXT NOT NULL,
            summary        TEXT,
            status         TEXT NOT NULL,           -- executed | failed | undone
            log_channel_id INTEGER,
            log_message_id INTEGER,
            undo_data_json TEXT,
            error          TEXT,
            executed_at    TEXT NOT NULL
        )
    """)
    _db.execute("""
        CREATE TABLE IF NOT EXISTS concierge_shop_extras (
            item_key   TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            price      INTEGER NOT NULL,
            kind       TEXT NOT NULL,
            value      TEXT,
            category   TEXT,
            descr      TEXT,
            added_at   TEXT NOT NULL,
            added_by   INTEGER NOT NULL
        )
    """)
    _conn.commit()


def _load_shop_extras_into_nishibee() -> int:
    """Re-attach previously-added shop items to nishibee.SHOP_ITEMS at boot."""
    try:
        import nishibee  # type: ignore
    except Exception:
        log.warning("nishibee not importable yet — shop extras not merged.")
        return 0
    rows = _db.execute(
        "SELECT item_key, name, price, kind, value, category, descr "
        "FROM concierge_shop_extras"
    ).fetchall()
    n = 0
    for key, name, price, kind, value, category, descr in rows:
        if key in nishibee.SHOP_ITEMS:
            continue
        nishibee.SHOP_ITEMS[key] = {
            "name": name,
            "price": int(price),
            "kind": kind,
            "value": value,
            "category": category,
            "desc": descr,
        }
        n += 1
    if n:
        log.info("Re-attached %d concierge shop extra(s) to nishibee.SHOP_ITEMS.", n)
    return n


# ============================================================================
# HELPERS — guild & member resolution
# ============================================================================

def _founder_guild(user: discord.User) -> Optional[discord.Guild]:
    """Return the first guild where `user` holds the Founder role."""
    for g in _bot.guilds:
        m = g.get_member(user.id)
        if not m:
            continue
        for r in m.roles:
            if r.name in ROLE_EXECUTE:
                return g
    return None


def _has_audit_role(user: discord.User) -> bool:
    for g in _bot.guilds:
        m = g.get_member(user.id)
        if m and any(r.name in ROLE_AUDIT for r in m.roles):
            return True
    return False


_MENTION_RE = re.compile(r"<@!?(\d+)>")


def _resolve_member(guild: discord.Guild, query: str) -> tuple[Optional[discord.Member], list[discord.Member]]:
    """Return (single_match, all_candidates). single_match=None if 0 or >1 hits."""
    if not query:
        return None, []
    q = query.strip()

    m = _MENTION_RE.search(q)
    if m:
        member = guild.get_member(int(m.group(1)))
        return (member, [member] if member else [])

    if q.isdigit():
        member = guild.get_member(int(q))
        return (member, [member] if member else [])

    q_clean = q.lstrip("@").lower()
    exact = [m for m in guild.members
             if m.name.lower() == q_clean or m.display_name.lower() == q_clean]
    if len(exact) == 1:
        return exact[0], exact
    if len(exact) > 1:
        return None, exact

    partial = [m for m in guild.members
               if q_clean in m.name.lower() or q_clean in m.display_name.lower()]
    if len(partial) == 1:
        return partial[0], partial
    return None, partial


def _resolve_channel(guild: discord.Guild, query: str) -> Optional[discord.TextChannel]:
    if not query:
        return None
    q = query.strip().lstrip("#").lower()
    m = re.match(r"<#(\d+)>", query.strip())
    if m:
        ch = guild.get_channel(int(m.group(1)))
        return ch if isinstance(ch, discord.TextChannel) else None
    for ch in guild.text_channels:
        if ch.name.lower() == q:
            return ch
    return None


def _audit_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    return discord.utils.get(guild.text_channels, name=_log_channel_name)


# ============================================================================
# LLM INTENT PARSER
# ============================================================================

INTENT_SYSTEM = (
    "You are the Founder Concierge intent parser for a Discord server called "
    "The Hive. The Founder will speak to you in plain English. Your ONLY job "
    "is to map their message to ONE registered admin action and extract the "
    "parameters as STRICT JSON.\n\n"
    "Rules:\n"
    "1. Reply with ONE JSON object. No prose, no code fences, no markdown.\n"
    "2. Schema: {\"action\": <name|null>, \"params\": {...}, "
    "\"summary\": <plain-english one-liner>, "
    "\"needs_clarification\": <question|null>}\n"
    "3. If the request is ambiguous, the action is unknown, or required "
    "params are missing, set action=null and put a SHORT clarifying question "
    "in needs_clarification. NEVER invent a username, channel, or amount.\n"
    "4. Channel/member references: keep the user's exact wording — no @, no #. "
    "Member resolution happens later.\n"
    "5. Numbers must be integers (no thousands separators, no quotes).\n"
    "6. Be conservative. When in doubt, ask.\n"
)


def _registry_doc() -> str:
    out = []
    for a in _REGISTRY.values():
        params_lines = []
        for pk, pv in a.params.items():
            req = " (required)" if pv.get("required") else ""
            default = (
                f" default={pv['default']!r}" if "default" in pv else ""
            )
            params_lines.append(
                f"    - {pk}: {pv.get('type', 'string')}{req}{default} "
                f"— {pv.get('desc', '')}"
            )
        ex = ""
        if a.examples:
            ex = "\n  Examples:\n" + "\n".join(f"    • {e}" for e in a.examples)
        flag = " [DESTRUCTIVE]" if a.destructive else ""
        out.append(
            f"- {a.name}{flag}: {a.description}\n"
            f"  Params:\n" + "\n".join(params_lines) + ex
        )
    return "\n".join(out)


async def _parse_intent(user_text: str) -> dict:
    if _llm_chat is None:
        return {"action": None, "params": {}, "summary": "",
                "needs_clarification": "AI is not configured — concierge unavailable."}

    sys_msg = INTENT_SYSTEM + "\n\nREGISTERED ACTIONS:\n" + _registry_doc()
    raw = await _llm_chat(
        [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_text},
        ],
        max_tokens=400,
        temperature=0.1,
    )
    if not raw:
        return {"action": None, "params": {}, "summary": "",
                "needs_clarification": "AI didn't respond — try again in a moment."}

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            return {"action": None, "params": {}, "summary": "",
                    "needs_clarification": "I couldn't make sense of that — try rephrasing?"}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {"action": None, "params": {}, "summary": "",
                    "needs_clarification": "I couldn't parse that — try rephrasing?"}

    return {
        "action": data.get("action"),
        "params": data.get("params") or {},
        "summary": (data.get("summary") or "").strip(),
        "needs_clarification": data.get("needs_clarification"),
    }


# ============================================================================
# PREVIEW + CONFIRM UI
# ============================================================================

class _PreviewView(View):
    def __init__(self, founder: discord.User, guild: discord.Guild,
                 act: Action, params: dict, summary: str):
        super().__init__(timeout=PREVIEW_TTL_SECONDS)
        self.founder = founder
        self.guild = guild
        self.act = act
        self.params = params
        self.summary = summary
        self.armed = not act.destructive   # destructive needs an extra tap
        self.message: Optional[discord.Message] = None
        self._refresh_buttons()

    def _refresh_buttons(self):
        self.clear_items()
        if self.act.destructive and not self.armed:
            self.add_item(_ArmDestructiveBtn(self))
        else:
            self.add_item(_ConfirmBtn(self))
        self.add_item(_CancelBtn(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.founder.id:
            await interaction.response.send_message(
                "🚫 Only the Founder who started this can confirm.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(
                    content="⌛ Concierge preview expired. Send the request again to retry.",
                    embed=None, view=None,
                )
            except Exception:
                pass


class _ArmDestructiveBtn(Button):
    def __init__(self, parent: _PreviewView):
        super().__init__(style=discord.ButtonStyle.danger,
                         label="⚠️ This is destructive — tap to arm")
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        self.parent.armed = True
        self.parent._refresh_buttons()
        await interaction.response.edit_message(
            content="🟠 Armed. Tap **Confirm** within 5 minutes to execute.",
            view=self.parent,
        )


class _ConfirmBtn(Button):
    def __init__(self, parent: _PreviewView):
        super().__init__(style=discord.ButtonStyle.success, label="✅ Confirm")
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        for child in self.parent.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self.parent)
        except Exception:
            pass
        await _execute_and_log(
            initiator=self.parent.founder,
            guild=self.parent.guild,
            act=self.parent.act,
            params=self.parent.params,
            summary=self.parent.summary,
            origin_message=interaction.message,
        )
        self.parent.stop()


class _CancelBtn(Button):
    def __init__(self, parent: _PreviewView):
        super().__init__(style=discord.ButtonStyle.secondary, label="✖ Cancel")
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        for child in self.parent.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="🚫 Cancelled. Nothing was changed.",
            embed=None, view=None,
        )
        self.parent.stop()


# ============================================================================
# UNDO UI
# ============================================================================

class _UndoView(View):
    def __init__(self, audit_id: int, act: Action, undo_data: dict,
                 founder_id: int):
        super().__init__(timeout=UNDO_TTL_SECONDS)
        self.audit_id = audit_id
        self.act = act
        self.undo_data = undo_data
        self.founder_id = founder_id
        self.add_item(_UndoBtn(self))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class _UndoBtn(Button):
    def __init__(self, parent: _UndoView):
        super().__init__(style=discord.ButtonStyle.danger,
                         label=f"↩️ Undo ({UNDO_TTL_SECONDS}s)")
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent.founder_id:
            await interaction.response.send_message(
                "🚫 Only the Founder who ran this can undo it.", ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            await self.parent.act.undo(self.parent.undo_data)  # type: ignore[arg-type]
        except Exception as e:
            log.exception("Undo failed for action %s", self.parent.act.name)
            await interaction.followup.send(
                f"❌ Undo failed: `{e}`", ephemeral=True
            )
            return
        _db.execute(
            "UPDATE concierge_audit SET status='undone' WHERE id=?",
            (self.parent.audit_id,),
        )
        _conn.commit()
        for child in self.parent.children:
            child.disabled = True
        try:
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.greyple()
            embed.title = "↩️ Concierge action — UNDONE"
            await interaction.message.edit(embed=embed, view=self.parent)
        except Exception:
            pass


# ============================================================================
# EXECUTE + AUDIT
# ============================================================================

def _format_params(params: dict) -> str:
    if not params:
        return "_(none)_"
    rows = []
    for k, v in params.items():
        if isinstance(v, str) and len(v) > 200:
            v = v[:197] + "…"
        rows.append(f"• **{k}**: `{v}`")
    return "\n".join(rows)


def _build_preview_embed(act: Action, params: dict, summary: str) -> discord.Embed:
    color = discord.Color.red() if act.destructive else discord.Color.gold()
    e = discord.Embed(
        title=f"🛡️ Preview · {act.name}",
        description=(summary or act.description),
        color=color,
    )
    e.add_field(name="Action", value=act.name, inline=True)
    e.add_field(
        name="Type",
        value="⚠️ destructive" if act.destructive else "🟢 safe",
        inline=True,
    )
    e.add_field(name="Parameters", value=_format_params(params), inline=False)
    e.set_footer(
        text="Confirm within 5 minutes."
        + ("  Two taps required." if act.destructive else "")
    )
    return e


async def _execute_and_log(
    *,
    initiator: discord.User,
    guild: discord.Guild,
    act: Action,
    params: dict,
    summary: str,
    origin_message: Optional[discord.Message],
) -> None:
    ctx = {
        "bot": _bot,
        "guild": guild,
        "initiator": initiator,
        "params": params,
        "resolve_member": lambda q: _resolve_member(guild, q),
        "resolve_channel": lambda q: _resolve_channel(guild, q),
    }
    status = "executed"
    error: Optional[str] = None
    undo_data: dict = {}
    result_msg = ""
    try:
        result = await act.executor(ctx)
        if isinstance(result, dict):
            undo_data = result.get("undo_data") or {}
            result_msg = result.get("message") or ""
        else:
            result_msg = str(result) if result else ""
    except Exception as e:
        status = "failed"
        error = str(e)
        log.exception("Concierge action %s failed", act.name)

    cur = _db.execute(
        "INSERT INTO concierge_audit "
        "(initiator_id, initiator_tag, action, params_json, summary, status, "
        " undo_data_json, error, executed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            initiator.id,
            str(initiator),
            act.name,
            json.dumps(params, default=str),
            summary or act.description,
            status,
            json.dumps(undo_data, default=str) if undo_data else None,
            error,
            _dt.datetime.utcnow().isoformat(),
        ),
    )
    audit_id = cur.lastrowid
    _conn.commit()

    audit_ch = _audit_channel(guild)
    audit_msg: Optional[discord.Message] = None
    if audit_ch:
        embed = discord.Embed(
            title=("✅ Concierge action — done" if status == "executed"
                   else "❌ Concierge action — FAILED"),
            description=summary or act.description,
            color=(discord.Color.green() if status == "executed"
                   else discord.Color.red()),
            timestamp=_dt.datetime.utcnow(),
        )
        embed.add_field(name="Action", value=act.name, inline=True)
        embed.add_field(name="By", value=initiator.mention, inline=True)
        embed.add_field(name="ID", value=f"#{audit_id}", inline=True)
        embed.add_field(name="Parameters", value=_format_params(params), inline=False)
        if result_msg:
            embed.add_field(name="Result", value=result_msg[:1000], inline=False)
        if error:
            embed.add_field(name="Error", value=f"```{error[:900]}```", inline=False)

        view = None
        if status == "executed" and act.undo and undo_data:
            view = _UndoView(audit_id, act, undo_data, initiator.id)
        try:
            audit_msg = await audit_ch.send(embed=embed, view=view) if view \
                else await audit_ch.send(embed=embed)
            _db.execute(
                "UPDATE concierge_audit SET log_channel_id=?, log_message_id=? "
                "WHERE id=?",
                (audit_ch.id, audit_msg.id, audit_id),
            )
            _conn.commit()
        except Exception:
            log.exception("Failed to post audit log")

    if origin_message:
        try:
            if status == "executed":
                tail = (
                    f"\n\n🔁 You have {UNDO_TTL_SECONDS}s to tap **Undo** "
                    f"in #{_log_channel_name}." if act.undo and undo_data else ""
                )
                msg = (
                    f"✅ Done — {result_msg or summary or act.description}"
                    f"  *(audit #{audit_id})*{tail}"
                )
            else:
                msg = (
                    f"❌ Action failed: `{error}`  *(audit #{audit_id})*"
                )
            await origin_message.edit(content=msg, embed=None, view=None)
        except Exception:
            pass


# ============================================================================
# ENTRY POINT — called by hivegpt's on_message DM handler
# ============================================================================

async def handle_dm(message: discord.Message) -> bool:
    """
    Try to handle `message` as a Founder Concierge request.
    Returns True if handled (so the caller skips its normal generate_reply path),
    False otherwise.
    """
    if not isinstance(message.channel, discord.DMChannel):
        return False

    guild = _founder_guild(message.author)
    if guild is None:
        return False  # not a Founder — fall through to normal HiveGPT chat

    text = message.content.strip()
    if not text:
        return False

    # Don't hijack questions / chit-chat. Heuristic: must look imperative.
    # We let the LLM decide — if it returns action=null we fall through to
    # normal HiveGPT chat so the founder can also just chat with the bee.
    async with message.channel.typing():
        intent = await _parse_intent(text)

    name = intent.get("action")
    if not name:
        return False  # let HiveGPT's normal handler reply conversationally

    act = _REGISTRY.get(name)
    if not act:
        await message.channel.send(
            f"🤔 I tried to map that to **{name}** but I don't have that action. "
            f"Available: {', '.join(_REGISTRY) or '(none)'}"
        )
        return True

    if intent.get("needs_clarification"):
        await message.channel.send(f"❓ {intent['needs_clarification']}")
        return True

    params = intent.get("params") or {}
    missing = [k for k, v in act.params.items()
               if v.get("required") and k not in params]
    if missing:
        await message.channel.send(
            f"❓ I need a bit more — please specify: **{', '.join(missing)}**"
        )
        return True

    # Apply defaults.
    for k, v in act.params.items():
        if k not in params and "default" in v:
            params[k] = v["default"]

    embed = _build_preview_embed(act, params, intent.get("summary", ""))
    view = _PreviewView(message.author, guild, act, params, intent.get("summary", ""))
    sent = await message.channel.send(
        content="🛡️ **Concierge preview** — review carefully before confirming.",
        embed=embed,
        view=view,
    )
    view.message = sent
    return True


# ============================================================================
# BUILT-IN ACTIONS
# ============================================================================
# Each executor takes a `ctx` dict and returns:
#   {"message": "human-readable result", "undo_data": {...optional...}}
# Raise on failure — `_execute_and_log` records the exception in the audit.

@action(
    "announce",
    description="Post a public announcement embed in a server channel.",
    params={
        "channel": {"type": "string", "required": True,
                    "desc": "Channel name (no #), e.g. 'general' or 'announcements'"},
        "title":   {"type": "string", "required": False, "default": "📣 Hive announcement",
                    "desc": "Embed title"},
        "body":    {"type": "string", "required": True, "desc": "Announcement body"},
    },
    examples=[
        "announce in #general that submissions reopen Friday",
        "post an announcement in announcements: weekly contest starts now",
    ],
)
async def _do_announce(ctx):
    ch = ctx["resolve_channel"](ctx["params"]["channel"])
    if not ch:
        raise RuntimeError(f"channel not found: {ctx['params']['channel']!r}")
    embed = discord.Embed(
        title=ctx["params"].get("title") or "📣 Hive announcement",
        description=ctx["params"]["body"],
        color=discord.Color.gold(),
        timestamp=_dt.datetime.utcnow(),
    )
    embed.set_footer(text=f"Posted by {ctx['initiator']}")
    msg = await ch.send(embed=embed)
    return {
        "message": f"Posted in #{ch.name} → [jump]({msg.jump_url})",
        "undo_data": {"channel_id": ch.id, "message_id": msg.id},
    }


@undo_for("announce")
async def _undo_announce(data):
    ch = _bot.get_channel(int(data["channel_id"]))
    if not ch:
        return
    msg = await ch.fetch_message(int(data["message_id"]))
    await msg.delete()


@action(
    "say",
    description="Make the bot send a plain message in a channel (no embed).",
    params={
        "channel": {"type": "string", "required": True, "desc": "Channel name"},
        "text":    {"type": "string", "required": True, "desc": "Message to post"},
    },
    examples=["say in #lounge: be kind today bees"],
)
async def _do_say(ctx):
    ch = ctx["resolve_channel"](ctx["params"]["channel"])
    if not ch:
        raise RuntimeError(f"channel not found: {ctx['params']['channel']!r}")
    msg = await ch.send(ctx["params"]["text"][:2000])
    return {
        "message": f"Sent in #{ch.name} → [jump]({msg.jump_url})",
        "undo_data": {"channel_id": ch.id, "message_id": msg.id},
    }


@undo_for("say")
async def _undo_say(data):
    ch = _bot.get_channel(int(data["channel_id"]))
    if not ch:
        return
    msg = await ch.fetch_message(int(data["message_id"]))
    await msg.delete()


@action(
    "give_honey",
    description="Add honey to a member's balance.",
    params={
        "member": {"type": "string",  "required": True, "desc": "Member name, mention, or ID"},
        "amount": {"type": "integer", "required": True, "desc": "Honey to add (positive)"},
        "reason": {"type": "string",  "required": False, "default": "Founder grant",
                   "desc": "Note for the audit log"},
    },
    examples=["give @nina 200 honey for the bingo win"],
)
async def _do_give_honey(ctx):
    import nishibee  # type: ignore
    amount = int(ctx["params"]["amount"])
    if amount <= 0:
        raise RuntimeError("amount must be a positive integer (use take_honey to deduct)")
    member, candidates = ctx["resolve_member"](ctx["params"]["member"])
    if not member:
        if candidates:
            names = ", ".join(m.display_name for m in candidates[:5])
            raise RuntimeError(f"ambiguous member — matched: {names}")
        raise RuntimeError(f"member not found: {ctx['params']['member']!r}")
    nishibee._add_honey(member.id, ctx["guild"].id, amount)
    return {
        "message": f"+{amount} 🍯 to **{member.display_name}** ({ctx['params'].get('reason')})",
        "undo_data": {"member_id": member.id, "guild_id": ctx["guild"].id, "amount": amount},
    }


@undo_for("give_honey")
async def _undo_give_honey(data):
    import nishibee  # type: ignore
    nishibee._add_honey(int(data["member_id"]), int(data["guild_id"]), -int(data["amount"]))


@action(
    "take_honey",
    description="Remove honey from a member's balance. DESTRUCTIVE.",
    params={
        "member": {"type": "string",  "required": True, "desc": "Member name, mention, or ID"},
        "amount": {"type": "integer", "required": True, "desc": "Honey to remove (positive)"},
        "reason": {"type": "string",  "required": False, "default": "Founder deduction",
                   "desc": "Note for the audit log"},
    },
    destructive=True,
    examples=["take 100 honey from @bob"],
)
async def _do_take_honey(ctx):
    import nishibee  # type: ignore
    amount = int(ctx["params"]["amount"])
    if amount <= 0:
        raise RuntimeError("amount must be a positive integer")
    member, candidates = ctx["resolve_member"](ctx["params"]["member"])
    if not member:
        if candidates:
            names = ", ".join(m.display_name for m in candidates[:5])
            raise RuntimeError(f"ambiguous member — matched: {names}")
        raise RuntimeError(f"member not found: {ctx['params']['member']!r}")
    nishibee._add_honey(member.id, ctx["guild"].id, -amount)
    return {
        "message": f"−{amount} 🍯 from **{member.display_name}** ({ctx['params'].get('reason')})",
        "undo_data": {"member_id": member.id, "guild_id": ctx["guild"].id, "amount": amount},
    }


@undo_for("take_honey")
async def _undo_take_honey(data):
    import nishibee  # type: ignore
    nishibee._add_honey(int(data["member_id"]), int(data["guild_id"]), int(data["amount"]))


@action(
    "set_honey",
    description="Set a member's honey balance to an exact amount. DESTRUCTIVE.",
    params={
        "member": {"type": "string",  "required": True, "desc": "Member name, mention, or ID"},
        "amount": {"type": "integer", "required": True, "desc": "Target balance"},
    },
    destructive=True,
    examples=["set @bob to 500 honey"],
)
async def _do_set_honey(ctx):
    import nishibee  # type: ignore
    amount = int(ctx["params"]["amount"])
    if amount < 0:
        raise RuntimeError("amount must be ≥ 0")
    member, candidates = ctx["resolve_member"](ctx["params"]["member"])
    if not member:
        if candidates:
            raise RuntimeError(
                "ambiguous member — matched: "
                + ", ".join(m.display_name for m in candidates[:5])
            )
        raise RuntimeError(f"member not found: {ctx['params']['member']!r}")
    prev_honey, *_ = nishibee._get_economy(member.id, ctx["guild"].id)
    nishibee.c.execute(
        "UPDATE economy SET honey=? WHERE discord_id=? AND guild_id=?",
        (amount, member.id, ctx["guild"].id),
    )
    nishibee.conn.commit()
    return {
        "message": f"**{member.display_name}** balance: {prev_honey} → {amount} 🍯",
        "undo_data": {"member_id": member.id, "guild_id": ctx["guild"].id,
                      "previous": prev_honey},
    }


@undo_for("set_honey")
async def _undo_set_honey(data):
    import nishibee  # type: ignore
    nishibee.c.execute(
        "UPDATE economy SET honey=? WHERE discord_id=? AND guild_id=?",
        (int(data["previous"]), int(data["member_id"]), int(data["guild_id"])),
    )
    nishibee.conn.commit()


@action(
    "award_badge",
    description="Award a badge to a member.",
    params={
        "member":   {"type": "string", "required": True, "desc": "Member name, mention, or ID"},
        "badge_id": {"type": "string", "required": True,
                     "desc": "Badge key (use list_badges to see options)"},
    },
    examples=["give @nina the bingo_winner badge"],
)
async def _do_award_badge(ctx):
    import nishibee  # type: ignore
    badge_id = ctx["params"]["badge_id"]
    if badge_id not in nishibee.BADGES:
        raise RuntimeError(
            f"unknown badge_id {badge_id!r}. "
            f"Known: {', '.join(list(nishibee.BADGES)[:15])}…"
        )
    member, candidates = ctx["resolve_member"](ctx["params"]["member"])
    if not member:
        if candidates:
            raise RuntimeError(
                "ambiguous member — matched: "
                + ", ".join(m.display_name for m in candidates[:5])
            )
        raise RuntimeError(f"member not found: {ctx['params']['member']!r}")
    newly = nishibee._award_badge(member.id, ctx["guild"].id, badge_id)
    if not newly:
        return {"message": f"**{member.display_name}** already has that badge — nothing changed."}
    b = nishibee.BADGES[badge_id]
    return {
        "message": f"{b['emoji']} **{member.display_name}** earned **{b['name']}**",
        "undo_data": {"member_id": member.id, "guild_id": ctx["guild"].id,
                      "badge_id": badge_id},
    }


@undo_for("award_badge")
async def _undo_award_badge(data):
    import nishibee  # type: ignore
    nishibee.c.execute(
        "DELETE FROM member_badges "
        "WHERE discord_id=? AND guild_id=? AND badge_id=?",
        (int(data["member_id"]), int(data["guild_id"]), data["badge_id"]),
    )
    nishibee.conn.commit()


@action(
    "add_shop_item",
    description="Add a new item to the shop. Persists across restarts.",
    params={
        "key":      {"type": "string",  "required": True,
                     "desc": "Short unique slug, e.g. 'title-bee-lord'"},
        "name":     {"type": "string",  "required": True, "desc": "Display name"},
        "price":    {"type": "integer", "required": True, "desc": "Cost in honey"},
        "kind":     {"type": "string",  "required": False, "default": "title",
                     "desc": "title | spotlight | role | custom"},
        "value":    {"type": "string",  "required": False, "default": None,
                     "desc": "For 'title' kind: the displayed title text"},
        "category": {"type": "string",  "required": False, "default": "show-off",
                     "desc": "Shop category"},
        "descr":    {"type": "string",  "required": False, "default": "",
                     "desc": "1-line description shown in shop"},
    },
    examples=[
        "add a 500-honey title called Bee Plushie to the shop",
        "make a new shop item: key=title-honeybun, name='🍯 Honeybun', price=400",
    ],
)
async def _do_add_shop_item(ctx):
    import nishibee  # type: ignore
    p = ctx["params"]
    key = p["key"].strip()
    if key in nishibee.SHOP_ITEMS:
        raise RuntimeError(f"shop item key {key!r} already exists")
    item = {
        "name":     p["name"],
        "price":    int(p["price"]),
        "kind":     p.get("kind") or "title",
        "value":    p.get("value") if p.get("value") is not None else p["name"],
        "category": p.get("category") or "show-off",
        "desc":     p.get("descr") or "",
    }
    nishibee.SHOP_ITEMS[key] = item
    _db.execute(
        "INSERT INTO concierge_shop_extras "
        "(item_key, name, price, kind, value, category, descr, added_at, added_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, item["name"], item["price"], item["kind"], item["value"],
         item["category"], item["desc"],
         _dt.datetime.utcnow().isoformat(), ctx["initiator"].id),
    )
    _conn.commit()
    return {
        "message": f"🛒 Added **{item['name']}** ({item['price']} 🍯) — key `{key}`",
        "undo_data": {"key": key},
    }


@undo_for("add_shop_item")
async def _undo_add_shop_item(data):
    import nishibee  # type: ignore
    key = data["key"]
    nishibee.SHOP_ITEMS.pop(key, None)
    _db.execute("DELETE FROM concierge_shop_extras WHERE item_key=?", (key,))
    _conn.commit()


@action(
    "dm_member",
    description="Send a direct message to a member from the bot.",
    params={
        "member": {"type": "string", "required": True, "desc": "Member name, mention, or ID"},
        "text":   {"type": "string", "required": True, "desc": "DM body (≤ 2000 chars)"},
    },
    examples=["DM @nina: thanks for moderating last night"],
)
async def _do_dm_member(ctx):
    member, candidates = ctx["resolve_member"](ctx["params"]["member"])
    if not member:
        if candidates:
            raise RuntimeError(
                "ambiguous member — matched: "
                + ", ".join(m.display_name for m in candidates[:5])
            )
        raise RuntimeError(f"member not found: {ctx['params']['member']!r}")
    try:
        await member.send(ctx["params"]["text"][:2000])
    except discord.Forbidden:
        raise RuntimeError("member has DMs closed — couldn't deliver")
    return {"message": f"📬 DM sent to **{member.display_name}**"}


@action(
    "set_nickname",
    description="Change a member's server nickname.",
    params={
        "member":   {"type": "string", "required": True, "desc": "Member name, mention, or ID"},
        "nickname": {"type": "string", "required": True, "desc": "New nickname (≤ 32 chars)"},
    },
    examples=["nickname @bob to Master Bee"],
)
async def _do_set_nickname(ctx):
    member, candidates = ctx["resolve_member"](ctx["params"]["member"])
    if not member:
        if candidates:
            raise RuntimeError(
                "ambiguous member — matched: "
                + ", ".join(m.display_name for m in candidates[:5])
            )
        raise RuntimeError(f"member not found: {ctx['params']['member']!r}")
    previous = member.display_name
    new_nick = ctx["params"]["nickname"][:32]
    try:
        await member.edit(nick=new_nick)
    except discord.Forbidden:
        raise RuntimeError("missing permission to change that member's nickname")
    return {
        "message": f"**{previous}** → **{new_nick}**",
        "undo_data": {"member_id": member.id, "previous": previous},
    }


@undo_for("set_nickname")
async def _undo_set_nickname(data):
    for g in _bot.guilds:
        m = g.get_member(int(data["member_id"]))
        if m:
            try:
                await m.edit(nick=data["previous"][:32])
            except Exception:
                pass


@action(
    "kick_member",
    description="Kick a member from the server. DESTRUCTIVE.",
    params={
        "member": {"type": "string", "required": True, "desc": "Member name, mention, or ID"},
        "reason": {"type": "string", "required": False, "default": "Concierge action",
                   "desc": "Audit log reason"},
    },
    destructive=True,
    examples=["kick @spammer for spam"],
)
async def _do_kick(ctx):
    member, candidates = ctx["resolve_member"](ctx["params"]["member"])
    if not member:
        if candidates:
            raise RuntimeError("ambiguous member — be more specific")
        raise RuntimeError(f"member not found: {ctx['params']['member']!r}")
    reason = ctx["params"].get("reason") or "Concierge action"
    try:
        await member.kick(reason=f"[Founder Concierge] {reason}")
    except discord.Forbidden:
        raise RuntimeError("missing permission to kick that member")
    return {"message": f"👢 Kicked **{member.display_name}** — {reason}"}


@action(
    "ban_member",
    description="Ban a member from the server. DESTRUCTIVE.",
    params={
        "member":      {"type": "string",  "required": True,
                        "desc": "Member name, mention, or ID"},
        "reason":      {"type": "string",  "required": False, "default": "Concierge action",
                        "desc": "Audit log reason"},
        "delete_days": {"type": "integer", "required": False, "default": 0,
                        "desc": "Days of recent messages to also delete (0–7)"},
    },
    destructive=True,
    examples=["ban @troll for repeated harassment, delete 1 day of messages"],
)
async def _do_ban(ctx):
    member, candidates = ctx["resolve_member"](ctx["params"]["member"])
    if not member:
        if candidates:
            raise RuntimeError("ambiguous member — be more specific")
        raise RuntimeError(f"member not found: {ctx['params']['member']!r}")
    reason = ctx["params"].get("reason") or "Concierge action"
    delete_days = max(0, min(7, int(ctx["params"].get("delete_days") or 0)))
    try:
        await ctx["guild"].ban(
            member,
            reason=f"[Founder Concierge] {reason}",
            delete_message_days=delete_days,
        )
    except discord.Forbidden:
        raise RuntimeError("missing permission to ban that member")
    return {
        "message": f"🔨 Banned **{member.display_name}** — {reason}",
        "undo_data": {"member_id": member.id, "guild_id": ctx["guild"].id},
    }


@undo_for("ban_member")
async def _undo_ban(data):
    g = _bot.get_guild(int(data["guild_id"]))
    if not g:
        return
    user = await _bot.fetch_user(int(data["member_id"]))
    await g.unban(user, reason="[Founder Concierge] Undo")


# ============================================================================
# SETUP
# ============================================================================

def setup(bot, *, llm_chat, conn, log_channel_name: str = "hive-logs"):
    """
    Wire the concierge into a running HiveGPT bot.

    Call this ONCE from hivegpt.py after `bot` and `conn` exist:

        import concierge
        concierge.setup(bot, llm_chat=llm_chat, conn=conn)

    The hivegpt DM handler must check `await concierge.handle_dm(message)`
    BEFORE its normal generate_reply path:

        if await concierge.handle_dm(message):
            return
    """
    global _bot, _llm_chat, _conn, _db, _log_channel_name
    _bot = bot
    _llm_chat = llm_chat
    _conn = conn
    _db = conn.cursor()
    _log_channel_name = log_channel_name
    _ensure_schema()
    log.info(
        "Founder Concierge ready — %d action(s) registered: %s",
        len(_REGISTRY),
        ", ".join(sorted(_REGISTRY)),
    )

    # Re-attach previously-added shop items once NishiBee is loaded.
    async def _later_merge():
        try:
            await asyncio.sleep(2)
            _load_shop_extras_into_nishibee()
        except Exception:
            log.exception("Shop extras merge failed")
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_later_merge())
    except RuntimeError:
        # No running loop yet — bot.start() will create one and we can't
        # schedule against it from here. The merge will happen on the first
        # `add_shop_item` invocation instead. Skip for now.
        pass