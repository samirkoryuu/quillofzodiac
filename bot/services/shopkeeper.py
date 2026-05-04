"""
shopkeeper.py
=============
Madam Wax — the Shopkeeper persona for #hive-life in The Hive.

She is warm, slightly mysterious, a little dramatic, and absolutely
devoted to her craft. She knows every item in the shop by name and has
a story for each one. She's been running Wax & Honey Emporium since
before most of the hive existed.

Usage
-----
    from shopkeeper import shopkeeper_response

    embed = shopkeeper_response("welcome")
    await channel.send(embed=embed)

Response kinds:
    "welcome"     — first time greeting when someone enters
    "shop"        — after !shop / !shopall
    "buy"         — after a successful !buy
    "buy_fail"    — not enough honey or invalid item
    "gift"        — after someone gifts honey
    "mystery_box" — after buying mystery-box
    "wrong"       — anything else typed in the channel
    "slacker"     — someone tried to buy while Slacker Bee
    "rich"        — someone has over 3000 honey
    "broke"       — someone has under 50 honey
    "bye"         — when someone says bye/thanks
"""
from __future__ import annotations

import random
import discord

SHOPKEEPER_NAME = "🧙 Madam Wax"
SHOP_FOOTER = "🍯 Wax & Honey Emporium  ·  #hive-life"
SHOP_COLOR = 0xf5c518
BUY_COLOR = 0x2ecc71
FAIL_COLOR = 0xe74c3c
MYSTERY_COLOR = 0x9b59b6

_LINES: dict[str, list[str]] = {
    "welcome": [
        "Ah, a visitor! *adjusts spectacles* The door chime rang true — welcome to Wax & Honey Emporium, dear. What can Madam Wax do for you today?",
        "*looks up from a leather-bound ledger* Well, well — a customer with good timing. The candles are fresh, the stock is full. Welcome!",
        "*the scent of honey and old parchment fills the air* Come in, come in. I was just cataloguing last season's rarities. Browse as long as you like.",
        "🕯️ *a warm candle flickers as you push open the door* My, my — I had a feeling someone special would visit today. Welcome to Wax & Honey.",
        "*sets down a quill dripping gold* You found the shop. Most people walk right past the sign. You must have writer's instincts — those always lead somewhere interesting.",
    ],
    "shop": [
        "*spreads hands over the display* Here is everything I have in stock. Take your time, dear — honey doesn't rush and neither do I.",
        "A fine selection, curated by yours truly. Each item has a story. Ask about any of them if you'd like to know more.",
        "*taps a shelf thoughtfully* My inventory changes, but the quality never does. What catches your eye?",
        "Everything has a price, everything has a purpose. Some of my items are practical, some are… for the soul. Browse freely.",
        "*unrolls a gilded scroll of items* Today's stock! The mystery boxes are particularly popular this season. Just saying. 👀",
    ],
    "buy": [
        "*wraps your purchase with golden ribbon* Excellent choice. I knew that one would find the right owner eventually. May it serve you well.",
        "*beams with genuine delight* Oh, that one! A personal favourite of mine. You have impeccable taste, and I shall remember that.",
        "*ties a small honey-coloured bow on the package and slides it across the counter* There you are. Do come back — good customers are always welcome.",
        "*holds up your item admiringly before handing it over* This will suit you perfectly. I have a gift for matching people to their right purchases.",
        "Splendid! *stamps a tiny wax seal on your receipt* Keep this somewhere safe. And if anyone asks where you got it — tell them Madam Wax, naturally.",
    ],
    "buy_fail": [
        "*clicks tongue sympathetically* Oh dear, not quite enough honey for that one. Earn a little more — chat, write, win a trivia round — and come back. It'll still be here.",
        "*peers at the ledger* Hmm, the numbers don't quite balance, love. A bit more honey and we'll have a deal. I'm not going anywhere.",
        "*shakes head gently* That item has a price for a reason — it's worth it! A few more days of writing and you'll have enough. I'm rooting for you.",
        "Almost! *taps the counter twice* Try `!honey` to see your balance, and `!checkin` every day to earn more. I'll hold your interest.",
    ],
    "gift": [
        "*watches with a hand over heart* Oh, how lovely. Generosity is the finest currency in any economy. The hive is lucky to have you.",
        "A gift! *dabs eye with handkerchief* This is why I love this community. You're a good bee, dear.",
        "*marks the transaction in the golden ledger* Recorded forever. Kindness compounds over time — remember that.",
    ],
    "mystery_box": [
        "*eyes gleaming* A mystery box! My favourite sale. I never know exactly what's inside either — that's the honest truth. But it's always something worthwhile.",
        "*whispers conspiratorially* Between you and me, the mystery boxes sometimes have things that aren't even listed in the shop. Sometimes. 🎁",
        "*shakes the box gently before handing it over* I can hear something in there. I think it likes you already.",
    ],
    "wrong": [
        "*peers over spectacles* Hmm? Were you looking for something specific? Try `!shop` to see my wares, or `!buy <item_id>` to make a purchase.",
        "🛒 This is a shop, dear, not a café. `!shop` to browse, `!buy <item_id>` to purchase. Simple as honey.",
        "*tilts head with patient smile* I didn't quite catch that. The magic words here are `!shop` and `!buy`. Give one a try?",
        "*continues polishing a jar of honey* I'm happy to chat, but only through the counter. `!shop` when you're ready.",
    ],
    "slacker": [
        "*frowns with genuine concern* Oh sweetheart. I can see you're in a spot of trouble. Finish your ticket in #ticket-room first — then come back to shop. Your items will wait.",
        "I would love to serve you, truly. But Madam Wax has a policy: settle your debts before new purchases. Head to #ticket-room — you'll feel better once it's done.",
    ],
    "rich": [
        "*raises an impressed eyebrow* My my… you have quite the honey reserve. The hive's finest customer, I dare say. What would you like today?",
        "*polishes a special shelf just for you* For a customer of your standing, nothing but the best. `!shopall` to see everything.",
    ],
    "broke": [
        "*pats hand kindly* Don't worry, dear. Everyone starts somewhere. Chat in the hive, use `!checkin` daily, win a duel or two — the honey adds up faster than you'd think.",
        "Ah, between pay periods? I understand perfectly. Come back when the wallet's fuller — everything will still be here. `!checkin` helps every day.",
    ],
    "bye": [
        "*waves from behind the counter* Do come back soon! Madam Wax is always here. 🕯️",
        "Until next time, dear! The candles will be burning when you return.",
        "*bows slightly* It was a pleasure. Safe travels through the hive. 🍯",
    ],
}


def shopkeeper_response(kind: str, *, extra_text: str = "") -> discord.Embed:
    """Return a styled embed for the given shopkeeper response kind."""
    lines = _LINES.get(kind, _LINES["wrong"])
    line = random.choice(lines)
    if extra_text:
        line = f"{line}\n\n{extra_text}"

    color_map = {
        "buy": BUY_COLOR,
        "gift": BUY_COLOR,
        "mystery_box": MYSTERY_COLOR,
        "buy_fail": FAIL_COLOR,
        "slacker": FAIL_COLOR,
        "wrong": 0xe67e22,
    }
    color = color_map.get(kind, SHOP_COLOR)

    embed = discord.Embed(
        description=f"**{SHOPKEEPER_NAME}:** {line}",
        color=color,
    )
    embed.set_footer(text=SHOP_FOOTER)
    return embed
