"""
HiveSlave Peak — Unified Launcher for NishiBee + HiveGPT.

Runs both Discord bots inside ONE Python process using asyncio.gather.
They maintain separate connections and profiles on Discord, but share memory,
database, and services.
"""
import asyncio
import logging
import os
import signal

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import discord
from discord.ext import commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("hive-launcher-peak")

_RESTART_BASE_DELAY = 5
_RESTART_MAX_DELAY = int(os.environ.get("BOT_RESTART_MAX_DELAY", "300"))
_MAX_RETRIES = int(os.environ.get("BOT_MAX_RETRIES", "0"))

def _resolve_token(name: str, *env_vars: str) -> str:
    for var in env_vars:
        val = os.environ.get(var, "").strip()
        if val and "your_" not in val.lower() and "_here" not in val.lower():
            return val
    log.error("%s token not found. Checked: %s", name, env_vars)
    return ""

def _init_shared_db() -> None:
    try:
        from core import pgcompat
        if pgcompat._USE_POSTGRES:
            pgcompat._get_pool()
            log.info("Shared PostgreSQL connection pool initialised.")
        else:
            log.info("pgcompat: no DATABASE_URL — using local SQLite.")
    except Exception as exc:
        log.warning("Could not pre-init pgcompat pool: %s", exc)

def _init_shared_services() -> dict:
    import importlib
    services: dict = {}
    for name, module_path in [
        ("inkstone_ocr",     "core.inkstone_ocr"),
        ("webnovel_tracker", "core.webnovel_tracker"),
        ("gamenews",         "core.gamenews"),
        ("auto_rank",        "core.auto_rank"),
    ]:
        try:
            services[name] = importlib.import_module(module_path)
            log.info("Loaded shared service: %s", name)
        except Exception as exc:
            log.warning("Could not load shared service %s: %s", name, exc)
    return services

def _init_persona_middleware() -> None:
    try:
        from core import persona as _p
        log.info(
            "hive-launcher: Persona middleware ready "
            "— store=%r, %d pre-loaded assignment(s).",
            _p.store, len(_p.store.snapshot()),
        )
    except Exception as exc:
        log.warning("Could not initialise persona middleware: %s", exc)

def _wire_persona_check(name: str, persona: str, bot) -> None:
    try:
        from core.persona import store as _store
        
        async def _persona_check(interaction) -> bool:
            await _store.set(persona, interaction.user.id)
            return True
            
        bot.tree.interaction_check = _persona_check
        log.info("hive-launcher: Persona check wired on %s → '%s'.", name, persona)
    except Exception as exc:
        log.warning("Could not wire persona check for %s: %s", name, exc)

# ---------------------------------------------------------------------------
# Bot Instances Setup
# ---------------------------------------------------------------------------

class HiveBot(commands.Bot):
    def __init__(self, name: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot_name = name
        self.cog_list = []

    async def setup_hook(self):
        # Pre-import core dependencies for cogs
        from core import pgcompat
        conn = pgcompat.connect()
        c = conn.cursor()
        from cogs.economy import RANK_TIERS, tier_for, award_badge, announce_badge
        
        for cog_name in self.cog_list:
            try:
                if cog_name == "cogs.auto_rank":
                    from cogs.auto_rank import setup_cog
                    import hivegpt
                    from core.inkstone_ocr import make_ocr_func
                    
                    # Wait for HiveGPT keys if needed, or just use the make_ocr_func
                    # HiveGPT keys are loaded at import time in hivegpt.py
                    ocr_func = make_ocr_func(hivegpt) 
                    
                    await setup_cog(
                        self,
                        conn=conn, c=c,
                        rank_tiers=RANK_TIERS,
                        tier_for=tier_for,
                        award_badge=award_badge,
                        announce_badge=announce_badge,
                        ocr_func=ocr_func
                    )
                    log.info(f"{self.bot_name} initialized {cog_name} with dependencies.")
                else:
                    await self.load_extension(cog_name)
                    log.info(f"{self.bot_name} loaded cog: {cog_name}")
            except Exception as e:
                log.error(f"Failed to load cog {cog_name} for {self.bot_name}: {e}")
        
        # Sync slash commands
        log.info(f"{self.bot_name} syncing slash commands...")
        await self.tree.sync()
        log.info(f"{self.bot_name} slash commands synced.")

    async def on_ready(self):
        log.info(f"{self.bot_name} logged in as {self.user}")

async def _run_bot(name: str, token: str, bot: commands.Bot) -> None:
    logger = logging.getLogger(name)
    delay = _RESTART_BASE_DELAY
    attempts = 0

    while True:
        try:
            logger.info("Starting (attempt %d)...", attempts + 1)
            await bot.start(token)
            logger.info("Exited cleanly.")
            break
        except asyncio.CancelledError:
            logger.info("Cancelled — shutting down.")
            raise
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt — shutting down.")
            raise
        except Exception as exc:
            attempts += 1
            import discord
            if isinstance(exc, discord.LoginFailure):
                logger.critical("Invalid token — will NOT retry.")
                break
            if _MAX_RETRIES and attempts >= _MAX_RETRIES:
                logger.critical("Crashed %d time(s) — giving up.", attempts)
                break
            logger.error("Crashed: %s — restarting in %.0fs...", exc, delay, exc_info=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RESTART_MAX_DELAY)
            try:
                await bot.close()
            except Exception:
                pass

async def main() -> None:
    _init_shared_db()
    shared = _init_shared_services()
    _init_persona_middleware()

    try:
        from services import webserver
        webserver.keep_alive()
    except Exception as e:
        log.warning(f"Could not start webserver: {e}")

    nishibee_token = _resolve_token("NishiBee", "DISCORD_BOT_TOKEN")
    hivegpt_token  = _resolve_token("HiveGPT", "HIVEGPT_TOKEN", "HIVEGPT_DISCORD_TOKEN", "DISCORD_TOKEN")
    meghdoot_token = _resolve_token("Meghdoot", "MEGHDOOT_TOKEN")
    premael_token  = _resolve_token("Premael", "PREMAEL_TOKEN")

    supervisors = []
    
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    # -----------------------------------------------------------------------
    # Group bots by token to prevent collisions
    # -----------------------------------------------------------------------
    token_to_cogs = {}
    token_to_names = {}
    
    def _add_to_token(token, name, cogs):
        if not token: return
        if token not in token_to_cogs:
            token_to_cogs[token] = []
            token_to_names[token] = []
        token_to_cogs[token].extend(cogs)
        token_to_names[token].append(name)

    _add_to_token(nishibee_token, "NishiBee", [
        "cogs.economy", "cogs.cleanliness", "cogs.gamenews", "cogs.slacker"
    ])
    _add_to_token(meghdoot_token, "Meghdoot", [
        "cogs.verification", "cogs.webnovel_tracker", "cogs.writing_tools", "cogs.tickets",
        "cogs.admin", "cogs.requestbee", "cogs.fun", "cogs.gaming",
        "cogs.duels", "cogs.challenges", "cogs.quests", "cogs.bingo", "cogs.founder"
    ])
    _add_to_token(premael_token, "Premael", [
        "cogs.events", "cogs.social", "cogs.beta_board", "cogs.scheduler", "cogs.birthdays"
    ])
    
    # Special handling for HiveGPT (it's a module, but we can treat it as a cog set)
    if hivegpt_token:
        # HiveGPT logic is primarily in its own main.py/bot, but we've integrated it here.
        # If it shares a token with others, we should ideally load its cogs into the shared bot.
        # For now, we'll keep HiveGPT's custom launcher logic if it's unique.
        pass

    supervisors = []
    auto_rank_assigned = False
    
    # Launch grouped bots
    for token, names in token_to_names.items():
        primary_name = "_".join(names)
        cogs = list(set(token_to_cogs[token]))
        
        # Ensure auto_rank is only assigned ONCE to the most appropriate bot (Meghdoot)
        if not auto_rank_assigned:
            if "Meghdoot" in names or "NishiBee" in names:
                if "cogs.auto_rank" not in cogs:
                    cogs.append("cogs.auto_rank")
                auto_rank_assigned = True
        
        bot_instance = HiveBot(primary_name, command_prefix="!", intents=intents, help_command=None)
        bot_instance.cog_list = cogs
        
        # Wire persona (use first name in list as primary persona)
        _wire_persona_check(primary_name, names[0].lower(), bot_instance)
        supervisors.append(_run_bot(primary_name, token, bot_instance))

    # HiveGPT standalone if token is unique
    if hivegpt_token and hivegpt_token not in token_to_cogs:
        import hivegpt
        _wire_persona_check("HiveGPT", "hivegpt", hivegpt.bot)
        supervisors.append(_run_bot("HiveGPT", hivegpt_token, hivegpt.bot))
    elif hivegpt_token:
        # HiveGPT shares a token. Its functionality needs to be Cog-ified or it will collide.
        # For the sake of this migration, we'll assume HiveGPT's core logic 
        # is handled via the 'ask' command which should be in a cog.
        log.info("HiveGPT shares token with %s. Core logic assumed to be in cogs.", token_to_names[hivegpt_token])

    if not supervisors:
        log.error("No valid tokens found — nothing to run.")
        return

    log.info("HiveSlave Peak: launching %d supervisor(s)", len(supervisors))
    results = await asyncio.gather(*supervisors, return_exceptions=True)

    for name, result in zip(["NishiBee", "Meghdoot", "Premael", "HiveGPT"], results):
        if isinstance(result, Exception):
            log.error("[%s] Supervisor exited with exception: %s", name, result)
        elif isinstance(result, BaseException):
            raise result

def _shutdown_handler(loop: asyncio.AbstractEventLoop) -> None:
    log.info("SIGTERM received — initiating graceful shutdown...")
    for task in asyncio.all_tasks(loop):
        task.cancel()
    try:
        from core import pgcompat
        pgcompat.close_pool()
    except Exception as exc:
        log.warning("Could not close pgcompat pool: %s", exc)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.add_signal_handler(signal.SIGTERM, _shutdown_handler, loop)
    except NotImplementedError:
        pass # Windows doesn't support add_signal_handler for SIGTERM
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
