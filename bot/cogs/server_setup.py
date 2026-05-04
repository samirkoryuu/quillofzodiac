"""
server_setup.py — Quill of Zodiac: The Eclipse
═══════════════════════════════════════════════
Plain-text message builder for Meghdoot's server rules and guides.
No embeds, just clean Markdown.
"""

import os

SERVER_NAME = "Quill of Zodiac: The Eclipse"
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://hiveslave.peak")

def build_rules_msgs() -> list[str]:
    """Returns a list of plain-text messages for the rules channel."""
    
    msg1 = (
        f"# 📜 THE DECREE OF THE HIVE\n"
        f"## Welcome to **{SERVER_NAME}**\n\n"
        "I am **Meghdoot**, the orchestrator of this sanctuary. This is not merely a server; it is a collective of "
        "souls dedicated to the ancient craft of the written word. To maintain the purity of our hive, you must "
        "adhere to the following laws. Ignorance is no excuse for transgression.\n\n"
        "> *Breaking these rules will result in the loss of Honey (🍯), XP, or your hard-earned Rank. "
        "Three serious violations will lead to permanent exile (Ban).* ⚖️"
    )

    msg2 = (
        "## 🐝 SECTION 1 — CODE OF CONDUCT\n\n"
        "**Rule 1: The Law of Respect**\n"
        "Every writer here is a fellow architect of worlds. Treat them as such. Insults, personal attacks, "
        "hate speech, or passive-aggressive behavior will not be tolerated. We may disagree on craft, but we "
        "shall do so with class.\n\n"
        "**Rule 2: The Purity of Content (PG-13)**\n"
        "NSFW content is forbidden. No graphic sexual text, imagery, or links. We keep this environment "
        "welcoming for all writers aged 13 and above. Mature themes in your books must remain in designated "
        "channels with clear warnings.\n\n"
        "**Rule 3: The Silence of Spam**\n"
        "Do not flood our channels with repetitive noise or excessive line breaks. This includes "
        "chain-messaging commands at the bots. Let the conversation flow like a calm river, not a flood.\n\n"
        "**Rule 4: The Language of the Hive**\n"
        "Our primary tongue is English. You may use other languages in the lounge, but please provide a 🌐 "
        "tag so we may all understand your intent.\n\n"
        "**Rule 5: The Ban on Unsolicited Promotion**\n"
        "Do not advertise external servers, stores, or services without my explicit approval. Your "
        "original books have their own home in the author channels—keep them there."
    )

    msg3 = (
        "## ✍️ SECTION 2 — THE WRITER'S COVENANT\n\n"
        "**Rule 6: The Sin of Plagiarism**\n"
        "Never claim work that is not yours. AI-generated text passed off as original authorship is a "
        "grave offense. You may use AI to edit or brainstorm, but the soul of the story must be your own.\n\n"
        "**Rule 7: The Art of Critique**\n"
        "Feedback must be a bridge, not a hammer. When critiquing a fellow writer, use the Sandwich Method: "
        "Positive → Area for Improvement → Positive. We grow through encouragement, not destruction.\n\n"
        "**Rule 8: The Weight of the Ticket**\n"
        "When you accept a **Blessed Bee Ticket**, you give your word to meet the deadline. Failing to do so "
        "without a 24-hour notice earns you the **Slacker Bee** role—a mark of shame that includes a rank "
        "demotion until your debt is paid.\n\n"
        "**Rule 9: Respect the Sanctuaries**\n"
        "Every channel has a purpose. Writing prompts in `#daily-buzz`, snippets in `#snippets`, "
        "and technical queries in `#helpbee`. Do not clutter the hive."
    )

    msg4 = (
        "## 👁️ SECTION 3 — THE RANK LADDER (27 TIERS)\n\n"
        "Your rank is a reflection of your dedication. I oversee the verification through HiveGPT's vision.\n\n"
        "### THE HIERARCHY:\n"
        "👁️ **GODSCRIBES** — The peak of authorship. Those who have mastered the word.\n"
        "🌌 **TRANSCENDENT & DIVINE** — Mythic figures of consistency.\n"
        "🌠 **ASTRAL & CELESTIAL** — Veteran architects of the hive.\n"
        "👑 **LEGEND & ETHEREAL** — Elite storytellers.\n"
        "🌙 **GRANDSCRIBES & WORDLORDS** — Masterful producers.\n"
        "🔥 **WORDSMITHS (I-III)** — Dedicated daily writers.\n"
        "📖 **STORYTELLERS (I-III)** — Developing talents.\n"
        "✒️ **QUILLBEARERS (I-III)** — Emerging voices.\n"
        "📝 **INKLING** — Your first true milestone.\n"
        "🪶 **NEWBIE WRITER** — Where every journey begins.\n\n"
        "**How to Promote:**\n"
        "Post your Inkstone Dashboard screenshot in **#update-ocr**. My vision will process your counts and "
        "award your role automatically. 🤖✨"
    )

    msg5 = (
        "## ⚖️ SECTION 4 — STAFF & ENFORCEMENT\n\n"
        "The decisions of the **Bee Staff** and **Founders** are final. If you have a grievance, bring it to "
        "an Admin privately. Publicly contesting my staff is a violation of the hive's peace.\n\n"
        "### PENALTY TIERS:\n"
        "🟡 **Verbal Warning** — A gentle nudge back to the path.\n"
        "🟠 **Honey Fine** — A penalty of 50–500 🍯.\n"
        "🔴 **Rank Demotion** — You are cast down one tier.\n"
        "⛔ **Timeout** — Temporary exclusion from the hive.\n"
        "☠️ **Permanent Ban** — You are erased from the records.\n\n"
        "**Walk with purpose, Writer. The Hive is watching.** 🐝"
    )

    return [msg1, msg2, msg3, msg4, msg5]

def build_commands_msgs() -> list[str]:
    """Returns a list of plain-text messages for the bot-guide channel."""
    
    msg1 = (
        f"# 📖 THE HIVE — COMMAND LEXICON\n"
        f"## How to Interact with the Bots\n\n"
        "All interactions are handled via slash (`/`) commands. Type `/` in any channel to see the available options. "
        "Most bot replies are **ephemeral**—visible only to you—to keep our halls clean.\n\n"
        f"🌐 **YOUR DASHBOARD:** [Click Here]({DASHBOARD_URL})"
    )

    msg2 = (
        "## 🍯 ECONOMY & IDENTITY\n"
        "**`/honey`** — View your current balance and progress.\n"
        "**`/profile`** — See your full stats, badges, and rank.\n"
        "**`/leaderboard`** — Witness the most industrious bees.\n"
        "**`/website`** — Get your private dashboard login link."
    )

    msg3 = (
        "## 🛒 THE HONEY SHOP\n"
        "**`/shop`** — Browse the marketplace.\n"
        "**`/buy <id>`** — Exchange honey for roles, colors, or items.\n"
        "**`/inventory`** — View your purchased possessions."
    )

    msg4 = (
        "## ✍️ WRITING & SUBMISSIONS\n"
        "**`/submit`** — Log your daily progress to earn honey.\n"
        "**`/update-ocr`** — Post your dashboard screenshot in #update-ocr for auto-rank.\n"
        "**`/duel @member`** — Challenge a writer; HiveGPT will judge the victor."
    )

    return [msg1, msg2, msg3, msg4]

def build_founders_msgs() -> list[str]:
    """Returns a list of plain-text messages for the foundry channel."""
    
    msg = (
        "# 🏛️ THE FOUNDRY — ARCHITECT CONTROLS\n"
        "## Staff & Founder Commands\n\n"
        "**`/setup_myserver`** — Full bootstrap (roles, channels, rules)\n"
        "**`/nuke_and_setup`** — Complete reconstruction (Destructive + Anchor)\n"
        "**`/setup_panels`** — Deploy Verification & Ticket desks\n"
        "**`/refresh_rules`** — Wipe & repost all rules/guides\n"
        "**`/start_ticket_cycle`** — Trigger a new 3-day update period\n"
        "**`/set_stats @member`** — Manually override a writer's statistics\n"
        "**`/force_verify @member`** — Manually verify a writer\n"
        "**`/toggle_dailies`** — Control automated background tasks\n"
        "**`/backup_db`** — Get a summary of the database health\n"
        "**`/sync_shop`** — Sync local items to Hive Dashboard"
    )
    return [msg]
