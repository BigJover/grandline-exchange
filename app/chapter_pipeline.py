"""
Chapter drop detection + price proposal pipeline.

enrich_chapter_with_youtube(db, chapter_num)
    - Supplements existing price proposals with YouTube reaction video sentiment.
    - Requires YOUTUBE_API_KEY env var (YouTube Data API v3, free, ~10k quota/day).
    - Searches for "One Piece chapter N reaction" videos, fetches top comments,
      extracts character mentions, and:
        * Appends YouTube signal to existing pending proposals' reason strings
        * Creates new +1% proposals for characters only YouTube caught
    - Returns {"chapter", "yt_chars_found", "proposals_updated", "proposals_added"}
    - Safe to call repeatedly — idempotent (skips if YouTube note already in reason)

detect_chapter_drop(db, force_chapter=None)
    - Sweeps multiple sources for a new chapter:
        1. One Piece Fandom Wiki  — authoritative, no auth, always works
        2. Reddit (/new /hot /search) — supplementary; works with OAuth creds,
           may be blocked on Railway without them
        3. Manual override via force_chapter param
    - If a new chapter is found (higher than anything in `chapters` table):
        * Creates a Chapter record
        * Extracts character appearances from wiki chapter page
        * Scrapes top 50 Reddit comments (if Reddit is accessible)
        * Combines with Reddit pulse mention scores + site trading data
        * Generates ProposedPriceChange rows for admin to review
    - Returns {"detected": bool, "chapter": int|None, "proposals": int,
               "message": str, "sources": list}
    - Safe to call repeatedly — idempotent on the same chapter number

Price proposal tiers (by combined-signal rank):
    #1       → +7 %   capped by mean reversion
    #2       → +5 %
    #3–4     → +3.5 %
    #5–7     → +2 %
    #8–12    → +1 %
    net_buy < -5 (sell pressure) → override to -2.5 %
    net_buy < -10               → override to -4 %
    Mean reversion cap: if beri > base_beri × 3, upward proposals capped at +0.5 %
"""

import os
import re
import html
import json
import math
import time
import base64
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app import models

# Discord announcements are no longer fired here via webhooks. Chapter alerts and
# synopses are handed off to the Vegapunk bot through the BotKV handshake below
# (chapter_alert / chapter_synopsis_ready), so every announcement comes from the
# bot in its own voice — System B.


def _set_kv(db: Session, key: str, value: str) -> None:
    """Write a key into the BotKV store the Vegapunk bot polls."""
    row = db.query(models.BotKV).filter(models.BotKV.key == key).first()
    if row:
        row.value = value
    else:
        db.add(models.BotKV(key=key, value=value))
    db.commit()


# ── Shared fetch helpers ──────────────────────────────────────────────────────

_UA = "GrandLineExchange:ChapterDetect:v1.0 (chapter drop detection for fan site)"

_REDDIT_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json",
}

_WIKI_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json",
}

_REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_oauth_token: dict = {"token": None, "expires_at": 0.0}


def _get_oauth_token() -> Optional[str]:
    """Get Reddit OAuth2 app-only access token. Returns None if creds not set."""
    client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None

    now = time.time()
    if _oauth_token["token"] and now < _oauth_token["expires_at"] - 60:
        return _oauth_token["token"]

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        _REDDIT_TOKEN_URL,
        data=b"grant_type=client_credentials",
        headers={
            "Authorization": f"Basic {credentials}",
            "User-Agent": _UA,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            token = result.get("access_token")
            expires_in = result.get("expires_in", 3600)
            _oauth_token["token"] = token
            _oauth_token["expires_at"] = now + expires_in
            print(f"[ChapterPipeline] Reddit OAuth token acquired (expires in {expires_in}s)")
            return token
    except Exception as e:
        print(f"[ChapterPipeline] OAuth token fetch failed: {e}")
        return None


def _fetch_reddit(url: str, timeout: int = 8):
    """Fetch a Reddit listing/comments endpoint, normalized to the JSON API shape.

    Two paths, transparent to callers:
      1. OAuth JSON (oauth.reddit.com) when REDDIT_CLIENT_ID/SECRET are set — the
         richer feed (exposes score + distinguished). This path is dormant today
         because Reddit disabled self-serve app creation (Responsible Builder
         Policy); it re-activates automatically if valid creds ever land on Railway.
      2. Public RSS (.rss) fallback — no app/OAuth needed, and unlike the public
         JSON API (which 403s from datacenter IPs like Railway) the RSS feeds are
         reachable. Parsed back into the same {"data": {"children": [...]}} shape.
    """
    token = _get_oauth_token()
    if token:
        oauth_url = url.replace("https://www.reddit.com/", "https://oauth.reddit.com/")
        try:
            req = urllib.request.Request(
                oauth_url,
                headers={**_REDDIT_HEADERS, "Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print(f"[ChapterPipeline] Reddit OAuth fetch failed ({oauth_url}): {e}")

    # No OAuth (or it failed): read the equivalent public RSS feed.
    return _fetch_reddit_rss(url, timeout)


# ── Reddit RSS fallback (no API/OAuth) ────────────────────────────────────────
# Reddit rate-limits RSS aggressively per IP (a burst of ~5 requests trips a 429
# even at ~1.3s spacing — observed live), so space requests generously and back
# off hard on 429. Every caller is a background job, so latency is free; partial
# feeds are fine too — the buzz sweep accumulates across runs all week.
_RSS_MIN_GAP = 3.0          # seconds enforced between successive RSS requests
_rss_last = {"t": 0.0}


def _throttle_rss() -> None:
    wait = _RSS_MIN_GAP - (time.time() - _rss_last["t"])
    if wait > 0:
        time.sleep(wait)
    _rss_last["t"] = time.time()


def _strip_html(s: str) -> str:
    """Reddit RSS wraps titles/bodies in escaped HTML — strip tags, unescape entities."""
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def _parse_reddit_rss(url: str, xml_text: str):
    """Parse a Reddit Atom feed into the JSON API shape the callers expect.

    Listing feeds (new/hot/search) → {"data": {"children": [{"data": {...}}]}}
    Per-post comments feed (/comments/<id>.rss) → [{}, {"data": {"children":[...]}}]
    (matches the 2-element array the JSON comments endpoint returns).

    RSS omits score and distinguished, so those degrade to 0 / None: Reddit won't
    self-advance the chapter or solo-confirm a break week via RSS, but character
    mention sentiment (the primary value) is fully preserved.
    """
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        print(f"[ChapterPipeline] Reddit RSS parse failed ({url}): {e}")
        return None

    ns = "{http://www.w3.org/2005/Atom}"
    is_comments = bool(re.search(r"/comments/[a-z0-9]+\.rss", url))
    children = []
    for entry in root.findall(f"{ns}entry"):
        def _txt(tag: str) -> str:
            el = entry.find(f"{ns}{tag}")
            return el.text if el is not None and el.text else ""

        title = _strip_html(_txt("title"))
        content = _strip_html(_txt("content"))
        if is_comments:
            children.append({"data": {"body": content or title}})
            continue

        raw_id = _txt("id")                              # e.g. "t3_abc123"
        base_id = raw_id.split("_", 1)[1] if "_" in raw_id else raw_id
        link_el = entry.find(f"{ns}link")
        link = link_el.get("href") if link_el is not None else ""
        path = urllib.parse.urlparse(link).path if link else ""
        children.append({"data": {
            "id": base_id,
            "title": title,
            "permalink": path,          # callers prefix "https://reddit.com"
            "selftext": content,
            "score": 0,
            "distinguished": None,
        }})

    if is_comments:
        return [{}, {"data": {"children": children}}]
    return {"data": {"children": children}}


def _fetch_reddit_rss(url: str, timeout: int = 8):
    """Fetch a Reddit .rss feed (translated from the .json API URL) with 429 backoff."""
    rss_url = url.replace(".json", ".rss")
    for attempt in range(3):
        _throttle_rss()
        try:
            req = urllib.request.Request(rss_url, headers=_REDDIT_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                xml_text = resp.read().decode("utf-8", "replace")
            return _parse_reddit_rss(rss_url, xml_text)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(6.0 * (attempt + 1))         # 6s, then 12s
                continue
            print(f"[ChapterPipeline] Reddit RSS fetch failed ({rss_url}): {e}")
            return None
        except Exception as e:
            print(f"[ChapterPipeline] Reddit RSS fetch failed ({rss_url}): {e}")
            return None
    return None


def _fetch_json(url: str, headers: Optional[dict] = None, timeout: int = 8) -> Optional[dict]:
    """Generic JSON fetch (for wiki and other sources)."""
    try:
        req = urllib.request.Request(url, headers=headers or _WIKI_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        # Redact API keys (e.g. YouTube ?key=...) before the URL hits the logs
        safe_url = re.sub(r'([?&]key=)[^&]+', r'\1REDACTED', url)
        print(f"[ChapterPipeline] fetch failed ({safe_url}): {e}")
        return None


# ── Chapter number extraction ─────────────────────────────────────────────────

_CHAPTER_RE = re.compile(
    r'\bone\s*piece\s*[:\-–]?\s*(?:chapter|ch\.?)\s*#?(\d{3,4})\b'
    r'|(?:chapter|ch\.?)\s*#?(\d{3,4})\b',
    re.IGNORECASE,
)


def _extract_chapter(title: str) -> Optional[int]:
    m = _CHAPTER_RE.search(title)
    if m:
        return int(m.group(1) or m.group(2))
    return None


# ── Character index ───────────────────────────────────────────────────────────

def _char_index_from_db(db: Session) -> dict:
    """Build name_lower→canonical mapping from DB."""
    chars = db.query(models.Character.name, models.Character.aliases).all()
    index = {}
    for name, aliases in chars:
        index[name.lower()] = name
        # Also index the final word of multi-word names so a roster entry stored
        # with a title (e.g. "King Reuven", "Queen Candelle") is still recognized
        # when the wiki/comments use the bare name ("Reuven"). setdefault keeps
        # full names/aliases authoritative; >=4 chars avoids matching short tokens.
        parts = name.split()
        if len(parts) > 1 and len(parts[-1]) >= 4:
            index.setdefault(parts[-1].lower(), name)
        if aliases:
            for alias in (aliases if isinstance(aliases, list) else []):
                if alias and len(alias) >= 3:
                    index[alias.lower()] = name
    return index


def _extract_chars(text: str, char_index: dict) -> list:
    text_lower = text.lower()
    found = []
    for name_lower, canonical in char_index.items():
        if re.search(r'\b' + re.escape(name_lower) + r'\b', text_lower):
            if canonical not in found:
                found.append(canonical)
    return found


# ── Source: One Piece Fandom Wiki ─────────────────────────────────────────────

_WIKI_API = "https://onepiece.fandom.com/api.php"


_WIKI_STUB_RE   = re.compile(r"is set to be released", re.IGNORECASE)
_WIKI_TITLED_RE = re.compile(r"is titled", re.IGNORECASE)


def _wiki_chapter_released(chapter_num: int) -> bool:
    """True only when the chapter's wiki page describes a READABLE chapter.
    The wiki creates stub pages before release ("'''Chapter N''' is set to be
    released on <date>" with TBA sections) — those must never count, or the
    site advances to a chapter nobody can read yet."""
    url = (f"{_WIKI_API}?action=parse&page=Chapter_{chapter_num}"
           f"&prop=wikitext&format=json")
    data = _fetch_json(url)
    wikitext = (data or {}).get("parse", {}).get("wikitext", {}).get("*", "")
    if not wikitext:
        return False
    if _WIKI_STUB_RE.search(wikitext):
        return False
    # Released pages have a filled-in Chapter Box title and real summaries
    m = re.search(r"\|\s*title\s*=\s*([^\n]*)", wikitext)
    has_title = bool(m and m.group(1).strip())
    return has_title or bool(_WIKI_TITLED_RE.search(wikitext)) or len(wikitext) >= 4000


def _wiki_latest_chapter(db: Session) -> Optional[int]:
    """
    Check the One Piece fandom wiki for the highest RELEASED chapter.
    Walks forward from (last known chapter + 1) up to +10.
    Pre-release stub pages do not advance the number — only pages with real
    content (title + summaries) count. Returns the chapter number or None.
    """
    max_known = db.query(sqlfunc.max(models.Chapter.number)).scalar() or 1050

    latest = None
    for candidate in range(max_known + 1, max_known + 12):
        url = (f"{_WIKI_API}?action=query&titles=Chapter_{candidate}"
               f"&prop=info&format=json")
        data = _fetch_json(url)
        if not data:
            break
        pages = data.get("query", {}).get("pages", {})
        # MediaWiki returns page_id == -1 when the page doesn't exist
        if all(str(pid) == "-1" for pid in pages):
            break
        if _wiki_chapter_released(candidate):
            latest = candidate   # readable, keep going to find the highest
        else:
            print(f"[ChapterPipeline] Chapter_{candidate} page exists but is a "
                  f"pre-release stub — not counting as released")

    return latest


def _wiki_characters_section_names(wikitext: str) -> list:
    """Extract character names from a wiki chapter page's ==Characters== section.

    The wiki dropped the old {{Char Box|Name}} templates; the section is now a
    CharTable wikitable in which the actual characters are the '*[[Name]]'
    bulleted links. Group/organization sub-headers (';[[Five Elders]]') and
    table-header cells ('![[World Government]]') are NOT characters, so only
    lines starting with '*' are taken. Returns the wiki page-target names in
    order of appearance, de-duplicated case-insensitively."""
    m = re.search(r'==\s*Characters\s*==(.*?)(?:\n==[^=]|\Z)', wikitext, re.S | re.I)
    if not m:
        return []
    names, seen = [], set()
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s.startswith("*"):
            continue
        lm = re.search(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', s)
        if not lm:
            continue
        raw = lm.group(1).strip()
        key = raw.lower()
        if raw and key not in seen:
            seen.add(key)
            names.append(raw)
    return names


def _wiki_chapter_new_names(chapter_num: int, char_index: dict) -> list:
    """
    Return character names from the wiki chapter's ==Characters== section that
    don't match any known character. Used to propose new characters for review.
    """
    url = (f"{_WIKI_API}?action=parse&page=Chapter_{chapter_num}"
           f"&prop=wikitext&format=json")
    data = _fetch_json(url)
    if not data:
        return []
    wikitext = data.get("parse", {}).get("wikitext", {}).get("*", "")
    if not wikitext:
        return []

    unknown = []
    for raw in _wiki_characters_section_names(wikitext):
        if len(raw) < 3:
            continue
        # Skip if any known character matches this name (word-boundary aware, so
        # e.g. "Nerona Imu" → Imu and "Marcus Mars" → Mars are recognized as known)
        if not _extract_chars(raw, char_index) and raw not in unknown:
            unknown.append(raw)
    return unknown


def _wiki_chapter_chars(chapter_num: int, char_index: dict) -> dict:
    """
    Fetch the wiki page for a chapter and extract character appearances.
    Returns canonical_name → count dict.
    The wiki's "Characters in Order of Appearance" section is the gold standard.
    """
    url = (f"{_WIKI_API}?action=parse&page=Chapter_{chapter_num}"
           f"&prop=wikitext&format=json")
    data = _fetch_json(url)
    if not data:
        return {}

    wikitext = data.get("parse", {}).get("wikitext", {}).get("*", "")
    if not wikitext:
        return {}

    counts: dict = {}

    # Pattern 1 — ==Characters== section bullets — the authoritative appearance
    # list (replaces the wiki's removed {{Char Box}} templates)
    for raw in _wiki_characters_section_names(wikitext):
        for name in _extract_chars(raw, char_index):
            counts[name] = counts.get(name, 0) + 5   # high weight: authoritative list

    # Pattern 2 — [[Character Name]] wiki links (common in prose sections)
    for m in re.finditer(r'\[\[([A-Z][^\]|]{1,40})(?:\|[^\]]+)?\]\]', wikitext):
        raw = m.group(1).strip()
        for name in _extract_chars(raw, char_index):
            counts[name] = counts.get(name, 0) + 1

    return counts


def _wiki_chapter_summary(chapter_num: int) -> str:
    """Return the chapter's Long/Short Summary prose from the wiki, lightly
    de-marked-up. This is the grounding context for LLM sentiment scoring — the
    model judges stock polarity ONLY from these events. Returns '' if the page
    has no readable summary yet (pre-release stub), which makes the caller skip
    LLM sentiment and fall back to mention-volume ranking."""
    url = (f"{_WIKI_API}?action=parse&page=Chapter_{chapter_num}"
           f"&prop=wikitext&format=json")
    data = _fetch_json(url)
    wikitext = (data or {}).get("parse", {}).get("wikitext", {}).get("*", "")
    if not wikitext:
        return ""

    chunks = []
    for header in ("Long Summary", "Short Summary"):
        m = re.search(rf'==\s*{header}\s*==(.*?)(?:\n==[^=]|\Z)', wikitext, re.S | re.I)
        if m:
            chunks.append(m.group(1))
    if not chunks:
        return ""

    text = "\n".join(chunks)
    # Strip the common wiki markup so the model reads clean prose.
    text = re.sub(r'\[\[(?:[^\]|]+\|)?([^\]]+)\]\]', r'\1', text)   # [[A|B]] / [[A]] → label
    text = re.sub(r"'{2,}", "", text)                               # bold/italic quotes
    text = re.sub(r'\{\{[^}]*\}\}', '', text)                        # templates
    text = re.sub(r'<[^>]+>', '', text)                             # html/ref tags
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


# ── Source: Reddit ────────────────────────────────────────────────────────────

def _reddit_find_chapter(min_chapter: int) -> tuple[Optional[int], Optional[str], str, str, bool]:
    """
    Sweep Reddit for the latest chapter discussion post.
    Returns (chapter_num, post_id, title, url, is_mod_post) or (None, None, "", "", False).
    is_mod_post is True only for moderator-distinguished threads — the official
    discussion post that goes up when a chapter actually releases.
    """
    sources = [
        "https://www.reddit.com/r/OnePiece/new.json?limit=50",
        "https://www.reddit.com/r/OnePiece/hot.json?limit=25",
        "https://www.reddit.com/r/OnePiece/search.json?q=chapter+discussion&sort=new&t=month&limit=10&restrict_sr=1",
        "https://www.reddit.com/r/OnePiece/search.json?q=official+release&sort=new&t=month&limit=10&restrict_sr=1",
    ]

    all_posts = []
    seen_ids: set = set()
    any_ok = False

    for url in sources:
        feed = _fetch_reddit(url)
        if not feed:
            continue
        any_ok = True
        for child in feed.get("data", {}).get("children", []):
            p = child.get("data", {})
            pid = p.get("id", "")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            title = p.get("title", "").strip()
            ch = _extract_chapter(title)
            if ch and ch >= min_chapter:
                all_posts.append({
                    "id": pid,
                    "title": title,
                    "chapter": ch,
                    "score": p.get("score", 0),
                    "url": "https://reddit.com" + p.get("permalink", ""),
                    "distinguished": p.get("distinguished"),
                })

    if not any_ok:
        print("[ChapterPipeline] Reddit: all sources blocked/failed")
        return None, None, "", "", False

    if not all_posts:
        return None, None, "", "", False

    mod_posts = [p for p in all_posts if p["distinguished"] == "moderator"]
    pool = mod_posts if mod_posts else all_posts
    best = max(pool, key=lambda p: (p["chapter"], p["score"]))
    return best["chapter"], best["id"], best["title"], best["url"], bool(mod_posts)


def _reddit_comment_chars(post_id: str, char_index: dict) -> dict:
    """Scrape top 50 comments from a Reddit post for character mentions."""
    data = _fetch_reddit(
        f"https://www.reddit.com/r/OnePiece/comments/{post_id}.json?limit=50&sort=top"
    )
    if not data or not isinstance(data, list) or len(data) < 2:
        return {}
    counts: dict = {}
    for child in data[1].get("data", {}).get("children", [])[:50]:
        body = child.get("data", {}).get("body", "")
        if not body:
            continue
        for name in _extract_chars(body, char_index):
            counts[name] = counts.get(name, 0) + 1
    return counts


def _reddit_pulse_chars(chapter_num: int, char_index: dict) -> dict:
    """Sweep spoiler/leak subs for post-level character mention scores."""
    pulse_sources = [
        "https://www.reddit.com/r/OnePieceLeaks/hot.json?limit=25",
        "https://www.reddit.com/r/OnePieceSpoilers/hot.json?limit=25",
        f"https://www.reddit.com/r/OnePieceLeaks/search.json?q={chapter_num}&sort=new&t=week&limit=15&restrict_sr=1",
        f"https://www.reddit.com/r/OnePieceSpoilers/search.json?q={chapter_num}&sort=new&t=week&limit=15&restrict_sr=1",
        f"https://www.reddit.com/r/OnePiece/search.json?q=chapter+{chapter_num}&sort=top&t=week&limit=25&restrict_sr=1",
    ]
    scores: dict = {}
    for url in pulse_sources:
        feed = _fetch_reddit(url)
        if not feed:
            continue
        for child in feed.get("data", {}).get("children", []):
            p = child.get("data", {})
            text = p.get("title", "") + " " + (p.get("selftext", "") or "")[:3000]
            weight = max(1, p.get("score", 1))
            for name in _extract_chars(text, char_index):
                scores[name] = scores.get(name, 0) + weight
    return scores


# ── Weekly community buzz (spoilers + memes between chapters) ─────────────────
# Chapters get processed Thu-Sun, but the community is loudest Mon-Wed when the
# spoiler leaks drop ("investing in Luffy while he's low"). A scheduled sweep
# accumulates character mentions from spoiler/meme subreddit listings all week
# into a BotKV rolling counter, which then feeds the NEXT chapter's proposals
# as an extra signal — INTEL ONLY, prices never move mid-week (that arbitrage
# window is the players' game, not the pipeline's).

_BUZZ_KV_KEY          = "weekly_buzz"
_BUZZ_ALERT_KV_KEY    = "buzz_alert"
_BUZZ_ALERT_THRESHOLD = 4     # new posts mentioning a char in one sweep → Vegapunk chatter
_BUZZ_SEEN_CAP        = 600   # post-id dedup memory (a week of listings fits easily)

_BUZZ_SOURCES = [
    "https://www.reddit.com/r/OnePiece/new.json?limit=25",
    "https://www.reddit.com/r/OnePiece/hot.json?limit=25",
    "https://www.reddit.com/r/OnePieceSpoilers/new.json?limit=25",
    "https://www.reddit.com/r/OnePieceLeaks/hot.json?limit=25",
    "https://www.reddit.com/r/MemePiece/hot.json?limit=25",
    "https://www.reddit.com/r/Piratefolk/hot.json?limit=25",
]


def _load_weekly_buzz(db: Session) -> dict:
    """Read the rolling buzz store; reset it when the ISO week rolls over."""
    iso = datetime.now(timezone.utc).isocalendar()
    week = f"{iso[0]}-W{iso[1]:02d}"
    row = db.query(models.BotKV).filter(models.BotKV.key == _BUZZ_KV_KEY).first()
    data = {}
    if row and row.value:
        try:
            data = json.loads(row.value)
        except Exception:
            data = {}
    if data.get("week") != week:
        data = {"week": week, "counts": {}, "seen": []}
    data.setdefault("counts", {})
    data.setdefault("seen", [])
    return data


def weekly_buzz_counts(db: Session) -> dict:
    """Current accumulated buzz counts {char_name: distinct posts this week}.
    Used by detect_chapter_drop as a proposal signal. Empty dict when the
    sweep hasn't run (signal degrades gracefully like every other source)."""
    row = db.query(models.BotKV).filter(models.BotKV.key == _BUZZ_KV_KEY).first()
    if not row or not row.value:
        return {}
    try:
        return json.loads(row.value).get("counts", {}) or {}
    except Exception:
        return {}


def sweep_weekly_buzz(db: Session) -> dict:
    """One buzz sweep: scan spoiler/meme subreddit listings, count NEW posts
    (deduped by post id across sweeps) mentioning each roster character, and
    fold them into the weekly rolling counter. A big single-sweep spike also
    queues a `buzz_alert` for the Vegapunk bot to post market chatter about.

    Returns {"week", "new_posts", "top", "alert"} for logs/admin."""
    char_index = _char_index_from_db(db)
    data = _load_weekly_buzz(db)
    seen = set(data["seen"])
    counts = data["counts"]

    new_mentions: dict = {}
    new_posts = 0
    for url in _BUZZ_SOURCES:
        feed = _fetch_reddit(url)
        if not feed:
            continue
        for child in feed.get("data", {}).get("children", []):
            p = child.get("data", {})
            pid = p.get("id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            new_posts += 1
            text = p.get("title", "") + " " + (p.get("selftext", "") or "")[:3000]
            for name in set(_extract_chars(text, char_index)):
                counts[name] = counts.get(name, 0) + 1
                new_mentions[name] = new_mentions.get(name, 0) + 1

    data["counts"] = counts
    data["seen"] = list(seen)[-_BUZZ_SEEN_CAP:]
    _set_kv(db, _BUZZ_KV_KEY, json.dumps(data))

    # Chatter alert — one character spiking hard in a single sweep means the
    # community found something (leak panel, meme wave). Queue it for the bot.
    alert = None
    if new_mentions:
        top_name, top_new = max(new_mentions.items(), key=lambda kv: kv[1])
        if top_new >= _BUZZ_ALERT_THRESHOLD:
            alert = {
                "name": top_name,
                "new_posts": top_new,
                "week_total": counts.get(top_name, top_new),
                "week": data["week"],
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            _set_kv(db, _BUZZ_ALERT_KV_KEY, json.dumps(alert))

    top5 = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    print(f"[BuzzSweep] {data['week']}: +{new_posts} new posts | top: {top5} | alert: {alert and alert['name']}")
    return {"week": data["week"], "new_posts": new_posts, "top": top5, "alert": alert}


# ── Break week detection ──────────────────────────────────────────────────────

_BREAK_RE = re.compile(
    r'\b(?:no\s+chapter|break\s+week|on\s+break|hiatus|no\s+op\s+this\s+week|'
    r'chapter\s+break|one\s+piece\s+break|jump\s+break)\b',
    re.IGNORECASE,
)


def _detect_break_week(chapter_num: int) -> bool:
    """
    Scan r/OnePiece for break week announcements following chapter_num.
    Returns True if strong evidence of a break next week is found.
    Checks the chapter discussion thread and recent top posts.
    """
    sources = [
        f"https://www.reddit.com/r/OnePiece/search.json?q=break+week+{chapter_num}&sort=new&t=week&limit=10&restrict_sr=1",
        f"https://www.reddit.com/r/OnePiece/search.json?q=no+chapter+{chapter_num}&sort=new&t=week&limit=10&restrict_sr=1",
        "https://www.reddit.com/r/OnePiece/hot.json?limit=25",
    ]
    hits = 0
    for url in sources:
        feed = _fetch_reddit(url)
        if not feed:
            continue
        for child in feed.get("data", {}).get("children", []):
            p = child.get("data", {})
            text = p.get("title", "") + " " + (p.get("selftext", "") or "")[:500]
            if _BREAK_RE.search(text):
                # Require at least a mod post OR 2+ matching posts to confirm
                if p.get("distinguished") == "moderator":
                    print(f"[ChapterPipeline] Break week confirmed by mod post for Ch.{chapter_num}")
                    return True
                hits += 1
                if hits >= 2:
                    print(f"[ChapterPipeline] Break week detected ({hits} posts) for Ch.{chapter_num}")
                    return True
    return False


# ── Source: YouTube ───────────────────────────────────────────────────────────

_YT_API = "https://www.googleapis.com/youtube/v3"
_YT_MENTION_THRESHOLD = 3.0   # minimum weighted score to act on a character


def _youtube_comment_chars(chapter_num: int, char_index: dict) -> dict:
    """
    Fetch comments from YouTube reaction videos for the chapter.
    Requires YOUTUBE_API_KEY env var. Returns {} gracefully if unset or API fails.

    Quota cost per call: ~100 (search) + 1 (stats) + 5 (comment pages) ≈ 106 units.
    Free tier is 10,000 units/day so this is well within budget.

    Returns canonical_name → weighted_score dict.
    Scores are weighted by log10(view_count) so viral videos contribute more signal.
    """
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        print("[ChapterPipeline] YOUTUBE_API_KEY not set — skipping YouTube enrichment")
        return {}

    # Step 1: Search for reaction/review videos
    query = f"One Piece chapter {chapter_num} reaction"
    search_url = (
        f"{_YT_API}/search?part=snippet"
        f"&q={urllib.parse.quote(query)}"
        f"&type=video&order=relevance&maxResults=5"
        f"&key={api_key}"
    )
    search_data = _fetch_json(search_url)
    if not search_data:
        print(f"[ChapterPipeline] YouTube search failed for Ch.{chapter_num}")
        return {}

    video_ids = [
        item["id"]["videoId"]
        for item in search_data.get("items", [])
        if item.get("id", {}).get("videoId")
    ]
    if not video_ids:
        print(f"[ChapterPipeline] YouTube: no reaction videos found for Ch.{chapter_num}")
        return {}

    print(f"[ChapterPipeline] YouTube: found {len(video_ids)} videos for Ch.{chapter_num}")

    # Step 2: Fetch view counts for weighting
    stats_url = (
        f"{_YT_API}/videos?part=statistics"
        f"&id={','.join(video_ids)}"
        f"&key={api_key}"
    )
    stats_data = _fetch_json(stats_url)
    view_counts: dict = {}
    if stats_data:
        for item in stats_data.get("items", []):
            views = int(item.get("statistics", {}).get("viewCount", 1))
            view_counts[item["id"]] = views

    # Step 3: Fetch top comments per video, extract character mentions
    counts: dict = {}
    for vid_id in video_ids:
        comments_url = (
            f"{_YT_API}/commentThreads?part=snippet"
            f"&videoId={vid_id}&maxResults=100"
            f"&order=relevance&key={api_key}"
        )
        comments_data = _fetch_json(comments_url)
        if not comments_data:
            continue

        views = view_counts.get(vid_id, 10)
        weight = max(1.0, math.log10(max(views, 10)))

        for item in comments_data.get("items", []):
            text = (
                item.get("snippet", {})
                    .get("topLevelComment", {})
                    .get("snippet", {})
                    .get("textDisplay", "")
            )
            if not text:
                continue
            for name in _extract_chars(text, char_index):
                counts[name] = counts.get(name, 0) + weight

    above_threshold = {k: v for k, v in counts.items() if v >= _YT_MENTION_THRESHOLD}
    print(f"[ChapterPipeline] YouTube Ch.{chapter_num}: {len(above_threshold)} characters above threshold")
    return counts


def enrich_chapter_with_youtube(db: Session, chapter_num: int) -> dict:
    """
    Re-rank all pending proposals for chapter_num with YouTube signal added.

    Loads the stored signal_scores from each existing proposal, injects the
    YouTube score, then re-runs the same tier logic as detect_chapter_drop so
    every proposal's pct_change reflects the full combined signal (wiki + Reddit
    + YouTube + site trading). Also creates proposals for characters YouTube
    found that Reddit/wiki missed entirely.

    Safe to call multiple times — idempotent on the YouTube score itself.

    Returns {"chapter", "yt_chars_found", "proposals_updated", "proposals_added"}
    """
    char_index = _char_index_from_db(db)
    yt_chars = _youtube_comment_chars(chapter_num, char_index)

    if not yt_chars:
        return {
            "chapter": chapter_num,
            "yt_chars_found": 0,
            "proposals_updated": 0,
            "proposals_added": 0,
        }

    # ── Load all pending proposals and their stored signal scores ────────────
    existing_proposals = {
        p.character_name: p
        for p in db.query(models.ProposedPriceChange).filter(
            models.ProposedPriceChange.chapter_number == chapter_num,
            models.ProposedPriceChange.status == "pending",
        ).all()
    }

    # ── Build unified signal map: existing signals + YouTube ─────────────────
    # {char_name: {wiki, reddit_comments, reddit_pulse, youtube, site_net}}
    all_signals: dict = {}

    for char_name, prop in existing_proposals.items():
        scores = dict(prop.signal_scores) if prop.signal_scores else {}
        scores["youtube"] = max(scores.get("youtube", 0), yt_chars.get(char_name, 0))
        all_signals[char_name] = scores

    # Characters YouTube found that have no existing proposal
    yt_only_names = [n for n in yt_chars if n not in existing_proposals
                     and yt_chars[n] >= _YT_MENTION_THRESHOLD]
    for char_name in yt_only_names:
        all_signals[char_name] = {"wiki": 0, "reddit_comments": 0,
                                   "reddit_pulse": 0, "youtube": yt_chars[char_name],
                                   "site_net": 0, "weekly_buzz": 0}

    if not all_signals:
        return {
            "chapter": chapter_num,
            "yt_chars_found": 0,
            "proposals_updated": 0,
            "proposals_added": 0,
        }

    # ── Recompute combined totals and re-rank ─────────────────────────────────
    ranked = sorted(
        [
            {
                "name": name,
                "total": _combined_total(scores),
                "scores": scores,
            }
            for name, scores in all_signals.items()
        ],
        key=lambda x: x["total"],
        reverse=True,
    )

    # Load character rows for beri data
    all_names = [r["name"] for r in ranked]
    char_rows = {
        c.name: c
        for c in db.query(models.Character).filter(
            models.Character.name.in_(all_names)
        ).all()
    }

    updated = 0
    added = 0

    for rank, entry in enumerate(ranked):
        char_name = entry["name"]
        scores = entry["scores"]
        char = char_rows.get(char_name)
        if not char:
            continue

        current_beri = char.beri
        base_beri = char.base_beri or current_beri
        net_buy = scores.get("site_net", 0)

        # Tier assignment — the SAME shared logic as detect_chapter_drop,
        # including the persisted LLM sentiment override (a defeated character
        # must stay "down" through this re-rank, whatever YouTube volume says).
        direction, pct = _proposal_tier(rank, scores, current_beri, base_beri)

        if pct < _MIN_PCT and net_buy >= 0:
            continue

        proposed_beri = (
            current_beri * (1 + pct / 100) if direction == "up"
            else max(_BERI_FLOOR, current_beri * (1 - pct / 100))
        )

        reason = _build_reason(chapter_num, scores)

        if char_name in existing_proposals:
            prop = existing_proposals[char_name]
            prop.pct_change = round(pct, 2)
            prop.direction = direction
            prop.proposed_beri = proposed_beri
            prop.reason = reason
            prop.signal_scores = scores
            updated += 1
        else:
            db.add(models.ProposedPriceChange(
                chapter_number=chapter_num,
                character_id=char.id,
                character_name=char_name,
                current_beri=current_beri,
                proposed_beri=proposed_beri,
                direction=direction,
                pct_change=round(pct, 2),
                reason=reason,
                signal_scores=scores,
            ))
            added += 1

    db.commit()
    yt_chars_found = len([s for s in yt_chars.values() if s >= _YT_MENTION_THRESHOLD])
    print(
        f"[ChapterPipeline] YouTube enrichment Ch.{chapter_num}: "
        f"{yt_chars_found} chars found, {updated} proposals re-ranked, {added} added"
    )
    return {
        "chapter": chapter_num,
        "yt_chars_found": yt_chars_found,
        "proposals_updated": updated,
        "proposals_added": added,
    }


# ── Shared signal helpers ─────────────────────────────────────────────────────

def _combined_total(scores: dict) -> float:
    """Compute combined signal score from raw per-source counts."""
    return (
        scores.get("wiki", 0) * 100
        + scores.get("reddit_comments", 0) * 500
        + scores.get("reddit_pulse", 0)
        + scores.get("youtube", 0) * 150
        + scores.get("site_net", 0) * 100
        + scores.get("weekly_buzz", 0) * 50   # distinct spoiler/meme posts this week
    )


def _build_reason(chapter_num: int, scores: dict) -> str:
    """Build a human-readable reason string from signal scores."""
    parts = []
    if scores.get("wiki"):
        parts.append(f"wiki ×{int(scores['wiki'])}")
    if scores.get("reddit_comments"):
        parts.append(f"{int(scores['reddit_comments'])} Reddit comments")
    if scores.get("reddit_pulse"):
        parts.append(f"pulse {int(scores['reddit_pulse'])}")
    if scores.get("youtube"):
        parts.append(f"YouTube {scores['youtube']:.1f}pts")
    if scores.get("weekly_buzz"):
        parts.append(f"{int(scores['weekly_buzz'])} buzz posts this week")
    net = scores.get("site_net", 0)
    if net:
        parts.append(f"net {'buy' if net > 0 else 'sell'} {abs(int(net))}")
    reason = f"Ch.{chapter_num} — " + (", ".join(parts) if parts else "signal detected")
    # The LLM story verdict travels inside signal_scores so every path that
    # rebuilds the reason (detect, YouTube enrich) keeps the one-line why.
    verdict = scores.get("sentiment")
    if isinstance(verdict, dict) and verdict.get("why"):
        reason += f" · {verdict['why']}"
    return reason


def _sentiment_verdict(scores: dict) -> Optional[dict]:
    """Return the persisted LLM sentiment verdict from a signal_scores dict,
    normalized (magnitude clamped 1-5), or None."""
    v = scores.get("sentiment")
    if not isinstance(v, dict) or v.get("direction") not in ("up", "down", "neutral"):
        return None
    try:
        mag = int(v.get("magnitude", 1) or 1)
    except (TypeError, ValueError):
        mag = 1
    return {"direction": v["direction"], "magnitude": max(1, min(5, mag)),
            "why": (v.get("why") or "").strip()}


def _proposal_tier(rank: int, scores: dict, current_beri: float, base_beri: float) -> tuple:
    """Single source of truth for a proposal's (direction, pct).

    Order of precedence:
      1. LLM story verdict "down" — a defeated/humiliated character drops no
         matter how loud the mentions are (magnitude sets the size).
      2. Site sell pressure overrides.
      3. Rank-tier upward move; an LLM "up" verdict lifts the floor; mean
         reversion caps runaway prices.

    Used by BOTH detect_chapter_drop and enrich_chapter_with_youtube so a
    re-rank can never silently drop the sentiment override again.
    """
    net_buy = scores.get("site_net", 0)
    verdict = _sentiment_verdict(scores)

    if verdict and verdict["direction"] == "down":
        return "down", _SENTIMENT_DOWN_PCT[verdict["magnitude"]]
    if net_buy < -10:
        return "down", 4.0
    if net_buy < -5:
        return "down", 2.5

    pct = _RANK_PCT[min(rank, len(_RANK_PCT) - 1)]
    if verdict and verdict["direction"] == "up":
        pct = max(pct, _SENTIMENT_UP_PCT[verdict["magnitude"]])
    if base_beri > 0 and current_beri > base_beri * 3:
        pct = min(pct, 0.5)
    return "up", pct


# ── Pipeline constants ────────────────────────────────────────────────────────

_MENTION_FLOOR = 1
_MIN_PCT       = 1.0
_BERI_FLOOR    = 100_000
_RANK_PCT      = [7.0, 5.0, 3.5, 3.5, 2.0, 2.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0]
# LLM sentiment magnitude (1-5) → % move. Downs get real teeth so a decisive
# defeat (5) falls much harder than a character who merely took a hit (1-2).
_SENTIMENT_DOWN_PCT = {1: 1.5, 2: 2.5, 3: 4.0, 4: 6.0, 5: 8.0}
_SENTIMENT_UP_PCT   = {1: 1.5, 2: 2.5, 3: 4.0, 4: 6.0, 5: 8.0}


# ── Main pipeline ─────────────────────────────────────────────────────────────

def detect_chapter_drop(
    db: Session,
    force_chapter: Optional[int] = None,
    announce: bool = True,
) -> dict:
    """
    Multi-source chapter drop detection and price proposal generation.

    force_chapter: skip detection entirely and run for this chapter number.
    announce: when False (delayed re-scrape), skip the Discord announcement
              and transmission publish — they already fired on first detection.
    Returns {"detected": bool, "chapter": int|None, "proposals": int,
             "message": str, "sources": list}
    """

    sources_used: list = []
    best_post_id: Optional[str] = None
    best_title: str = ""
    best_url: str = ""

    # ── 1. Determine chapter number ───────────────────────────────────────────
    if force_chapter is not None:
        chapter_num = force_chapter
        sources_used.append("manual")
        print(f"[ChapterPipeline] Manual trigger for Ch.{chapter_num}")

    else:
        max_known = db.query(sqlfunc.max(models.Chapter.number)).scalar() or 1050
        chapter_num = None

        # Source A: One Piece Fandom Wiki (always works — no auth needed)
        wiki_ch = _wiki_latest_chapter(db)
        if wiki_ch and wiki_ch > max_known:
            chapter_num = wiki_ch
            sources_used.append("wiki")
            print(f"[ChapterPipeline] Wiki detected Ch.{chapter_num}")

        # Source B: Reddit (supplementary — adds post ID for comment scraping)
        reddit_ch, reddit_post_id, reddit_title, reddit_url, reddit_is_mod = _reddit_find_chapter(
            max_known + 1
        )
        if reddit_ch:
            sources_used.append("reddit")
            # Only a moderator-distinguished discussion thread is release
            # evidence — spoiler/leak post titles must not advance the chapter.
            if reddit_is_mod and (chapter_num is None or reddit_ch > chapter_num):
                chapter_num = reddit_ch
            # Always capture Reddit post metadata if available
            if reddit_post_id:
                best_post_id = reddit_post_id
                best_title = reddit_title
                best_url = reddit_url

        # Break-week gate: if the wiki did NOT confirm readable content and
        # Reddit chatter says this is a break week, hold the number back —
        # the uplink must always show the latest chapter people can read.
        if chapter_num is not None and "wiki" not in sources_used:
            try:
                if _detect_break_week(max_known):
                    print(f"[ChapterPipeline] Break week chatter detected — holding "
                          f"back Ch.{chapter_num} until the wiki confirms content")
                    chapter_num = None
            except Exception:
                pass

        if chapter_num is None:
            wiki_hint = "wiki returned no new chapter" if "wiki" not in sources_used else ""
            reddit_hint = "Reddit blocked (no creds)" if "reddit" not in sources_used else ""
            hints = ", ".join(h for h in [wiki_hint, reddit_hint] if h)
            return {
                "detected": False,
                "chapter": None,
                "proposals": 0,
                "sources": sources_used,
                "message": f"No new chapter found. {hints}".strip(". ") + ".",
            }

    # ── 2. Check if already processed ────────────────────────────────────────
    existing = db.query(models.Chapter).filter(
        models.Chapter.number == chapter_num
    ).first()
    if existing and existing.processed and force_chapter is None:
        return {
            "detected": False,
            "chapter": chapter_num,
            "proposals": 0,
            "sources": sources_used,
            "message": f"Ch.{chapter_num} already processed",
        }

    # ── 3. Create or update Chapter record ───────────────────────────────────
    # Always clear pending proposals from older chapters — they're stale
    db.query(models.ProposedPriceChange).filter(
        models.ProposedPriceChange.chapter_number < chapter_num,
        models.ProposedPriceChange.status == "pending",
    ).delete(synchronize_session=False)

    if not existing:
        chapter_row = models.Chapter(
            number=chapter_num,
            title=best_title or f"Chapter {chapter_num}",
            reddit_url=best_url or "",
        )
        db.add(chapter_row)
        db.flush()
    else:
        chapter_row = existing
        # Manual re-run: wipe this chapter's pending proposals for a clean slate
        if force_chapter is not None:
            db.query(models.ProposedPriceChange).filter(
                models.ProposedPriceChange.chapter_number == chapter_num,
                models.ProposedPriceChange.status == "pending",
            ).delete(synchronize_session=False)
            chapter_row.processed = False
            db.flush()

    # ── 4. Gather character mentions from all available sources ───────────────
    char_index = _char_index_from_db(db)

    # 4a. Wiki character appearances (authoritative, high weight per mention)
    wiki_chars: dict = {}
    if "wiki" in sources_used or force_chapter is not None:
        wiki_chars = _wiki_chapter_chars(chapter_num, char_index)
        if wiki_chars:
            sources_used.append("wiki-chars")
            print(f"[ChapterPipeline] Wiki chars found: {len(wiki_chars)}")

    # 4b. Reddit top-comment character mentions
    # For manual/force runs, try to find the Reddit post even if step 1 skipped it
    if not best_post_id and force_chapter is not None:
        _, reddit_post_id2, _, _, _ = _reddit_find_chapter(chapter_num)
        if reddit_post_id2:
            best_post_id = reddit_post_id2
            if "reddit" not in sources_used:
                sources_used.append("reddit")

    comment_chars: dict = {}
    if best_post_id:
        comment_chars = _reddit_comment_chars(best_post_id, char_index)
        if comment_chars:
            sources_used.append("reddit-comments")

    # 4c. Reddit pulse (spoiler/leak subreddits, post-level score weighting)
    # Always run for manual/force triggers so spoiler subs are swept
    pulse_chars: dict = {}
    if "reddit" in sources_used or best_post_id or force_chapter is not None:
        pulse_chars = _reddit_pulse_chars(chapter_num, char_index)
        if pulse_chars:
            sources_used.append("reddit-pulse")

    # ── 5. Site sell pressure (last 7 days) ──────────────────────────────────
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    txns = db.query(
        models.Transaction.character_id,
        models.Transaction.action,
        models.Transaction.quantity,
    ).filter(models.Transaction.timestamp >= cutoff).all()

    site_net_by_id: dict = {}
    for char_id, action, qty in txns:
        site_net_by_id.setdefault(char_id, 0)
        site_net_by_id[char_id] += qty if action == "buy" else -qty

    chars_by_id = {
        c.id: c.name
        for c in db.query(models.Character.id, models.Character.name).filter(
            models.Character.id.in_(list(site_net_by_id.keys()))
        ).all()
    }
    site_net: dict = {
        chars_by_id[cid]: net
        for cid, net in site_net_by_id.items()
        if cid in chars_by_id
    }

    # ── 5b. Weekly community buzz (accumulated spoiler/meme chatter) ──────────
    buzz_chars: dict = {}
    try:
        buzz_chars = weekly_buzz_counts(db)
        if buzz_chars:
            sources_used.append("weekly-buzz")
    except Exception as e:
        print(f"[ChapterPipeline] Weekly buzz load failed (non-fatal): {e}")

    # ── 6. Combine signals into ranked list ───────────────────────────────────
    # Weights applied in _combined_total():
    #   wiki × 100  |  reddit_comments × 500  |  reddit_pulse × 1  |
    #   youtube × 150 (added later by enrich pass)  |  site_net × 100  |
    #   weekly_buzz × 50 (accumulated Mon-Wed spoiler/meme chatter)
    all_names = set(wiki_chars) | set(comment_chars) | set(pulse_chars) | set(site_net) | set(buzz_chars)
    combined = []
    for name in all_names:
        scores = {
            "wiki":             wiki_chars.get(name, 0),
            "reddit_comments":  comment_chars.get(name, 0),
            "reddit_pulse":     pulse_chars.get(name, 0),
            "youtube":          0,   # filled by Sunday enrichment pass
            "site_net":         site_net.get(name, 0),
            "weekly_buzz":      buzz_chars.get(name, 0),
        }
        total = _combined_total(scores)
        if total < _MENTION_FLOOR and scores["site_net"] >= 0:
            continue
        combined.append({"name": name, "total": total, "scores": scores})

    # Always include heavy sell-pressure chars even if not mentioned
    for name, net in site_net.items():
        if net < -5 and not any(c["name"] == name for c in combined):
            scores = {"wiki": 0, "reddit_comments": 0, "reddit_pulse": 0,
                      "youtube": 0, "site_net": net, "weekly_buzz": 0}
            combined.append({"name": name, "total": net * 100, "scores": scores})

    combined.sort(key=lambda x: x["total"], reverse=True)
    top = combined[:15]

    # ── 7. Load character rows ────────────────────────────────────────────────
    char_rows = {
        c.name: c
        for c in db.query(models.Character).filter(
            models.Character.name.in_([c["name"] for c in top])
        ).all()
    }

    # ── 8. Generate price proposals (upsert — idempotent across re-runs) ──────
    # Load this chapter's existing pending proposals keyed by character so a
    # re-run UPDATES them in place instead of stacking duplicate rows. Anything
    # left over (a char that dropped out of `top` on this run) is stale → deleted.
    existing_pending = {
        p.character_id: p
        for p in db.query(models.ProposedPriceChange).filter(
            models.ProposedPriceChange.chapter_number == chapter_num,
            models.ProposedPriceChange.status == "pending",
        ).all()
    }

    # ── 8a. LLM story-sentiment polarity ─────────────────────────────────────
    # Mention volume alone can't tell "Imu is scary and everywhere" from "Imu
    # took a hit and is sweating" — both look like heavy signal. Ask Claude to
    # judge each candidate's stock direction + magnitude from the chapter's
    # actual events (wiki summary). Degrades to {} if the LLM is unavailable or
    # the summary is a pre-release stub, leaving the mention ranking untouched.
    sentiment: dict = {}
    try:
        summary = _wiki_chapter_summary(chapter_num)
        if summary:
            from app.vegapunk_llm import score_chapter_sentiment
            sentiment = score_chapter_sentiment(
                chapter_num, summary, [e["name"] for e in top]
            )
            if sentiment:
                downs = [n for n, v in sentiment.items() if v["direction"] == "down"]
                sources_used.append("llm-sentiment")
                print(f"[ChapterPipeline] LLM sentiment: {len(sentiment)} verdicts, "
                      f"{len(downs)} downs ({', '.join(downs[:6])})")
    except Exception as e:
        print(f"[ChapterPipeline] LLM sentiment failed (non-fatal): {e}")

    proposals_created = 0
    for rank, entry in enumerate(top):
        char = char_rows.get(entry["name"])
        if not char:
            continue

        scores = dict(entry["scores"])
        current_beri = char.beri
        base_beri = char.base_beri or current_beri

        # Persist the LLM verdict INSIDE signal_scores so later re-ranks
        # (Sunday YouTube enrichment) keep honoring the story beat instead of
        # silently reverting a defeated character to an upward mention-rank move.
        verdict = sentiment.get(entry["name"])
        if verdict:
            scores["sentiment"] = verdict

        direction, pct = _proposal_tier(rank, scores, current_beri, base_beri)

        if pct < _MIN_PCT:
            continue

        proposed_beri = (
            current_beri * (1 + pct / 100) if direction == "up"
            else max(_BERI_FLOOR, current_beri * (1 - pct / 100))
        )
        reason = _build_reason(chapter_num, scores)

        prop = existing_pending.pop(char.id, None)
        if prop:
            prop.current_beri = current_beri
            prop.proposed_beri = proposed_beri
            prop.direction = direction
            prop.pct_change = round(pct, 2)
            prop.reason = reason
            prop.signal_scores = scores
        else:
            db.add(models.ProposedPriceChange(
                chapter_number=chapter_num,
                character_id=char.id,
                character_name=entry["name"],
                current_beri=current_beri,
                proposed_beri=proposed_beri,
                direction=direction,
                pct_change=round(pct, 2),
                reason=reason,
                signal_scores=scores,
            ))
        proposals_created += 1

    # Remove pending proposals that no longer have a signal this run (stale).
    for leftover in existing_pending.values():
        db.delete(leftover)

    # ── 9. Mark chapter as processed + detect break week ─────────────────────
    chapter_row.processed = True
    try:
        # Never downgrade True→False: a manual mark or earlier detection wins.
        # Break announcements often land on Reddit hours after the chapter does.
        chapter_row.next_is_break = bool(chapter_row.next_is_break) or _detect_break_week(chapter_num)
        if chapter_row.next_is_break:
            print(f"[ChapterPipeline] Ch.{chapter_num}: break week next — predictions will be extended")
    except Exception as e:
        print(f"[ChapterPipeline] Break week detection failed (non-fatal): {e}")
    db.commit()

    # ── 10. New-character debuts from the wiki Characters section ──────────────
    # Computed once here; reused by both the chapter alert and the proposal step.
    new_names: list = []
    try:
        new_names = _wiki_chapter_new_names(chapter_num, char_index)
    except Exception as e:
        print(f"[ChapterPipeline] New-character wiki scan failed (non-fatal): {e}")

    # ── 10a. Chapter ALERT handshake → Vegapunk posts to #chapter-intel ───────
    # Wave 1 (first notice). The bot polls this BotKV key and posts a
    # preliminary breakdown. The matured synopsis follows on Saturday.
    top_chars = [c["name"] for c in combined[:10]]
    if announce:
        try:
            _set_kv(db, "chapter_alert", json.dumps({
                "chapter": chapter_num,
                "detected": top_chars[:8],
                "debuts": new_names[:8],
            }))
            print(f"[ChapterPipeline] Ch.{chapter_num} alert queued for Vegapunk (chapter-intel)")
        except Exception as e:
            print(f"[ChapterPipeline] Alert handshake failed (non-fatal): {e}")

    # ── 10b. Auto-publish transmission (on-site uplink) ──────────────────────
    if announce:
        _publish_chapter_transmission(db, chapter_num, top, best_url)

    # ── 10c. Propose new characters from the wiki Characters section, not in DB ─
    try:
        if new_names:
            # Dedup against ALL pending proposals, not just this chapter's — a
            # debut character mentioned across consecutive chapters must not be
            # proposed twice (e.g. Reuven for both Ch.1185 and Ch.1186).
            existing_proposals = {
                r[0].lower() for r in db.query(models.ProposedNewCharacter.name).filter(
                    models.ProposedNewCharacter.status == "pending",
                ).all()
            }
            for raw_name in new_names:
                if raw_name.lower() in existing_proposals:
                    continue
                existing_proposals.add(raw_name.lower())
                db.add(models.ProposedNewCharacter(
                    chapter_number=chapter_num,
                    name=raw_name,
                    proposed_beri=500_000_000,   # conservative default — admin sets real value
                    reason=f"Ch.{chapter_num} — wiki Characters-section debut, not in roster",
                ))
            db.commit()
            print(f"[ChapterPipeline] {len(new_names)} potential new character(s) proposed for Ch.{chapter_num}: {new_names}")
    except Exception as e:
        print(f"[ChapterPipeline] New character proposal failed (non-fatal): {e}")

    # ── 11. Auto-resolve predictions that targeted this chapter ─────────────
    try:
        from app.prediction_pipeline import auto_resolve_predictions
        resolve_result = auto_resolve_predictions(db, chapter_num, wiki_chars)
        if resolve_result["resolved"] or resolve_result["needs_review"]:
            print(
                f"[ChapterPipeline] Predictions for Ch.{chapter_num}: "
                f"{resolve_result['resolved']} resolved, "
                f"{resolve_result['needs_review']} flagged for review"
            )
    except Exception as e:
        print(f"[ChapterPipeline] Prediction auto-resolve failed (non-fatal): {e}")

    # Beri drop intentionally NOT fired here — it runs on the fixed weekly
    # cron (scheduler.py) so user income stays consistent regardless of
    # chapter timing, breaks, or detection hiccups.

    sources_str = ", ".join(sources_used) if sources_used else "manual"
    print(f"[ChapterPipeline] Ch.{chapter_num} — {proposals_created} proposals | sources: {sources_str}")
    return {
        "detected": True,
        "chapter": chapter_num,
        "proposals": proposals_created,
        "sources": sources_used,
        "message": f"Ch.{chapter_num}: {proposals_created} proposals generated (sources: {sources_str})",
    }


def _publish_chapter_transmission(db: Session, chapter_num: int, top: list, best_url: str):
    """Auto-publish the chapter transmission (Vegapunk voice summary + movers)."""
    try:
        raw_movers = [
            {"name": e["name"], "direction": "up" if e["scores"]["site_net"] >= 0 else "down"}
            for e in top[:10]
        ]

        # Build transmission_response() movers using real ProposedPriceChange data
        ppc_rows = db.query(models.ProposedPriceChange).filter(
            models.ProposedPriceChange.chapter_number == chapter_num,
        ).all()
        ppc_map = {r.character_name: r for r in ppc_rows}

        # Current beri lookup for movers
        mover_names = [m["name"] for m in raw_movers]
        beri_map = {}
        if mover_names:
            for c in db.query(models.Character.name, models.Character.beri).filter(
                models.Character.name.in_(mover_names)
            ).all():
                beri_map[c.name] = c.beri

        tr_movers = []
        for m in raw_movers:
            name = m["name"]
            ppc = ppc_map.get(name)
            if ppc:
                signed_pct = ppc.pct_change if ppc.direction == "up" else -ppc.pct_change
                beri = ppc.proposed_beri
            else:
                signed_pct = 5.0 if m["direction"] == "up" else -7.0
                beri = beri_map.get(name, 0)
            tr_movers.append({"name": name, "change_pct": signed_pct, "beri": beri})
        tr_movers.sort(key=lambda x: x["change_pct"], reverse=True)

        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
            from vegapunk.personality import transmission_response
            summary = transmission_response(tr_movers)
        except Exception as voice_err:
            print(f"[ChapterPipeline] Vegapunk voice failed, using plain summary: {voice_err}")
            up_names   = [m["name"] for m in raw_movers if m["direction"] == "up"][:3]
            down_names = [m["name"] for m in raw_movers if m["direction"] == "down"][:2]
            parts = []
            if up_names:
                parts.append(f"▲ {', '.join(up_names)}")
            if down_names:
                parts.append(f"▼ {', '.join(down_names)}")
            summary = f"Ch.{chapter_num} price proposals generated. " + ((" · ".join(parts)) if parts else "No strong movers this chapter.")

        tx = models.ChapterTransmission(
            chapter_number=chapter_num,
            uplink_label=f"Uplink: Ch.{chapter_num} ◈ Chapter Drop",
            summary=summary,
            movers=raw_movers,
            reddit_context=[best_url] if best_url else [],
        )
        db.add(tx)
        db.commit()
        print(f"[ChapterPipeline] Transmission auto-published for Ch.{chapter_num}")
    except Exception as e:
        print(f"[ChapterPipeline] Transmission publish failed (non-fatal): {e}")


def publish_chapter_synopsis(db: Session, chapter_num: int) -> bool:
    """Wave 2 (Saturday): rebuild the chapter transmission from the matured
    proposals, then signal the Vegapunk bot to post the synopsis to
    #announcements via the BotKV handshake. Returns True if a synopsis was
    queued. Idempotent at the bot layer (it dedups on last_synopsis_chapter)."""
    try:
        ppc_rows = db.query(models.ProposedPriceChange).filter(
            models.ProposedPriceChange.chapter_number == chapter_num
        ).all()
        # Reconstruct the `top` shape _publish_chapter_transmission expects from
        # the (now matured) proposals — direction drives the up/down split.
        top = [
            {"name": r.character_name,
             "scores": {"site_net": 1 if r.direction == "up" else -1}}
            for r in ppc_rows
        ]
        ch = db.query(models.Chapter).filter(models.Chapter.number == chapter_num).first()
        best_url = (ch.reddit_url if ch else "") or ""
        # Regenerate the transmission so /transmission serves the matured summary.
        _publish_chapter_transmission(db, chapter_num, top, best_url)
        _set_kv(db, "chapter_synopsis_ready", str(chapter_num))
        print(f"[ChapterPipeline] Ch.{chapter_num} synopsis queued for Vegapunk (announcements)")
        return True
    except Exception as e:
        print(f"[ChapterPipeline] Synopsis publish failed (non-fatal): {e}")
        return False
