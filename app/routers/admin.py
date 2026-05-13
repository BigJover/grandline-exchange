import os
import re as _re
import json as _json
import time as _time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app import models, schemas
from app.scheduler import run_beri_drop, WEEKLY_DROP
from app.bots import run_bot_tick
from app.routers.characters import invalidate_char_cache

router = APIRouter(prefix="/admin", tags=["admin"])


def _check_secret(x_admin_secret: Optional[str]):
    expected = os.getenv("ADMIN_SECRET", "").strip()
    if not expected or not x_admin_secret or x_admin_secret.strip() != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/rename-user")
def rename_user(old_username: str, new_username: str, x_admin_secret: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Rename a user. Protected by X-Admin-Secret header."""
    _check_secret(x_admin_secret)
    new_username = new_username.strip()
    if not new_username:
        raise HTTPException(status_code=400, detail="New username cannot be empty")
    user = db.query(models.User).filter(models.User.username == old_username).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{old_username}' not found")
    taken = db.query(models.User).filter(models.User.username == new_username).first()
    if taken:
        raise HTTPException(status_code=409, detail=f"Username '{new_username}' is already taken")
    user.username = new_username
    db.commit()
    return {"status": "ok", "old_username": old_username, "new_username": new_username}


@router.post("/set-beri")
def set_character_beri(name: str, beri: float, x_admin_secret: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Set a character's beri value by name. Protected by X-Admin-Secret header."""
    _check_secret(x_admin_secret)
    char = db.query(models.Character).filter(models.Character.name == name).first()
    if not char:
        raise HTTPException(status_code=404, detail=f"Character '{name}' not found")
    old = char.beri
    char.beri = beri
    db.commit()
    invalidate_char_cache()
    return {"status": "ok", "name": char.name, "old_beri": old, "new_beri": beri}


@router.post("/buster-call")
def trigger_buster_call(
    loss_pct: float = 0.20,
    marine_bonus: int = 100_000,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Fire a Buster Call. Protected by X-Admin-Secret header."""
    _check_secret(x_admin_secret)
    if not 0.01 <= loss_pct <= 0.80:
        raise HTTPException(status_code=400, detail="loss_pct must be between 0.01 and 0.80")

    now = datetime.now(timezone.utc)

    pirates = db.query(models.User).filter(
        models.User.is_bot == False,
        models.User.user_faction == "pirate",
    ).all()

    marines = db.query(models.User).filter(
        models.User.is_bot == False,
        models.User.user_faction == "marine",
    ).all()

    events = []
    hit_count = 0
    immune_count = 0

    for u in pirates:
        is_warlord = u.warlord_until and u.warlord_until > now
        if is_warlord:
            immune_count += 1
            continue
        loss = int(u.beri_balance * loss_pct)
        if loss > 0:
            u.beri_balance = max(0, u.beri_balance - loss)
            hit_count += 1
            events.append(models.BeriEvent(
                user_id=u.id,
                event_type="buster_call",
                amount=-loss,
                description=f"Buster Call \u2014 {loss:,}\u0e3f seized ({loss_pct*100:.0f}% of wallet)",
            ))

    for u in marines:
        u.beri_balance += marine_bonus
        events.append(models.BeriEvent(
            user_id=u.id,
            event_type="enforcement_bonus",
            amount=marine_bonus,
            description=f"Buster Call enforcement bonus \u2014 {marine_bonus:,}\u0e3f",
        ))

    db.bulk_save_objects(events)
    db.commit()

    return {
        "status": "ok",
        "pirates_hit": hit_count,
        "pirates_immune_warlord": immune_count,
        "marines_rewarded": len(marines),
        "loss_pct": loss_pct,
        "marine_bonus": marine_bonus,
    }


@router.post("/delete-user")
def delete_user(username: str, x_admin_secret: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Delete a user and all their data. Protected by X-Admin-Secret header."""
    _check_secret(x_admin_secret)
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    db.query(models.Share).filter(models.Share.user_id == user.id).delete()
    db.query(models.Transaction).filter(models.Transaction.user_id == user.id).delete()
    db.query(models.BeriEvent).filter(models.BeriEvent.user_id == user.id).delete()
    db.delete(user)
    db.commit()
    return {"status": "ok", "deleted": username}


@router.get("/users")
def list_users(x_admin_secret: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """List all non-bot users. Protected by X-Admin-Secret header."""
    _check_secret(x_admin_secret)
    users = db.query(models.User).filter(models.User.is_bot == False).order_by(models.User.created_at).all()
    return [{"id": u.id, "username": u.username, "email": u.email, "beri_balance": int(u.beri_balance), "created_at": str(u.created_at)} for u in users]


@router.post("/add-user-beri")
def add_user_beri(username: str, amount: int, x_admin_secret: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Add beri to a user's balance. Protected by X-Admin-Secret header."""
    _check_secret(x_admin_secret)
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    old = user.beri_balance
    user.beri_balance += amount
    db.add(models.BeriEvent(
        user_id=user.id,
        event_type="admin_grant",
        amount=amount,
        description=f"Admin grant \u2014 {amount:,}\u0e3f added",
    ))
    db.commit()
    return {"status": "ok", "username": username, "old_balance": old, "new_balance": user.beri_balance}


@router.post("/beri-drop")
def trigger_beri_drop(x_admin_secret: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Manually trigger a beri drop. Protected by X-Admin-Secret header."""
    _check_secret(x_admin_secret)
    run_beri_drop()
    count = db.query(models.User).filter(models.User.is_bot == False).count()
    return {
        "status": "ok",
        "beri_per_user": WEEKLY_DROP,
        "users_paid": count,
        "total_distributed": WEEKLY_DROP * count,
    }


@router.post("/bot-tick")
def trigger_bot_tick(x_admin_secret: Optional[str] = Header(None)):
    """Manually fire one round of bot market trades. Protected by X-Admin-Secret header."""
    _check_secret(x_admin_secret)
    run_bot_tick()
    return {"status": "ok", "message": "Bot tick fired"}


@router.post("/grant-admin")
def grant_admin(username: str, x_admin_secret: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Grant admin privileges to a user. Protected by X-Admin-Secret header."""
    _check_secret(x_admin_secret)
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    if user.is_bot:
        raise HTTPException(status_code=400, detail="Cannot grant admin to a bot")
    user.is_admin = True
    db.commit()
    return {"status": "ok", "username": username, "is_admin": True}


@router.post("/revoke-admin")
def revoke_admin(username: str, x_admin_secret: Optional[str] = Header(None), db: Session = Depends(get_db)):
    """Revoke admin privileges from a user. Protected by X-Admin-Secret header."""
    _check_secret(x_admin_secret)
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    user.is_admin = False
    db.commit()
    return {"status": "ok", "username": username, "is_admin": False}


@router.post("/transmission/publish")
def publish_transmission(
    payload: schemas.TransmissionPublish,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Publish a new chapter transmission. Replaces the live TRANSMISSION dropdown on all pages."""
    _check_secret(x_admin_secret)
    tx = models.ChapterTransmission(
        chapter_number=payload.chapter_number,
        uplink_label=payload.uplink_label,
        summary=payload.summary,
        movers=payload.movers,
        reddit_context=payload.reddit_context,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return {"status": "ok", "id": tx.id, "chapter_number": tx.chapter_number}


# ── Casino admin ──────────────────────────────────────────────────────────────

@router.post("/casino/create")
def casino_create(
    prop: schemas.PropositionCreate,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Create a new proposition. options must have at least 2 entries."""
    _check_secret(x_admin_secret)
    if len(prop.options) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 options")
    if not 0 < prop.house_cut < 1:
        raise HTTPException(status_code=400, detail="house_cut must be between 0 and 1")
    p = models.Proposition(
        question=prop.question,
        category=prop.category,
        options=prop.options,
        house_cut=prop.house_cut,
        closes_at=prop.closes_at,
        status="open",
        is_chapter_prediction=prop.is_chapter_prediction,
        chapter_drop_time=prop.chapter_drop_time,
        is_break_week=prop.is_break_week,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"status": "ok", "id": p.id, "question": p.question}


@router.post("/casino/close/{prop_id}")
def casino_close(
    prop_id: int,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Lock a proposition so no new bets can be placed (status → closed)."""
    _check_secret(x_admin_secret)
    prop = db.query(models.Proposition).filter(models.Proposition.id == prop_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Proposition not found")
    if prop.status != "open":
        raise HTTPException(status_code=400, detail=f"Proposition is already {prop.status}")
    prop.status = "closed"
    db.commit()
    return {"status": "ok", "prop_id": prop_id}


@router.post("/casino/resolve/{prop_id}")
def casino_resolve(
    prop_id: int,
    correct_option: int,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Resolve a proposition, pay out winners, log BeriEvents for all bettors."""
    _check_secret(x_admin_secret)
    prop = db.query(models.Proposition).filter(models.Proposition.id == prop_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Proposition not found")
    if prop.status == "resolved":
        raise HTTPException(status_code=400, detail="Already resolved")
    if correct_option < 0 or correct_option >= len(prop.options):
        raise HTTPException(status_code=400, detail="Invalid correct_option index")

    bets = db.query(models.PropositionBet).filter(
        models.PropositionBet.proposition_id == prop_id
    ).all()

    # For chapter predictions, use effective_amount = amount × multiplier for pool math
    is_prediction = bool(prop.is_chapter_prediction)

    def _effective_amount(b: models.PropositionBet) -> float:
        if is_prediction and not b.is_free_play:
            return b.amount * (b.multiplier or 1.0)
        return b.amount

    total_pool = sum(b.amount for b in bets if not b.is_free_play)
    winner_pool = sum(
        _effective_amount(b)
        for b in bets
        if b.option_index == correct_option
    )
    house_take = round(total_pool * prop.house_cut, 2)
    prize_pool = total_pool - house_take

    winners, losers, total_paid = 0, 0, 0.0

    for bet in bets:
        user = db.query(models.User).filter(models.User.id == bet.user_id).first()
        if not user:
            continue

        multiplier = float(bet.multiplier or 1.0)

        if bet.option_index == correct_option and winner_pool > 0:
            eff = _effective_amount(bet)
            base_payout = round((eff / winner_pool) * prize_pool, 2)
            # Free play winnings are capped at 2× the free play credit to limit house exposure
            if bet.is_free_play:
                base_payout = min(base_payout, bet.amount * 2)

            # Sale bonus: house refunds a portion of its cut to sale bettors
            sale_discount = float(getattr(bet, 'sale_discount', 0.0) or 0.0)
            sale_bonus = 0.0
            if sale_discount > 0 and not bet.is_free_play and winner_pool > 0:
                sale_bonus = round((eff / winner_pool) * house_take * sale_discount, 2)

            payout = base_payout + sale_bonus
            bet.payout = payout
            user.beri_balance += payout
            total_paid += payout

            extra = f" {multiplier}×" if multiplier > 1.0 and not bet.is_free_play else ""
            free_tag = " [free play]" if bet.is_free_play else ""
            sale_tag = f" [{int(sale_discount*100)}% sale]" if sale_bonus > 0 else ""
            db.add(models.BeriEvent(
                user_id=user.id,
                event_type="casino_win",
                amount=payout,
                description=(
                    f"Prediction win{free_tag}{sale_tag} — \"{prop.question}\" → "
                    f"\"{prop.options[correct_option]}\"{extra} "
                    f"({payout:,.0f}฿ on {bet.amount:,.0f}฿ bet)"
                ),
            ))
            winners += 1
        else:
            bet.payout = 0.0
            penalty = float(bet.penalty_amount or 0)
            if penalty > 0 and not bet.is_free_play:
                actual_penalty = min(penalty, user.beri_balance)
                user.beri_balance -= actual_penalty
                db.add(models.BeriEvent(
                    user_id=user.id,
                    event_type="casino_penalty",
                    amount=-actual_penalty,
                    description=(
                        f"Late prediction penalty — \"{prop.question}\" → "
                        f"\"{prop.options[correct_option]}\" "
                        f"({actual_penalty:,.0f}฿ penalty)"
                    ),
                ))

            if not bet.is_free_play:
                free_tag = ""
                db.add(models.BeriEvent(
                    user_id=user.id,
                    event_type="casino_loss",
                    amount=0,
                    description=(
                        f"Prediction loss — \"{prop.question}\" → "
                        f"\"{prop.options[correct_option]}\" "
                        f"({bet.amount:,.0f}฿ lost)"
                    ),
                ))
            losers += 1

    prop.correct_option = correct_option
    prop.status = "resolved"
    prop.resolved_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "status": "ok",
        "prop_id": prop_id,
        "correct_option": correct_option,
        "correct_label": prop.options[correct_option],
        "total_pool": total_pool,
        "house_take": house_take,
        "prize_pool": prize_pool,
        "winners": winners,
        "losers": losers,
        "total_paid_out": total_paid,
    }


@router.get("/casino/propositions")
def casino_list_all(
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """List all propositions with pool totals for admin review."""
    _check_secret(x_admin_secret)
    props = db.query(models.Proposition).order_by(models.Proposition.created_at.desc()).all()
    result = []
    for p in props:
        total = sum(b.amount for b in p.bets)
        per_option = {}
        for b in p.bets:
            per_option[p.options[b.option_index]] = per_option.get(p.options[b.option_index], 0) + b.amount
        result.append({
            "id": p.id,
            "question": p.question,
            "category": p.category,
            "status": p.status,
            "options": p.options,
            "closes_at": str(p.closes_at) if p.closes_at else None,
            "is_chapter_prediction": p.is_chapter_prediction,
            "total_pool": total,
            "pool_breakdown": per_option,
            "correct_option": p.options[p.correct_option] if p.correct_option is not None else None,
        })
    return result


# ── Reddit prediction suggestions ─────────────────────────────────────────────

REDDIT_SUBS = ["OnePiece", "OnePieceLeaks", "OnePieceSpoilers", "Piratefolk"]
REDDIT_QUERIES = ["prediction", "theory", "spoilers"]
REDDIT_KEYWORDS = ["predict", "theory", "theor", "will ", "chapter", "spoil", "who will", "what if", "oda"]
_reddit_cache: dict = {"data": [], "fetched_at": 0.0}
REDDIT_CACHE_TTL = 3600  # 1 hour


@router.get("/reddit-suggestions")
def reddit_suggestions(
    x_admin_secret: Optional[str] = Header(None),
    refresh: bool = False,
):
    """Fetch top prediction/theory posts from One Piece subreddits.
    Results are cached for 1 hour. Pass ?refresh=true to force a fresh fetch."""
    _check_secret(x_admin_secret)

    now = _time.time()
    if not refresh and now - _reddit_cache["fetched_at"] < REDDIT_CACHE_TTL and _reddit_cache["data"]:
        return {"cached": True, "results": _reddit_cache["data"]}

    combined_subs = "+".join(REDDIT_SUBS)
    seen_ids: set = set()
    results = []

    headers = {
        "User-Agent": "GrandLineExchange:PredictionReader:v1.0 (prediction aggregator for fan site)",
        "Accept": "application/json",
    }

    # 1. Search each query term across all subs combined
    for query in REDDIT_QUERIES:
        url = (
            "https://www.reddit.com/r/" + combined_subs +
            "/search.json?q=" + urllib.parse.quote(query) +
            "&sort=top&t=week&limit=20&restrict_sr=1"
        )
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read())
                for post in data.get("data", {}).get("children", []):
                    p = post.get("data", {})
                    pid = p.get("id", "")
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    title = p.get("title", "").strip()
                    title_lower = title.lower()
                    if not any(kw in title_lower for kw in REDDIT_KEYWORDS):
                        continue
                    # Skip pure meme/image posts with no text
                    if p.get("post_hint") in ("image", "rich:video", "video") and not p.get("selftext"):
                        continue
                    results.append({
                        "id": pid,
                        "title": title,
                        "url": "https://reddit.com" + p.get("permalink", ""),
                        "subreddit": p.get("subreddit", ""),
                        "score": p.get("score", 0),
                        "num_comments": p.get("num_comments", 0),
                        "selftext_preview": (p.get("selftext", "") or "")[:200],
                    })
        except Exception as e:
            print(f"[Reddit] query '{query}' failed: {e}")

    # 2. Also pull hot posts from OnePieceLeaks specifically — everything there is relevant
    leaks_url = "https://www.reddit.com/r/OnePieceLeaks/hot.json?limit=20"
    try:
        req = urllib.request.Request(leaks_url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
            for post in data.get("data", {}).get("children", []):
                p = post.get("data", {})
                pid = p.get("id", "")
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                title = p.get("title", "").strip()
                if not title:
                    continue
                results.append({
                    "id": pid,
                    "title": title,
                    "url": "https://reddit.com" + p.get("permalink", ""),
                    "subreddit": p.get("subreddit", "OnePieceLeaks"),
                    "score": p.get("score", 0),
                    "num_comments": p.get("num_comments", 0),
                    "selftext_preview": (p.get("selftext", "") or "")[:200],
                })
    except Exception as e:
        print(f"[Reddit] OnePieceLeaks hot fetch failed: {e}")

    # Sort by score descending, cap at 40
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:40]

    _reddit_cache["data"] = results
    _reddit_cache["fetched_at"] = now

    return {"cached": False, "results": results}


# ── Character name index (shared by pulse + discord-ingest) ───────────────────

_char_index_cache: dict = {"index": {}, "ids": {}, "prices": {}, "built_at": 0.0}
_CHAR_INDEX_TTL = 600  # rebuild every 10 minutes


def _load_char_index(db: Session) -> "tuple[dict, dict, dict]":
    """Return (name_lower→canonical, canonical→id, canonical→price). Rebuilt every 10min."""
    now = _time.time()
    if now - _char_index_cache["built_at"] < _CHAR_INDEX_TTL and _char_index_cache["index"]:
        return _char_index_cache["index"], _char_index_cache["ids"], _char_index_cache["prices"]

    chars = db.query(
        models.Character.id, models.Character.name,
        models.Character.aliases, models.Character.beri
    ).all()
    index: dict = {}
    ids: dict = {}
    prices: dict = {}
    for char_id, name, aliases, beri in chars:
        index[name.lower()] = name
        ids[name] = char_id
        prices[name] = beri
        if aliases:
            for alias in (aliases if isinstance(aliases, list) else []):
                if alias and len(alias) >= 3:
                    index[alias.lower()] = name
    _char_index_cache.update({"index": index, "ids": ids, "prices": prices, "built_at": now})
    return index, ids, prices


def _extract_chars(text: str, char_index: dict) -> list:
    """Return list of canonical character names mentioned in text (word-boundary match)."""
    text_lower = text.lower()
    found: list = []
    for name_lower, canonical in char_index.items():
        if _re.search(r'\b' + _re.escape(name_lower) + r'\b', text_lower):
            if canonical not in found:
                found.append(canonical)
    return found


# ── Chapter Pulse ─────────────────────────────────────────────────────────────

_pulse_cache: dict = {"data": {}, "fetched_at": 0.0}
PULSE_CACHE_TTL = 3600


@router.get("/chapter-pulse")
def chapter_pulse(
    x_admin_secret: Optional[str] = Header(None),
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    """Fetch latest chapter discussion from Reddit and extract character mentions.
    Pulls from OnePieceLeaks, OnePieceSpoilers, OnePiece, and Piratefolk hot feeds."""
    _check_secret(x_admin_secret)

    now = _time.time()
    if not refresh and now - _pulse_cache["fetched_at"] < PULSE_CACHE_TTL and _pulse_cache["data"]:
        return {**_pulse_cache["data"], "cached": True}

    char_index, char_ids, char_prices = _load_char_index(db)

    headers = {
        "User-Agent": "GrandLineExchange:ChapterPulse:v1.0 (chapter intelligence for fan site)",
        "Accept": "application/json",
    }

    posts = []
    seen_ids: set = set()

    sources = [
        "https://www.reddit.com/r/OnePieceLeaks/hot.json?limit=25",
        "https://www.reddit.com/r/OnePieceSpoilers/hot.json?limit=25",
        "https://www.reddit.com/r/OnePiece/search.json?q=chapter&sort=new&t=week&limit=25&restrict_sr=1",
        "https://www.reddit.com/r/OnePiece/hot.json?limit=20",
        "https://www.reddit.com/r/Piratefolk/hot.json?limit=15",
    ]

    for url in sources:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read())
                for post in data.get("data", {}).get("children", []):
                    p = post.get("data", {})
                    pid = p.get("id", "")
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    title = p.get("title", "").strip()
                    if not title:
                        continue
                    selftext = (p.get("selftext", "") or "")
                    full_text = title + " " + selftext[:500]
                    mentioned = _extract_chars(full_text, char_index)
                    posts.append({
                        "id": pid,
                        "title": title,
                        "url": "https://reddit.com" + p.get("permalink", ""),
                        "subreddit": p.get("subreddit", ""),
                        "score": p.get("score", 0),
                        "num_comments": p.get("num_comments", 0),
                        "selftext_preview": selftext[:200],
                        "characters": mentioned,
                    })
        except Exception as e:
            print(f"[ChapterPulse] {url} failed: {e}")

    # Aggregate character mention scores weighted by post score
    char_scores: dict = {}
    for post in posts:
        weight = max(1, post["score"])
        for char_name in post["characters"]:
            if char_name not in char_scores:
                char_scores[char_name] = {"score": 0.0, "post_count": 0}
            char_scores[char_name]["score"] += weight
            char_scores[char_name]["post_count"] += 1

    char_list = [
        {
            "name": name,
            "character_id": char_ids.get(name),
            "current_price": char_prices.get(name),
            "mention_score": round(d["score"]),
            "post_count": d["post_count"],
        }
        for name, d in char_scores.items()
    ]
    char_list.sort(key=lambda x: x["mention_score"], reverse=True)

    result = {
        "posts": sorted(posts, key=lambda x: x["score"], reverse=True)[:40],
        "characters": char_list[:30],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    _pulse_cache["data"] = result
    _pulse_cache["fetched_at"] = now
    return {**result, "cached": False}


# ── Community Signal ──────────────────────────────────────────────────────────

@router.get("/community-signal")
def community_signal(
    x_admin_secret: Optional[str] = Header(None),
    days: int = 7,
    db: Session = Depends(get_db),
):
    """Return per-character buy/sell pressure from recent site trading activity."""
    _check_secret(x_admin_secret)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    txns = db.query(
        models.Transaction.character_id,
        models.Transaction.action,
        models.Transaction.quantity,
    ).filter(models.Transaction.timestamp >= cutoff).all()

    signal: dict = {}
    for char_id, action, qty in txns:
        if char_id not in signal:
            signal[char_id] = {"buys": 0, "sells": 0, "buy_qty": 0, "sell_qty": 0}
        if action == "buy":
            signal[char_id]["buys"] += 1
            signal[char_id]["buy_qty"] += qty
        elif action == "sell":
            signal[char_id]["sells"] += 1
            signal[char_id]["sell_qty"] += qty

    if not signal:
        return {"days": days, "characters": []}

    chars = db.query(
        models.Character.id, models.Character.name, models.Character.beri
    ).filter(models.Character.id.in_(list(signal.keys()))).all()

    result = []
    for char_id, name, beri in chars:
        s = signal[char_id]
        net = s["buy_qty"] - s["sell_qty"]
        result.append({
            "character_id": char_id,
            "name": name,
            "current_price": beri,
            "buys": s["buys"],
            "sells": s["sells"],
            "buy_qty": s["buy_qty"],
            "sell_qty": s["sell_qty"],
            "net_qty": net,
        })

    result.sort(key=lambda x: abs(x["net_qty"]), reverse=True)
    return {"days": days, "characters": result[:30]}


# ── Discord bridge ────────────────────────────────────────────────────────────

@router.post("/discord-ingest")
def discord_ingest(
    event: schemas.DiscordEventIn,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Ingest a Discord message for character intelligence. Called by the Discord bot."""
    _check_secret(x_admin_secret)

    char_index, _, _ = _load_char_index(db)
    mentioned = _extract_chars(event.content, char_index)

    chapter_num = None
    ch_match = _re.search(r'chapter\s*#?(\d{3,4})', event.content.lower())
    if ch_match:
        chapter_num = int(ch_match.group(1))

    db.add(models.DiscordEvent(
        channel=event.channel or "",
        author=event.author or "",
        content=event.content,
        characters_detected=mentioned,
        chapter_num=chapter_num,
        source_url=event.source_url or "",
    ))
    db.commit()
    return {"stored": True, "characters_detected": mentioned, "chapter_num": chapter_num}


@router.get("/discord-events")
def discord_events_list(
    x_admin_secret: Optional[str] = Header(None),
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Return recent Discord-ingested events for the Chapter Intelligence panel."""
    _check_secret(x_admin_secret)
    events = db.query(models.DiscordEvent).order_by(
        models.DiscordEvent.created_at.desc()
    ).limit(min(limit, 100)).all()
    return [
        {
            "id": e.id,
            "channel": e.channel,
            "author": e.author,
            "content": (e.content or "")[:500],
            "characters_detected": e.characters_detected or [],
            "chapter_num": e.chapter_num,
            "source_url": e.source_url,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
