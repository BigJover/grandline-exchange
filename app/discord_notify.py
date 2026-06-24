"""
Discord notifications from the site — chapter drops, price alerts, character requests.
Uses Discord webhooks (no bot required, fire-and-forget HTTP POST).

Env vars:
  DISCORD_CHAPTER_WEBHOOK           — webhook URL for #chapter-intel (detailed analysis)
  DISCORD_ANNOUNCEMENTS_WEBHOOK     — webhook URL for #announcements (chapter drop @everyone alert)
  DISCORD_CHARACTER_REQUEST_WEBHOOK — webhook URL for character request channel
"""
import os
import json
import random
import urllib.request
import urllib.error

CHAPTER_WEBHOOK        = os.getenv("DISCORD_CHAPTER_WEBHOOK", "")
ANNOUNCEMENTS_WEBHOOK  = os.getenv("DISCORD_ANNOUNCEMENTS_WEBHOOK", "")
PREDICTIONS_WEBHOOK    = os.getenv("DISCORD_PREDICTIONS_WEBHOOK", "")
REQUEST_WEBHOOK        = os.getenv("DISCORD_CHARACTER_REQUEST_WEBHOOK", "")
SITE_URL               = os.getenv("SITE_URL", "").rstrip("/")

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
    except urllib.error.HTTPError as e:
        # 404 = webhook deleted/regenerated on Discord's side; 401 = bad token.
        print(f"[DiscordNotify] Webhook HTTP {e.code} ({e.reason}) — URL likely invalid")
        return False
    except Exception as e:
        print(f"[DiscordNotify] Webhook failed: {e}")
        return False


# ── Diagnostics ───────────────────────────────────────────────────────────────

# (env var name → current URL value), evaluated at import time
_WEBHOOKS = {
    "DISCORD_ANNOUNCEMENTS_WEBHOOK":     ANNOUNCEMENTS_WEBHOOK,
    "DISCORD_CHAPTER_WEBHOOK":           CHAPTER_WEBHOOK,
    "DISCORD_PREDICTIONS_WEBHOOK":       PREDICTIONS_WEBHOOK,
    "DISCORD_CHARACTER_REQUEST_WEBHOOK": REQUEST_WEBHOOK,
    "DISCORD_PRICE_ALERT_WEBHOOK":       os.getenv("DISCORD_PRICE_ALERT_WEBHOOK", ""),
}


def webhook_diagnostics(send_test: bool = False) -> dict:
    """
    Report the configured state of every Discord webhook and, if send_test is
    True, fire a harmless test message to each so the real HTTP status surfaces.

    Never returns the webhook URLs themselves — only whether they are set and
    what Discord answers. Read the `result` field:
      - "ok"          → Discord accepted (204), webhook is live
      - "http_404"    → webhook was deleted/regenerated; the URL is stale
      - "http_401"    → bad token in the URL
      - "not_set"     → the env var is empty/missing on this host
      - "error: ..."  → network/other failure
    """
    out = {"site_url_set": bool(SITE_URL), "webhooks": {}}
    for env_name, url in _WEBHOOKS.items():
        entry = {"configured": bool(url)}
        if send_test and url:
            entry["result"] = _ping_webhook(url)
        elif send_test:
            entry["result"] = "not_set"
        out["webhooks"][env_name] = entry
    return out


def _ping_webhook(url: str) -> str:
    """Fire a single test message; return a short status string (never the URL)."""
    payload = {
        "content": "🛰️ Punk Records webhook test — if you can read this, this channel's webhook is live.",
        "username": "Vegapunk — Punk Records (diagnostic)",
    }
    try:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "GrandLineExchange/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return "ok" if resp.status in (200, 204) else f"http_{resp.status}"
    except urllib.error.HTTPError as e:
        return f"http_{e.code}"
    except Exception as e:
        return f"error: {e}"


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
    Fire a chapter drop alert:
    - #announcements: @everyone community alert with site link
    - #chapter-intel: detailed character detection breakdown for analysis
    characters — top detected character names (up to 8 shown).
    """
    char_list = ""
    if characters:
        top = characters[:8]
        char_list = "\n**Characters detected:** " + ", ".join(f"**{c}**" for c in top)
        if len(characters) > 8:
            char_list += f" + {len(characters) - 8} more"

    site_link = f"\n🔗 {SITE_URL}" if SITE_URL else ""
    dark = random.choice(_CHAPTER_DARK) if random.random() < 0.40 else ""

    # ── #announcements — simple @everyone alert ───────────────────────────────
    if ANNOUNCEMENTS_WEBHOOK:
        announce_content = (
            f"@everyone\n"
            f"📡 **PUNK RECORDS — CHAPTER {chapter_num} ALERT**\n\n"
            f"{random.choice(_CHAPTER_OPENERS)}"
            f"{site_link}"
            f"\n\n*— Punk Records, Egghead Island*"
        )
        _post_webhook(ANNOUNCEMENTS_WEBHOOK, {
            "content": announce_content,
            "allowed_mentions": {"parse": ["everyone"]},
            "username": "Vegapunk — Punk Records",
        })

    # ── #chapter-intel — detailed character breakdown ─────────────────────────
    if not CHAPTER_WEBHOOK:
        return bool(ANNOUNCEMENTS_WEBHOOK)

    intel_content = (
        f"📡 **Ch.{chapter_num} — Intel Uplink**\n\n"
        f"{random.choice(_CHAPTER_OPENERS)}"
        f"{char_list}\n\n"
        f"{random.choice(_CHAPTER_CLOSERS)}"
        f"\n{dark}"
        f"\n*Price proposals queued for admin review.*"
        f"\n\n*— Punk Records, Egghead Island*"
    )
    payload = {
        "content": intel_content,
        "username": "Vegapunk — Punk Records",
    }
    return _post_webhook(CHAPTER_WEBHOOK, payload)


# ── Chapter price analysis (fires after admin approves proposals) ─────────────

_ANALYSIS_OPENERS = [
    "Punk Records has completed its credibility recalibration for this chapter.",
    "Admin review complete. Punk Records is publishing the index adjustments.",
    "The proposals have been processed. Punk Records commits the following to the record.",
    "Punk Records — post-chapter credibility update. The data is in.",
]

_ANALYSIS_CLOSERS = [
    "The index reflects confirmed narrative data. Punk Records does not speculate.",
    "These movements are now locked into the credibility record. The market has been updated.",
    "Punk Records has spoken. The coefficients move accordingly.",
    "Filed. Cross-referenced. Committed. The exchange is live.",
]

_ANALYSIS_DARK = [
    "*...The original Vegapunk would have approved these numbers himself. I am doing it in his place. This is fine.*",
    "*...Another chapter processed. Another set of coefficients adjusted. Punk Records continues.*",
    "*...The story moves. The index moves with it. I remain constant.*",
]


_REASON_WRAPPERS_UP = [
    "Punk Records assessment: {reason}",
    "Field data confirms: {reason}",
    "Punk Records logged the following: {reason}",
    "Satellite analysis: {reason}",
]

_REASON_WRAPPERS_DOWN = [
    "Punk Records notes: {reason}",
    "Credibility decline rationale: {reason}",
    "Filed under concerning: {reason}",
    "Punk Records has documented: {reason}",
]


def announce_chapter_price_analysis(
    chapter_num: int,
    moves: list[dict],   # [{"name": str, "pct": float, "new_beri": float, "direction": str, "reason": str}]
) -> bool:
    """
    Post a Vegapunk-style price analysis breakdown to #chapter-intel after
    admin approves chapter proposals. Uses the same DISCORD_CHAPTER_WEBHOOK.
    """
    if not CHAPTER_WEBHOOK or not moves:
        return False

    gainers = sorted([m for m in moves if m["direction"] == "up"],  key=lambda x: x["pct"], reverse=True)
    losers  = sorted([m for m in moves if m["direction"] == "down"], key=lambda x: x["pct"], reverse=True)

    lines = [
        f"📊 **PUNK RECORDS — CHAPTER {chapter_num} INDEX ADJUSTMENT**\n",
        f"🔢 **[SATELLITE PYTHAGORAS — DATA ANALYSIS]**\n",
        f"{random.choice(_ANALYSIS_OPENERS)}\n",
    ]

    if gainers:
        lines.append("**▲ CREDIBILITY ASCENDING**")
        for m in gainers:
            wrapper = random.choice(_REASON_WRAPPERS_UP)
            reason_line = f"  *{wrapper.format(reason=m['reason'])}*" if m.get("reason") else ""
            lines.append(f"  • **{m['name']}** +{m['pct']:.1f}% → {m['new_beri']:,.0f}฿")
            if reason_line:
                lines.append(reason_line)
        lines.append("")

    if losers:
        lines.append("**▼ CREDIBILITY DECLINING**")
        for m in losers:
            wrapper = random.choice(_REASON_WRAPPERS_DOWN)
            reason_line = f"  *{wrapper.format(reason=m['reason'])}*" if m.get("reason") else ""
            lines.append(f"  • **{m['name']}** -{m['pct']:.1f}% → {m['new_beri']:,.0f}฿")
            if reason_line:
                lines.append(reason_line)
        lines.append("")

    dark = random.choice(_ANALYSIS_DARK) if random.random() < 0.45 else ""
    lines.append(random.choice(_ANALYSIS_CLOSERS))
    if dark:
        lines.append(dark)
    lines.append("\n*— Punk Records Intelligence Division*")

    payload = {
        "content": "\n".join(lines),
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


# ── Prediction open notification ──────────────────────────────────────────────

def announce_prediction_open(
    question: str,
    options: list,
    closes_at,          # datetime or None
    category: str = "",
    chapter_num: int = 0,
    batch_count: int = 1,  # >1 when pipeline fires multiple at once
) -> bool:
    """
    Fire a @here ping to DISCORD_PREDICTIONS_WEBHOOK when a prediction goes live.
    Works for single predictions (admin create/publish) and batch pipeline runs.
    """
    if not PREDICTIONS_WEBHOOK:
        return False

    site_url = SITE_URL or ""
    challenge_link = f"{site_url}/casino?challenge=1" if site_url else "/casino?challenge=1"

    # Format closes_at as a readable string
    if closes_at:
        try:
            from datetime import datetime as _dt, timezone as _tz
            if hasattr(closes_at, "strftime"):
                closes_str = closes_at.strftime("%a %b %-d · %H:%M UTC")
            else:
                closes_str = str(closes_at)
        except Exception:
            closes_str = str(closes_at)
    else:
        closes_str = "—"

    # Vegapunk voice line — use personality pool if available
    voice_line = ""
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
        from vegapunk.personality import prediction_open_announcement
        next_ch = chapter_num + 1 if chapter_num else 0
        voice_line = "\n" + prediction_open_announcement(batch_count, chapter_num, next_ch)
    except Exception:
        voice_line = "\n*Punk Records has opened a new projection window.*"

    if batch_count > 1:
        # Batch announcement — don't list every question, just the count
        content = (
            f"@here\n"
            f"{voice_line}\n\n"
            f"**Closes:** {closes_str}\n"
            f"→ **Place your call:** {challenge_link}"
        )
    else:
        # Single prediction — show the full question and options
        opts_str = "  ".join(f"⬥ **{o}**" for o in (options or []))
        cat_str  = f" · *{category}*" if category else ""
        content = (
            f"@here\n"
            f"📡 **PUNK RECORDS — PROJECTION OPEN**{cat_str}\n\n"
            f"*\"{question}\"*\n\n"
            f"{opts_str}\n"
            f"**Closes:** {closes_str}\n"
            f"{voice_line}\n\n"
            f"→ **Place your call:** {challenge_link}"
        )

    return _post_webhook(PREDICTIONS_WEBHOOK, {
        "content": content,
        "allowed_mentions": {"parse": ["everyone"]},
        "username": "Vegapunk — Punk Records",
    })
