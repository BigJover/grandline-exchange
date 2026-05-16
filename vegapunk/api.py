"""Async HTTP client for the Grand Line Exchange API."""
import os
import aiohttp
from typing import Optional

SITE_URL   = os.getenv("SITE_URL", "https://grandline-exchange.up.railway.app").rstrip("/")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")


async def fetch_all_characters() -> list[dict]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SITE_URL}/characters/",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
    except Exception:
        pass
    return []


async def find_character(query: str) -> Optional[dict]:
    """Case-insensitive search by name or alias. Returns first match."""
    chars = await fetch_all_characters()
    q = query.lower().strip()
    # exact name
    for c in chars:
        if c["name"].lower() == q:
            return c
    # alias exact
    for c in chars:
        if any(a.lower() == q for a in c.get("aliases") or []):
            return c
    # substring
    for c in chars:
        if q in c["name"].lower():
            return c
    return None


def change_pct(char: dict) -> float:
    """
    Recent % change using the last two price_history entries.
    price_history entries are dicts: {"chapter": int, "label": str, "beri": float}
    Falls back to comparing first vs last entry, then to 0.
    """
    history: list = char.get("price_history") or []
    if len(history) >= 2:
        first_beri = history[0].get("beri", 0)
        last_beri  = history[-1].get("beri", 0)
        if first_beri and first_beri > 0:
            return (last_beri - first_beri) / first_beri * 100
    return 0.0


def recent_change_pct(char: dict) -> float:
    """% change over the last 3 price_history entries (short-term sentiment)."""
    history: list = char.get("price_history") or []
    window = history[-3:] if len(history) >= 3 else history
    if len(window) >= 2:
        first_beri = window[0].get("beri", 0)
        last_beri  = window[-1].get("beri", 0)
        if first_beri and first_beri > 0:
            return (last_beri - first_beri) / first_beri * 100
    return change_pct(char)


async def ingest_message(channel: str, author: str, content: str) -> bool:
    """Forward a Discord message to /admin/discord-ingest for character intelligence."""
    if not ADMIN_SECRET or not content.strip():
        return False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{SITE_URL}/admin/discord-ingest",
                json={"channel": channel, "author": author, "content": content},
                headers={"X-Admin-Secret": ADMIN_SECRET},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
    except Exception:
        return False
