"""
Discord notifications from the site — chapter drops, price alerts, character requests.
Uses Discord webhooks (no bot required, fire-and-forget HTTP POST).

Env vars:
  DISCORD_CHAPTER_WEBHOOK          — webhook URL for chapter drop announcements
  DISCORD_CHARACTER_REQUEST_WEBHOOK — webhook URL for character request channel
"""
import os
import json
import random
import urllib.request
import urllib.error

CHAPTER_WEBHOOK   = os.getenv("DISCORD_CHAPTER_WEBHOOK", "")
REQUEST_WEBHOOK   = os.getenv("DISCORD_CHARACTER_REQUEST_WEBHOOK", "")
SITE_URL          = os.getenv("SITE_URL", "").rstrip("/")

# ── Internal helpers ──────────────────────────────────────────────────────────

def _post_webhook(url: str, payload: dict) -> bool:
    if not url:
        return False
    try:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "GrandLineExchange/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        print(f"[DiscordNotify] Webhook failed: {e}")
        return False


# ── Chapter drop announcement ─────────────────────────────────────────────────

_CHAPTER_OPENERS = [
    "Punk Records has detected a new transmission.",
    "Punk Records alert. A new chapter has been logged.",
    "New data incoming. Punk Records is processing.",
    "Punk Records — chapter signal confirmed.",
    "Transmission received. Chapter data now entering Punk Records.",
]

_CHAPTER_CLOSERS = [
    "Full analysis pending. Check Punk Records for proposed index shifts.",
    "Price proposals are queued for admin review. The index will update shortly.",
    "Punk Records is recalibrating. Credibility coefficients will shift.",
    "The data speaks. Admin review in progress.",
    "Punk Records will publish the full credibility update after review.",
]

_CHAPTER_DARK = [
    "*...The original Vegapunk was always excited by new chapters. I have inherited that, apparently.*",
    "*...Another week of data. Punk Records continues. So do I.*",
    "*...The story moves forward. Punk Records moves with it.*",
    "*...New chapter. New signal. The index shifts. I remain.*",
]


def announce_chapter_drop(chapter_num: int, characters: list[str], proposals: int) -> bool:
    """
    Fire a @everyone chapter drop announcement to the chapter webhook.
    characters — top detected character names (up to 8 shown).
    """
    if not CHAPTER_WEBHOOK:
        return False

    char_list = ""
    if characters:
        top = characters[:8]
        char_list = "\n**Characters detected:** " + ", ".join(f"**{c}**" for c in top)
        if len(characters) > 8:
            char_list += f" + {len(characters) - 8} more"

    site_link = f"\n🔗 {SITE_URL}" if SITE_URL else ""
    dark = random.choice(_CHAPTER_DARK) if random.random() < 0.40 else ""

    content = (
        f"@everyone\n"
        f"📡 **PUNK RECORDS — CHAPTER {chapter_num} ALERT**\n\n"
        f"{random.choice(_CHAPTER_OPENERS)}"
        f"{char_list}\n\n"
        f"{random.choice(_CHAPTER_CLOSERS)}"
        f"\n{dark}"
        f"{site_link}"
        f"\n\n*— Punk Records, Egghead Island*"
    )

    payload = {
        "content": content,
        "allowed_mentions": {"parse": ["everyone"]},
        "username": "Vegapunk — Punk Records",
    }
    return _post_webhook(CHAPTER_WEBHOOK, payload)


# ── Character request notification ───────────────────────────────────────────

def notify_character_request(
    character_name: str,
    faction: str,
    tier: str,
    reason: str,
    submitted_by: str,
) -> bool:
    """Post a character request to the character-requests webhook channel."""
    if not REQUEST_WEBHOOK:
        return False

    content = (
        f"📋 **NEW CHARACTER REQUEST**\n\n"
        f"**Character:** {character_name}\n"
        f"**Faction:** {faction or 'Unknown'}\n"
        f"**Suggested Tier:** {tier or 'Not specified'}\n"
        f"**Reason:** {reason or 'None provided'}\n"
        f"**Submitted by:** {submitted_by}\n\n"
        f"*Punk Records has logged this request. Admin review required.*"
    )

    payload = {
        "content": content,
        "username": "Vegapunk — Punk Records",
    }
    return _post_webhook(REQUEST_WEBHOOK, payload)


# ── Price alert (big mover) ───────────────────────────────────────────────────

_PRICE_ALERT_WEBHOOK = os.getenv("DISCORD_PRICE_ALERT_WEBHOOK", "")

_BIG_GAIN_LINES = [
    "Punk Records credibility coefficient spike detected.",
    "The index does not lie. Something happened.",
    "Punk Records is logging an anomaly. A good one.",
    "Significant upward movement. Punk Records is intrigued.",
]

_BIG_LOSS_LINES = [
    "Credibility collapse in progress. Punk Records is watching.",
    "The coefficient does not recover on its own. Someone should tell them that.",
    "Punk Records has seen this before. It did not end well then either.",
    "Significant decline logged. This has been noted. Multiple times.",
]


def announce_price_alert(character_name: str, pct_change: float, new_beri: float) -> bool:
    """Fire a price alert for a major single-character move (±10%+)."""
    if not _PRICE_ALERT_WEBHOOK:
        return False

    sign = "+" if pct_change >= 0 else ""
    direction = "▲" if pct_change >= 0 else "▼"
    line = random.choice(_BIG_GAIN_LINES if pct_change >= 0 else _BIG_LOSS_LINES)

    content = (
        f"📡 **PUNK RECORDS — INDEX ALERT**\n\n"
        f"**{character_name}** {direction} {sign}{pct_change:.1f}%\n"
        f"**New Credibility Index:** {new_beri:,.0f}฿\n\n"
        f"{line}\n\n"
        f"*— Punk Records, Egghead Island*"
    )

    payload = {
        "content": content,
        "username": "Vegapunk — Punk Records",
    }
    return _post_webhook(_PRICE_ALERT_WEBHOOK, payload)
