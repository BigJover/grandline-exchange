"""Async HTTP client for the Grand Line Exchange API."""
import os
import aiohttp
from typing import Optional

SITE_URL     = os.getenv("SITE_URL", "https://grandline-exchange-production.up.railway.app").rstrip("/")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")


async def fetch_announce_state() -> Optional[dict]:
    """Fetch the chapter-announcement handshake state (public endpoint — works
    with no ADMIN_SECRET, so a bare-bones bot deployment can still announce).
    Returns {"chapter_alert": dict|None, "synopsis_ready": str|None} or None
    (older web deploys without the endpoint)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SITE_URL}/public/announce-state",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if isinstance(data, dict):
                        return data
    except Exception:
        pass
    return None


async def fetch_transmission() -> Optional[dict]:
    """Fetch the latest published chapter transmission (synopsis + movers)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SITE_URL}/transmission",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
    except Exception:
        pass
    return None


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


async def fetch_character_full(char_id: int) -> Optional[dict]:
    """Fetch full character data including bio, events, and sbs."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SITE_URL}/characters/{char_id}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
    except Exception:
        pass
    return None


async def find_character(query: str, full: bool = False) -> Optional[dict]:
    """
    Case-insensitive search by name or alias.
    If full=True, fetches the complete record (bio, events, sbs) after matching.
    """
    chars = await fetch_all_characters()
    q = query.lower().strip()

    match = None
    for c in chars:
        if c["name"].lower() == q:
            match = c
            break
    if not match:
        for c in chars:
            if any(a.lower() == q for a in c.get("aliases") or []):
                match = c
                break
    if not match:
        for c in chars:
            if q in c["name"].lower():
                match = c
                break

    if match and full:
        full_data = await fetch_character_full(match["id"])
        return full_data if full_data else match

    return match


def change_pct(char: dict) -> float:
    """Overall % change using first vs last price_history entry."""
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


async def get_kv(key: str) -> str:
    """Read a persistent key from the site's bot KV store."""
    if not ADMIN_SECRET:
        return ""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SITE_URL}/admin/kv/{key}",
                headers={"X-Admin-Secret": ADMIN_SECRET},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    return data.get("value", "")
    except Exception:
        pass
    return ""


async def set_kv(key: str, value: str) -> bool:
    """Write a persistent key to the site's bot KV store."""
    if not ADMIN_SECRET:
        return False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{SITE_URL}/admin/kv/{key}",
                json={"value": value},
                headers={"X-Admin-Secret": ADMIN_SECRET},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
    except Exception:
        return False


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
