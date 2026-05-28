"""
Chapter drop detection + price proposal pipeline.

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
import json
import time
import base64
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app import models
from app.discord_notify import announce_chapter_drop

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


def _fetch_reddit(url: str, timeout: int = 8) -> Optional[dict]:
    """Fetch a Reddit JSON endpoint. Uses OAuth when creds are set, falls back to public."""
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

    try:
        req = urllib.request.Request(url, headers=_REDDIT_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[ChapterPipeline] Reddit fetch failed ({url}): {e}")
        return None


def _fetch_json(url: str, headers: Optional[dict] = None, timeout: int = 8) -> Optional[dict]:
    """Generic JSON fetch (for wiki and other sources)."""
    try:
        req = urllib.request.Request(url, headers=headers or _WIKI_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[ChapterPipeline] fetch failed ({url}): {e}")
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


def _wiki_latest_chapter(db: Session) -> Optional[int]:
    """
    Check the One Piece fandom wiki for the highest chapter that has a page.
    Walks forward from (last known chapter + 1) up to +10.
    Returns the chapter number, or None if nothing new is found.
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
        latest = candidate   # page exists, keep going to find the highest

    return latest


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

    # Pattern 1 — {{Char Box|Name|...}} — explicit character appearance template
    for m in re.finditer(r'\{\{Char Box\|([^|}\n]{2,50})', wikitext, re.IGNORECASE):
        raw = m.group(1).strip()
        for name in _extract_chars(raw, char_index):
            counts[name] = counts.get(name, 0) + 5   # high weight: authoritative list

    # Pattern 2 — [[Character Name]] wiki links (common in prose sections)
    for m in re.finditer(r'\[\[([A-Z][^\]|]{1,40})(?:\|[^\]]+)?\]\]', wikitext):
        raw = m.group(1).strip()
        for name in _extract_chars(raw, char_index):
            counts[name] = counts.get(name, 0) + 1

    return counts


# ── Source: Reddit ────────────────────────────────────────────────────────────

def _reddit_find_chapter(min_chapter: int) -> tuple[Optional[int], Optional[str], str, str]:
    """
    Sweep Reddit for the latest chapter discussion post.
    Returns (chapter_num, post_id, title, url) or (None, None, "", "").
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
        return None, None, "", ""

    if not all_posts:
        return None, None, "", ""

    mod_posts = [p for p in all_posts if p["distinguished"] == "moderator"]
    pool = mod_posts if mod_posts else all_posts
    best = max(pool, key=lambda p: (p["chapter"], p["score"]))
    return best["chapter"], best["id"], best["title"], best["url"]


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
        f"https://www.reddit.com/r/OnePiece/search.json?q=chapter+{chapter_num}&sort=top&t=week&limit=25&restrict_sr=1",
    ]
    scores: dict = {}
    for url in pulse_sources:
        feed = _fetch_reddit(url)
        if not feed:
            continue
        for child in feed.get("data", {}).get("children", []):
            p = child.get("data", {})
            text = p.get("title", "") + " " + (p.get("selftext", "") or "")[:500]
            weight = max(1, p.get("score", 1))
            for name in _extract_chars(text, char_index):
                scores[name] = scores.get(name, 0) + weight
    return scores


# ── Pipeline constants ────────────────────────────────────────────────────────

_MENTION_FLOOR = 3
_MIN_PCT       = 1.0
_BERI_FLOOR    = 100_000
_RANK_PCT      = [7.0, 5.0, 3.5, 3.5, 2.0, 2.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0]


# ── Main pipeline ─────────────────────────────────────────────────────────────

def detect_chapter_drop(db: Session, force_chapter: Optional[int] = None) -> dict:
    """
    Multi-source chapter drop detection and price proposal generation.

    force_chapter: skip detection entirely and run for this chapter number.
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
        reddit_ch, reddit_post_id, reddit_title, reddit_url = _reddit_find_chapter(
            max_known + 1
        )
        if reddit_ch:
            sources_used.append("reddit")
            if chapter_num is None or reddit_ch > chapter_num:
                chapter_num = reddit_ch
            # Always capture Reddit post metadata if available
            if reddit_post_id:
                best_post_id = reddit_post_id
                best_title = reddit_title
                best_url = reddit_url

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
    comment_chars: dict = {}
    if best_post_id:
        comment_chars = _reddit_comment_chars(best_post_id, char_index)
        if comment_chars:
            sources_used.append("reddit-comments")

    # 4c. Reddit pulse (spoiler/leak subreddits, post-level score weighting)
    pulse_chars: dict = {}
    if "reddit" in sources_used or best_post_id:
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

    # ── 6. Combine signals into ranked list ───────────────────────────────────
    # wiki_chars (already ×5 per occurrence in _wiki_chapter_chars) × 100
    # comment_chars × 500
    # pulse_chars (raw weighted by post score)
    # site_net × 100
    all_names = set(wiki_chars) | set(comment_chars) | set(pulse_chars) | set(site_net)
    combined = []
    for name in all_names:
        w_score = wiki_chars.get(name, 0) * 100
        c_score = comment_chars.get(name, 0) * 500
        p_score = pulse_chars.get(name, 0)
        n_score = site_net.get(name, 0) * 100
        total = w_score + c_score + p_score + n_score
        if total < _MENTION_FLOOR and site_net.get(name, 0) >= 0:
            continue
        combined.append({
            "name": name,
            "total": total,
            "wiki_count": wiki_chars.get(name, 0),
            "comment_count": comment_chars.get(name, 0),
            "pulse_score": pulse_chars.get(name, 0),
            "net_buy": site_net.get(name, 0),
        })

    # Always include heavy sell-pressure chars even if not mentioned
    for name, net in site_net.items():
        if net < -5 and not any(c["name"] == name for c in combined):
            combined.append({
                "name": name, "total": net * 100,
                "wiki_count": 0, "comment_count": 0,
                "pulse_score": 0, "net_buy": net,
            })

    combined.sort(key=lambda x: x["total"], reverse=True)
    top = combined[:15]

    # ── 7. Load character rows ────────────────────────────────────────────────
    char_rows = {
        c.name: c
        for c in db.query(models.Character).filter(
            models.Character.name.in_([c["name"] for c in top])
        ).all()
    }

    # ── 8. Generate price proposals ──────────────────────────────────────────
    proposals_created = 0
    for rank, entry in enumerate(top):
        char = char_rows.get(entry["name"])
        if not char:
            continue

        current_beri = char.beri
        base_beri = char.base_beri or current_beri
        net_buy = entry["net_buy"]

        if net_buy < -10:
            direction, pct = "down", 4.0
        elif net_buy < -5:
            direction, pct = "down", 2.5
        else:
            direction = "up"
            pct = _RANK_PCT[min(rank, len(_RANK_PCT) - 1)]
            if base_beri > 0 and current_beri > base_beri * 3:
                pct = min(pct, 0.5)

        if pct < _MIN_PCT:
            continue

        proposed_beri = (
            current_beri * (1 + pct / 100) if direction == "up"
            else max(_BERI_FLOOR, current_beri * (1 - pct / 100))
        )

        parts = []
        if entry["wiki_count"]:
            parts.append(f"wiki appearances ×{entry['wiki_count']}")
        if entry["comment_count"]:
            parts.append(f"{entry['comment_count']} Reddit comment mentions")
        if entry["pulse_score"]:
            parts.append(f"pulse score {int(entry['pulse_score'])}")
        if net_buy:
            parts.append(f"site net {'buy' if net_buy > 0 else 'sell'} {abs(net_buy)} shares")

        reason = f"Ch.{chapter_num} — " + (", ".join(parts) if parts else "signal detected")

        db.add(models.ProposedPriceChange(
            chapter_number=chapter_num,
            character_id=char.id,
            character_name=entry["name"],
            current_beri=current_beri,
            proposed_beri=proposed_beri,
            direction=direction,
            pct_change=round(pct, 2),
            reason=reason,
        ))
        proposals_created += 1

    # ── 9. Mark chapter as processed ─────────────────────────────────────────
    chapter_row.processed = True
    db.commit()

    # ── 10. Discord announcement ──────────────────────────────────────────────
    top_chars = [c["name"] for c in combined[:10]]
    try:
        announce_chapter_drop(chapter_num, top_chars, proposals_created)
    except Exception as e:
        print(f"[ChapterPipeline] Discord notify failed (non-fatal): {e}")

    # ── 10b. Auto-publish transmission ───────────────────────────────────────
    try:
        movers = [
            {"name": e["name"], "direction": "up" if e["net_buy"] >= 0 else "down"}
            for e in top[:10]
        ]
        up_names   = [m["name"] for m in movers if m["direction"] == "up"][:3]
        down_names = [m["name"] for m in movers if m["direction"] == "down"][:2]
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
            movers=movers,
            reddit_context=[best_url] if best_url else [],
        )
        db.add(tx)
        db.commit()
        print(f"[ChapterPipeline] Transmission auto-published for Ch.{chapter_num}")
    except Exception as e:
        print(f"[ChapterPipeline] Transmission publish failed (non-fatal): {e}")

    # ── 11. Beri drop — fires on chapter release instead of fixed weekly cron ──
    try:
        from app.scheduler import run_beri_drop
        run_beri_drop()
        print(f"[ChapterPipeline] Beri drop fired for Ch.{chapter_num}")
    except Exception as e:
        print(f"[ChapterPipeline] Beri drop failed (non-fatal): {e}")

    sources_str = ", ".join(sources_used) if sources_used else "manual"
    print(f"[ChapterPipeline] Ch.{chapter_num} — {proposals_created} proposals | sources: {sources_str}")
    return {
        "detected": True,
        "chapter": chapter_num,
        "proposals": proposals_created,
        "sources": sources_used,
        "message": f"Ch.{chapter_num}: {proposals_created} proposals generated (sources: {sources_str})",
    }
