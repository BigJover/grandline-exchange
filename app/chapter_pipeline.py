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

import re
import json
import time
import math
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app import models

# ── Reddit helpers ────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "GrandLineExchange:ChapterDetect:v1.0 (chapter drop detection for fan site)",
    "Accept": "application/json",
}

_CHAPTER_RE = re.compile(
    r'\bone\s*piece\s*(?:chapter|ch\.?)\s*#?(\d{3,4})\b'
    r'|chapter\s*#?(\d{3,4})\s*(?:discussion|spoiler|release|thread|official|raw)',
    re.IGNORECASE,
)

_MENTION_FLOOR = 3      # minimum mention score to generate an upward proposal
_MIN_PCT       = 1.0    # minimum % change worth proposing
_BERI_FLOOR    = 100_000

# Rank → proposed % gain (index 0 = rank 1)
_RANK_PCT = [7.0, 5.0, 3.5, 3.5, 2.0, 2.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0]


def _fetch(url: str, timeout: int = 8) -> Optional[dict]:
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

def detect_chapter_drop(db: Session) -> dict:
    """
    Poll Reddit for a new chapter drop.
    Returns {"detected": bool, "chapter": int|None, "proposals": int, "message": str}
    """

    # ── 1. Poll r/OnePiece/new for chapter discussion posts ───────────────────
    data = _fetch("https://www.reddit.com/r/OnePiece/new.json?limit=50")
    if not data:
        return {"detected": False, "chapter": None, "proposals": 0, "message": "Reddit fetch failed"}

    # Also check hot — chapter discussion posts sometimes surface there
    hot_data = _fetch("https://www.reddit.com/r/OnePiece/hot.json?limit=25")

    all_posts = []
    for feed in [data, hot_data]:
        if not feed:
            continue
        for child in feed.get("data", {}).get("children", []):
            p = child.get("data", {})
            title = p.get("title", "").strip()
            if not title:
                continue
            ch = _extract_chapter(title)
            if ch:
                all_posts.append({
                    "id": p.get("id", ""),
                    "title": title,
                    "chapter": ch,
                    "score": p.get("score", 0),
                    "url": "https://reddit.com" + p.get("permalink", ""),
                    "distinguished": p.get("distinguished"),  # "moderator" for mod posts
                    "num_comments": p.get("num_comments", 0),
                })

    if not all_posts:
        return {"detected": False, "chapter": None, "proposals": 0, "message": "No chapter posts found on Reddit"}

    # ── 2. Find highest chapter number ────────────────────────────────────────
    # Prefer mod posts; fall back to highest-score post
    mod_posts = [p for p in all_posts if p["distinguished"] == "moderator"]
    candidate_pool = mod_posts if mod_posts else all_posts
    best = max(candidate_pool, key=lambda p: (p["chapter"], p["score"]))
    chapter_num = best["chapter"]

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
            title=best["title"],
            reddit_url=best["url"],
        )
        db.add(chapter_row)
        db.flush()
    else:
        chapter_row = existing

    # ── 5. Scrape top comments for character mentions ─────────────────────────
    char_index = _char_index_from_db(db)
    comment_mention_counts: dict = {}   # canonical_name → count

    if best["id"]:
        comments_data = _fetch(
            f"https://www.reddit.com/r/OnePiece/comments/{best['id']}.json?limit=50&sort=top"
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
