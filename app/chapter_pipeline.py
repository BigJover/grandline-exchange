"""
Chapter drop detection + price proposal pipeline.

detect_chapter_drop(db)
    - Polls r/OnePiece/new for a mod-style chapter discussion post
    - If a new chapter is found (higher than any row in `chapters` table):
        * Creates a Chapter record
        * Scrapes top 50 comments for character mentions
        * Combines with chapter_pulse mention scores
        * Generates ProposedPriceChange rows for admin to review
    - Returns {"detected": True/False, "chapter": int|None, "proposals": int}
    - Safe to call repeatedly — idempotent on the same chapter number

Price proposal tiers (by Reddit rank):
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
import math
import base64
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app import models

# ── Reddit helpers ────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "GrandLineExchange:ChapterDetect:v1.0 (chapter drop detection for fan site)",
    "Accept": "application/json",
}

_REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_oauth_token: dict = {"token": None, "expires_at": 0.0}


def _get_oauth_token() -> Optional[str]:
    """Get a Reddit OAuth2 app-only access token using client credentials.
    Returns None if env vars are not set — callers fall back to unauthenticated."""
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
            "User-Agent": _HEADERS["User-Agent"],
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

_CHAPTER_RE = re.compile(
    # "One Piece Chapter 1183" / "One Piece: Chapter 1183" / "One Piece Ch. 1183"
    r'\bone\s*piece\s*[:\-–]?\s*(?:chapter|ch\.?)\s*#?(\d{3,4})\b'
    # "Chapter 1183 Discussion / Spoilers / Release / Thread"
    r'|(?:chapter|ch\.?)\s*#?(\d{3,4})\b',
    re.IGNORECASE,
)

_MENTION_FLOOR = 3      # minimum mention score to generate an upward proposal
_MIN_PCT       = 1.0    # minimum % change worth proposing
_BERI_FLOOR    = 100_000

# Rank → proposed % gain (index 0 = rank 1)
_RANK_PCT = [7.0, 5.0, 3.5, 3.5, 2.0, 2.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0]


def _fetch(url: str, timeout: int = 8) -> Optional[dict]:
    token = _get_oauth_token()
    if token:
        # Authenticated path — oauth.reddit.com bypasses datacenter IP blocks
        oauth_url = url.replace("https://www.reddit.com/", "https://oauth.reddit.com/")
        try:
            req = urllib.request.Request(
                oauth_url,
                headers={**_HEADERS, "Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print(f"[ChapterPipeline] OAuth fetch {oauth_url} failed: {e}")
            # Fall through to unauthenticated attempt

    # Unauthenticated fallback (works locally, may be blocked on Railway without creds)
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[ChapterPipeline] fetch {url} failed: {e}")
        return None


def _extract_chapter(title: str) -> Optional[int]:
    m = _CHAPTER_RE.search(title)
    if m:
        return int(m.group(1) or m.group(2))
    return None


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


# ── Main detection function ───────────────────────────────────────────────────

def detect_chapter_drop(db: Session, force_chapter: Optional[int] = None) -> dict:
    """
    Poll Reddit for a new chapter drop, or run the pipeline for a manually-specified chapter.

    force_chapter: skip Reddit polling entirely and run for this chapter number.
    Returns {"detected": bool, "chapter": int|None, "proposals": int, "message": str}
    """

    best_post_id: Optional[str] = None
    best_title: str = ""
    best_url: str = ""

    if force_chapter is not None:
        # ── Manual mode — no Reddit needed ───────────────────────────────────
        chapter_num = force_chapter
        print(f"[ChapterPipeline] Manual trigger for Ch.{chapter_num}")
    else:
        # ── 1. Poll multiple Reddit sources for chapter discussion posts ──────
        # /new + /hot catch posts from the last day or two.
        # The search fallback (t=month) reliably finds last week's discussion thread
        # even after it has dropped off /new and /hot.
        sources = [
            "https://www.reddit.com/r/OnePiece/new.json?limit=50",
            "https://www.reddit.com/r/OnePiece/hot.json?limit=25",
            "https://www.reddit.com/r/OnePiece/search.json?q=chapter+discussion&sort=new&t=month&limit=10&restrict_sr=1",
            "https://www.reddit.com/r/OnePiece/search.json?q=official+release&sort=new&t=month&limit=10&restrict_sr=1",
        ]

        all_posts = []
        seen_ids: set = set()
        any_fetch_ok = False

        for url in sources:
            feed = _fetch(url)
            if not feed:
                continue
            any_fetch_ok = True
            for child in feed.get("data", {}).get("children", []):
                p = child.get("data", {})
                pid = p.get("id", "")
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                title = p.get("title", "").strip()
                if not title:
                    continue
                ch = _extract_chapter(title)
                if ch:
                    all_posts.append({
                        "id": pid,
                        "title": title,
                        "chapter": ch,
                        "score": p.get("score", 0),
                        "url": "https://reddit.com" + p.get("permalink", ""),
                        "distinguished": p.get("distinguished"),
                        "num_comments": p.get("num_comments", 0),
                    })

        using_oauth = bool(_get_oauth_token())
        if not any_fetch_ok:
            hint = ("Set REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET env vars to enable OAuth, "
                    "or use the manual chapter input in the admin panel.") if not using_oauth \
                   else "OAuth token present but all requests failed — check credentials."
            return {
                "detected": False,
                "chapter": None,
                "proposals": 0,
                "message": f"Reddit fetch failed (all {len(sources)} sources errored). {hint}",
            }

        if not all_posts:
            return {"detected": False, "chapter": None, "proposals": 0, "message": "No chapter posts found on Reddit"}

        # ── 2. Find highest chapter number ────────────────────────────────────
        mod_posts = [p for p in all_posts if p["distinguished"] == "moderator"]
        candidate_pool = mod_posts if mod_posts else all_posts
        best = max(candidate_pool, key=lambda p: (p["chapter"], p["score"]))
        chapter_num = best["chapter"]
        best_post_id = best["id"]
        best_title = best["title"]
        best_url = best["url"]

    # ── 3. Check if already processed ─────────────────────────────────────────
    existing = db.query(models.Chapter).filter(
        models.Chapter.number == chapter_num
    ).first()
    if existing and existing.processed:
        return {
            "detected": False,
            "chapter": chapter_num,
            "proposals": 0,
            "message": f"Chapter {chapter_num} already processed",
        }

    # ── 4. Create or update Chapter record ────────────────────────────────────
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

    # ── 5. Scrape top comments for character mentions (Reddit optional) ────────
    char_index = _char_index_from_db(db)
    comment_mention_counts: dict = {}   # canonical_name → count

    if best_post_id:
        comments_data = _fetch(
            f"https://www.reddit.com/r/OnePiece/comments/{best_post_id}.json?limit=50&sort=top"
        )
        if comments_data and isinstance(comments_data, list) and len(comments_data) > 1:
            for child in comments_data[1].get("data", {}).get("children", [])[:50]:
                body = child.get("data", {}).get("body", "")
                if not body:
                    continue
                for name in _extract_chars(body, char_index):
                    comment_mention_counts[name] = comment_mention_counts.get(name, 0) + 1

    # ── 6. Pull Reddit pulse mention scores (post-level) ─────────────────────
    pulse_mention_scores: dict = {}   # canonical_name → weighted score
    pulse_sources = [
        f"https://www.reddit.com/r/OnePieceLeaks/hot.json?limit=25",
        f"https://www.reddit.com/r/OnePieceSpoilers/hot.json?limit=25",
        f"https://www.reddit.com/r/OnePiece/search.json?q=chapter+{chapter_num}&sort=top&t=week&limit=25&restrict_sr=1",
    ]
    for url in pulse_sources:
        feed = _fetch(url)
        if not feed:
            continue
        for child in feed.get("data", {}).get("children", []):
            p = child.get("data", {})
            title = p.get("title", "")
            selftext = (p.get("selftext", "") or "")[:500]
            for name in _extract_chars(title + " " + selftext, char_index):
                weight = max(1, p.get("score", 1))
                pulse_mention_scores[name] = pulse_mention_scores.get(name, 0) + weight

    # ── 7. Site sell pressure (last 7 days) ───────────────────────────────────
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    txns = db.query(
        models.Transaction.character_id,
        models.Transaction.action,
        models.Transaction.quantity,
    ).filter(models.Transaction.timestamp >= cutoff).all()

    site_net_by_id: dict = {}
    for char_id, action, qty in txns:
        if char_id not in site_net_by_id:
            site_net_by_id[char_id] = 0
        site_net_by_id[char_id] += qty if action == "buy" else -qty

    chars_by_id = {
        c.id: c.name
        for c in db.query(models.Character.id, models.Character.name).filter(
            models.Character.id.in_(list(site_net_by_id.keys()))
        ).all()
    }
    site_net: dict = {chars_by_id[cid]: net for cid, net in site_net_by_id.items() if cid in chars_by_id}

    # ── 8. Combine signals into ranked list ───────────────────────────────────
    # comment_mention_counts × 500 + pulse_mention_scores (raw) + site_net × 100
    all_names = set(comment_mention_counts) | set(pulse_mention_scores) | set(site_net)
    combined = []
    for name in all_names:
        c_score = comment_mention_counts.get(name, 0) * 500
        p_score = pulse_mention_scores.get(name, 0)
        n_score = site_net.get(name, 0) * 100
        total = c_score + p_score + n_score
        if total < _MENTION_FLOOR and site_net.get(name, 0) >= 0:
            continue   # too weak a signal for upward proposals
        combined.append({
            "name": name,
            "total": total,
            "comment_count": comment_mention_counts.get(name, 0),
            "pulse_score": pulse_mention_scores.get(name, 0),
            "net_buy": site_net.get(name, 0),
        })

    # Add sell-pressure chars even if not mentioned much
    for name, net in site_net.items():
        if net < -5 and not any(c["name"] == name for c in combined):
            combined.append({
                "name": name,
                "total": net * 100,
                "comment_count": 0,
                "pulse_score": 0,
                "net_buy": net,
            })

    combined.sort(key=lambda x: x["total"], reverse=True)
    top = combined[:15]

    # ── 9. Load character data for proposals ─────────────────────────────────
    names_in_top = [c["name"] for c in top]
    char_rows = {
        c.name: c
        for c in db.query(models.Character).filter(
            models.Character.name.in_(names_in_top)
        ).all()
    }

    # ── 10. Generate price proposals ─────────────────────────────────────────
    proposals_created = 0
    for rank, entry in enumerate(top):
        name = entry["name"]
        char = char_rows.get(name)
        if not char:
            continue

        current_beri = char.beri
        base_beri = char.base_beri or current_beri
        net_buy = entry["net_buy"]

        # Determine direction and %
        if net_buy < -10:
            direction = "down"
            pct = 4.0
        elif net_buy < -5:
            direction = "down"
            pct = 2.5
        else:
            direction = "up"
            pct = _RANK_PCT[min(rank, len(_RANK_PCT) - 1)]
            # Mean reversion cap
            if base_beri > 0 and current_beri > base_beri * 3:
                pct = min(pct, 0.5)

        if pct < _MIN_PCT:
            continue

        if direction == "up":
            proposed_beri = current_beri * (1 + pct / 100)
        else:
            proposed_beri = max(_BERI_FLOOR, current_beri * (1 - pct / 100))

        # Build human-readable reason
        parts = []
        if entry["comment_count"]:
            parts.append(f"{entry['comment_count']} comment mentions")
        if entry["pulse_score"]:
            parts.append(f"Reddit pulse score {int(entry['pulse_score'])}")
        if net_buy:
            parts.append(f"site net {'buy' if net_buy > 0 else 'sell'} {abs(net_buy)} shares")
        reason = f"Ch.{chapter_num} — " + (", ".join(parts) if parts else "signal detected")

        proposal = models.ProposedPriceChange(
            chapter_number=chapter_num,
            character_id=char.id,
            character_name=name,
            current_beri=current_beri,
            proposed_beri=proposed_beri,
            direction=direction,
            pct_change=round(pct, 2),
            reason=reason,
        )
        db.add(proposal)
        proposals_created += 1

    # ── 11. Mark chapter as processed ────────────────────────────────────────
    chapter_row.processed = True
    db.commit()

    print(f"[ChapterPipeline] Ch.{chapter_num} detected — {proposals_created} proposals created")
    return {
        "detected": True,
        "chapter": chapter_num,
        "proposals": proposals_created,
        "message": f"Ch.{chapter_num}: {proposals_created} price proposals generated",
    }
