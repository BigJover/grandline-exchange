import os
import re as _re
import json as _json
import time as _time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header, Body
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app import models, schemas
from app.scheduler import run_beri_drop, WEEKLY_DROP
from app.bots import run_bot_tick
from app.routers.characters import invalidate_char_cache
from app.websocket_manager import manager
from app.discord_notify import announce_price_alert, announce_chapter_price_analysis

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


@router.post("/bot-reseed")
def trigger_bot_reseed(x_admin_secret: Optional[str] = Header(None)):
    """Wipe all bot share holdings and rebuild from tier targets. Use when bots need a fresh start."""
    _check_secret(x_admin_secret)
    from app.bots import reseed_bots
    reseed_bots()
    return {"status": "ok", "message": "Bot positions reseeded"}


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


@router.get("/transmission/generate")
def generate_transmission(
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Auto-generate a ready-to-review transmission draft from Reddit pulse + Discord events + site signal.
    Returns a pre-filled payload that the admin reviews then publishes."""
    _check_secret(x_admin_secret)

    # ── 1. Reddit pulse (use cache if fresh, else fetch) ────────────────────
    now = _time.time()
    if now - _pulse_cache["fetched_at"] < PULSE_CACHE_TTL and _pulse_cache["data"]:
        pulse = _pulse_cache["data"]
    else:
        # Inline fetch (mirrors chapter_pulse logic)
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
                print(f"[GenerateTransmission] Reddit {url} failed: {e}")

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
        pulse = {
            "posts": sorted(posts, key=lambda x: x["score"], reverse=True)[:40],
            "characters": char_list[:30],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        _pulse_cache["data"] = pulse
        _pulse_cache["fetched_at"] = now

    # ── 2. Discord events (last 14 days) ────────────────────────────────────
    discord_cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    discord_events = db.query(models.DiscordEvent).filter(
        models.DiscordEvent.created_at >= discord_cutoff
    ).all()

    discord_char_counts: dict = {}   # name → detection count
    discord_chapter_nums: list = []
    for ev in discord_events:
        if ev.chapter_num:
            discord_chapter_nums.append(ev.chapter_num)
        for char_name in (ev.characters_detected or []):
            discord_char_counts[char_name] = discord_char_counts.get(char_name, 0) + 1

    # ── 3. Site trading signal (last 7 days) ────────────────────────────────
    signal_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    txns = db.query(
        models.Transaction.character_id,
        models.Transaction.action,
        models.Transaction.quantity,
    ).filter(models.Transaction.timestamp >= signal_cutoff).all()

    site_signal: dict = {}   # char_id → {buy_qty, sell_qty}
    for char_id, action, qty in txns:
        if char_id not in site_signal:
            site_signal[char_id] = {"buy_qty": 0, "sell_qty": 0}
        if action == "buy":
            site_signal[char_id]["buy_qty"] += qty
        elif action == "sell":
            site_signal[char_id]["sell_qty"] += qty

    # Map char_id → name for site signal
    signal_char_ids = list(site_signal.keys())
    site_chars_by_id: dict = {}
    if signal_char_ids:
        for c in db.query(models.Character.id, models.Character.name).filter(
            models.Character.id.in_(signal_char_ids)
        ).all():
            site_chars_by_id[c.id] = c.name

    site_net: dict = {}   # name → net_qty (positive = buy pressure)
    for char_id, sq in site_signal.items():
        name = site_chars_by_id.get(char_id)
        if name:
            site_net[name] = sq["buy_qty"] - sq["sell_qty"]

    # ── 4. Detect chapter number ────────────────────────────────────────────
    chapter_number = max(discord_chapter_nums) if discord_chapter_nums else None
    if chapter_number is None:
        for post in pulse.get("posts", []):
            m = _re.search(r'\bchapter\s*#?(\d{3,4})\b', post.get("title", ""), _re.I)
            if m:
                chapter_number = int(m.group(1))
                break
    if chapter_number is None:
        chapter_number = 0   # admin fills in manually

    # ── 5. Build combined scores ─────────────────────────────────────────────
    # Reddit mention_score (raw) + Discord detections × 1000 + net_buy × 100
    reddit_chars = {c["name"]: c["mention_score"] for c in pulse.get("characters", [])}
    all_names = set(reddit_chars) | set(discord_char_counts) | set(site_net)

    combined: list = []
    for name in all_names:
        r_score = reddit_chars.get(name, 0)
        d_count = discord_char_counts.get(name, 0)
        net_buy = site_net.get(name, 0)
        total = r_score + d_count * 1000 + net_buy * 100
        combined.append({
            "name": name,
            "total": total,
            "reddit_score": r_score,
            "discord_count": d_count,
            "net_buy": net_buy,
        })

    combined.sort(key=lambda x: x["total"], reverse=True)
    top = combined[:12]   # top 12 movers

    # ── 6. Auto-classify directions ──────────────────────────────────────────
    movers = []
    for c in top:
        if c["net_buy"] < -3:
            direction = "down"
        elif c["discord_count"] > 0 and c["reddit_score"] == 0:
            direction = "new"   # Discord-only signal (leak/early spoiler)
        else:
            direction = "up"
        movers.append({"name": c["name"], "direction": direction})

    # ── 7. Generate summary ──────────────────────────────────────────────────
    up_names   = [m["name"] for m in movers if m["direction"] == "up"]
    down_names = [m["name"] for m in movers if m["direction"] == "down"]
    new_names  = [m["name"] for m in movers if m["direction"] == "new"]

    ch_label = f"Ch.{chapter_number}" if chapter_number else "this chapter"
    parts = []
    if up_names:
        parts.append(f"{', '.join(up_names[:4])} seeing strong buy pressure")
    if down_names:
        parts.append(f"{', '.join(down_names[:3])} under heavy sell pressure")
    if new_names:
        parts.append(f"{', '.join(new_names[:3])} entering the board via early signals")

    summary = (
        f"Intelligence compiled from {len(pulse.get('posts', []))} Reddit posts "
        f"and {len(discord_events)} Discord events for {ch_label}. "
        + (" — ".join(parts) + "." if parts else "Market activity nominal.")
    )

    # ── 8. Build response ────────────────────────────────────────────────────
    uplink_label = (
        f"Uplink: {ch_label} ◈ Signal Active"
        if chapter_number
        else "Uplink: Scanning ◈ Awaiting Chapter"
    )
    reddit_context = [
        p["title"] for p in pulse.get("posts", [])[:5]
    ]

    return {
        "chapter_number": chapter_number,
        "uplink_label": uplink_label,
        "summary": summary,
        "movers": movers,
        "reddit_context": reddit_context,
        "sources": {
            "reddit_posts": len(pulse.get("posts", [])),
            "discord_events": len(discord_events),
            "site_signal_chars": len(site_net),
            "top_combined": [
                {
                    "name": c["name"],
                    "reddit_score": c["reddit_score"],
                    "discord_count": c["discord_count"],
                    "net_buy": c["net_buy"],
                }
                for c in combined[:20]
            ],
        },
    }


@router.post("/transmission/regen")
def regen_transmission(
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Regenerate the live transmission using the Vegapunk personality voice.
    Takes the latest ChapterTransmission, enriches its movers with actual
    pct_change + current beri values, runs them through transmission_response(),
    and saves a new ChapterTransmission record.
    Returns {"status": "ok", "summary": <new text>, "chapter_number": int}
    """
    _check_secret(x_admin_secret)

    tx = (
        db.query(models.ChapterTransmission)
        .order_by(models.ChapterTransmission.id.desc())
        .first()
    )
    if not tx:
        raise HTTPException(status_code=404, detail="No transmission found — publish one first.")

    chapter_num = tx.chapter_number or 0
    raw_movers  = tx.movers or []

    # ── 1. Build a name→pct_change lookup from ProposedPriceChange ───────────
    ppc_lookup: dict = {}   # name → {"pct_change": float, "direction": str, "proposed_beri": float}
    if chapter_num:
        rows = db.query(models.ProposedPriceChange).filter(
            models.ProposedPriceChange.chapter_number == chapter_num,
        ).all()
        for r in rows:
            ppc_lookup[r.character_name] = {
                "pct_change": r.pct_change,
                "direction":  r.direction,
                "proposed_beri": r.proposed_beri,
            }

    # ── 2. Build current beri lookup from characters table ───────────────────
    all_names = [m["name"] for m in raw_movers if isinstance(m, dict) and "name" in m]
    beri_lookup: dict = {}
    if all_names:
        chars = db.query(models.Character.name, models.Character.beri).filter(
            models.Character.name.in_(all_names)
        ).all()
        for c in chars:
            beri_lookup[c.name] = c.beri

    # ── 3. Build transmission_response() movers ──────────────────────────────
    tr_movers = []
    for m in raw_movers:
        if not isinstance(m, dict) or "name" not in m:
            continue
        name = m["name"]
        direction = m.get("direction", "up")

        # Prefer real ppc data; fall back to direction-based defaults
        if name in ppc_lookup:
            ppc = ppc_lookup[name]
            signed_pct = ppc["pct_change"] if ppc["direction"] == "up" else -ppc["pct_change"]
            beri = ppc["proposed_beri"]
        else:
            signed_pct = {"up": 5.0, "down": -7.0, "new": 3.0}.get(direction, 5.0)
            beri = beri_lookup.get(name, 0)

        tr_movers.append({"name": name, "change_pct": signed_pct, "beri": beri})

    # Sort: biggest gain first → biggest loss last (transmission_response expects this)
    tr_movers.sort(key=lambda x: x["change_pct"], reverse=True)

    # ── 4. Generate the new voiced summary ───────────────────────────────────
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "../.."))
        from vegapunk.personality import transmission_response
        new_summary = transmission_response(tr_movers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"transmission_response() failed: {e}")

    # ── 5. Save new ChapterTransmission record ────────────────────────────────
    new_tx = models.ChapterTransmission(
        chapter_number=tx.chapter_number,
        uplink_label=tx.uplink_label,
        summary=new_summary,
        movers=tx.movers,
        reddit_context=tx.reddit_context,
    )
    db.add(new_tx)
    db.commit()
    db.refresh(new_tx)

    return {
        "status": "ok",
        "id": new_tx.id,
        "chapter_number": new_tx.chapter_number,
        "summary": new_summary,
    }


# ── Casino admin ──────────────────────────────────────────────────────────────

@router.post("/casino/create")
async def casino_create(
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
    if p.is_chapter_prediction:
        await manager.broadcast({
            "type": "new_prediction",
            "id": p.id,
            "question": p.question,
            "closes_at": p.closes_at.isoformat() if p.closes_at else None,
        })
    try:
        from app.discord_notify import announce_prediction_open
        announce_prediction_open(
            question=p.question,
            options=p.options,
            closes_at=p.closes_at,
            category=p.category,
        )
    except Exception as e:
        print(f"[Admin] Prediction Discord notify failed (non-fatal): {e}")
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
            "is_chapter_prediction": bool(p.is_chapter_prediction),
            "chapter_drop_time": str(p.chapter_drop_time) if p.chapter_drop_time else None,
            "is_break_week": bool(p.is_break_week),
            "total_pool": total,
            "pool_breakdown": per_option,
            "bet_count": len(p.bets),
            "correct_option": p.options[p.correct_option] if p.correct_option is not None else None,
        })
    return result


@router.delete("/casino/{prop_id}")
def delete_proposition(
    prop_id: int,
    force: bool = False,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Delete a proposition. Refuses if bets exist unless force=true, which refunds all bets."""
    _check_secret(x_admin_secret)
    prop = db.query(models.Proposition).filter(models.Proposition.id == prop_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Proposition not found")
    if prop.status == "resolved":
        raise HTTPException(status_code=400, detail="Cannot delete a resolved proposition")

    bets = db.query(models.PropositionBet).filter(
        models.PropositionBet.proposition_id == prop_id
    ).all()

    if bets and not force:
        raise HTTPException(
            status_code=409,
            detail=f"Proposition has {len(bets)} bet(s). Pass force=true to delete and refund all bets."
        )

    refunded = 0
    if bets:
        for bet in bets:
            if bet.is_free_play:
                continue
            user = db.query(models.User).filter(models.User.id == bet.user_id).first()
            if user:
                user.beri_balance += bet.amount
                refunded += 1
                db.add(models.BeriEvent(
                    user_id=bet.user_id,
                    event_type="admin_refund",
                    amount=bet.amount,
                    new_balance=user.beri_balance,
                    description=f"Bet refunded — prop #{prop_id} deleted by admin",
                ))
        db.query(models.PropositionBet).filter(
            models.PropositionBet.proposition_id == prop_id
        ).delete()

    db.delete(prop)
    db.commit()
    return {"status": "ok", "deleted_id": prop_id, "bets_refunded": refunded}


# ── Auto-prediction pipeline admin endpoints ──────────────────────────────────

def _prop_summary(p: models.Proposition) -> dict:
    """Shared serialiser for proposition rows."""
    total = sum(b.amount for b in p.bets)
    return {
        "id": p.id,
        "question": p.question,
        "category": p.category,
        "status": p.status,
        "options": p.options,
        "closes_at": str(p.closes_at) if p.closes_at else None,
        "is_chapter_prediction": bool(p.is_chapter_prediction),
        "source_chapter": p.source_chapter,
        "llm_confidence": p.llm_confidence,
        "llm_reasoning": p.llm_reasoning,
        "total_pool": total,
        "bet_count": len(p.bets),
    }


@router.get("/casino/drafts")
def casino_list_drafts(
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """List Vegapunk-generated draft propositions awaiting admin publish/dismiss."""
    _check_secret(x_admin_secret)
    props = (
        db.query(models.Proposition)
        .filter(models.Proposition.status == "draft")
        .order_by(models.Proposition.created_at.desc())
        .all()
    )
    return [_prop_summary(p) for p in props]


@router.post("/casino/publish/{prop_id}")
def casino_publish_draft(
    prop_id: int,
    payload: Optional[dict] = Body(default=None),
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Publish a draft proposition — sets status to 'open', optionally updates question/closes_at."""
    _check_secret(x_admin_secret)
    prop = db.query(models.Proposition).filter(models.Proposition.id == prop_id).first()
    if not prop:
        raise HTTPException(404, "Proposition not found")
    if prop.status != "draft":
        raise HTTPException(400, f"Proposition is '{prop.status}', not 'draft'")

    if payload:
        if payload.get("question"):
            prop.question = payload["question"].strip()
        if payload.get("closes_at"):
            try:
                from datetime import datetime as _dt
                prop.closes_at = _dt.fromisoformat(payload["closes_at"].replace("Z", "+00:00"))
            except Exception:
                pass
        if payload.get("category"):
            prop.category = payload["category"]

    prop.status = "open"
    db.commit()
    try:
        from app.discord_notify import announce_prediction_open
        announce_prediction_open(
            question=prop.question,
            options=prop.options,
            closes_at=prop.closes_at,
            category=prop.category,
            chapter_num=prop.source_chapter or 0,
        )
    except Exception as e:
        print(f"[Admin] Prediction Discord notify failed (non-fatal): {e}")
    return {"status": "ok", "id": prop.id, "question": prop.question}


@router.delete("/casino/draft/{prop_id}")
def casino_dismiss_draft(
    prop_id: int,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Dismiss (delete) a draft proposition — no bets exist so safe to hard-delete."""
    _check_secret(x_admin_secret)
    prop = db.query(models.Proposition).filter(models.Proposition.id == prop_id).first()
    if not prop:
        raise HTTPException(404, "Proposition not found")
    if prop.status != "draft":
        raise HTTPException(400, f"Use DELETE /casino/{prop_id} to delete non-draft propositions")
    db.delete(prop)
    db.commit()
    return {"status": "ok", "dismissed_id": prop_id}


@router.get("/casino/needs-review")
def casino_needs_review(
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """List propositions Vegapunk couldn't auto-resolve — awaiting admin decision."""
    _check_secret(x_admin_secret)
    props = (
        db.query(models.Proposition)
        .filter(models.Proposition.status == "needs_review")
        .order_by(models.Proposition.created_at.desc())
        .all()
    )
    return [_prop_summary(p) for p in props]


@router.post("/casino/accept-resolve/{prop_id}")
def casino_accept_auto_resolve(
    prop_id: int,
    correct_option: int,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Admin manually resolves a needs_review proposition (uses same payout logic as casino_resolve)."""
    _check_secret(x_admin_secret)
    prop = db.query(models.Proposition).filter(models.Proposition.id == prop_id).first()
    if not prop:
        raise HTTPException(404, "Proposition not found")
    if prop.status not in ("needs_review", "open", "closed"):
        raise HTTPException(400, f"Cannot resolve from status '{prop.status}'")
    if correct_option < 0 or correct_option >= len(prop.options):
        raise HTTPException(400, "correct_option index out of range")

    # Reuse the full resolve logic from casino_resolve
    prop.correct_option = correct_option
    prop.status = "resolved"
    prop.resolved_at = datetime.now(timezone.utc)

    bets = db.query(models.PropositionBet).filter(
        models.PropositionBet.proposition_id == prop_id
    ).all()
    total_pool = sum(b.amount for b in bets)
    house_take = total_pool * (prop.house_cut or 0.05)
    payout_pool = total_pool - house_take
    winners = [b for b in bets if b.option_index == correct_option]
    winner_stake = sum(b.amount for b in winners) or 0

    paid_out = 0
    for bet in bets:
        user = db.query(models.User).filter(models.User.id == bet.user_id).first()
        if not user:
            continue
        if bet.option_index == correct_option and winner_stake > 0:
            share = bet.amount / winner_stake
            payout = payout_pool * share * (getattr(bet, "multiplier", 1.0) or 1.0)
            user.beri_balance += payout
            bet.payout = payout
            paid_out += payout
            db.add(models.BeriEvent(
                user_id=user.id,
                event_type="casino_win",
                amount=payout,
                description=f"Prediction win — {prop.question[:60]}",
            ))
        else:
            penalty = getattr(bet, "penalty_amount", 0) or 0
            if penalty > 0 and not getattr(bet, "is_free_play", False):
                user.beri_balance = max(0, user.beri_balance - penalty)
                db.add(models.BeriEvent(
                    user_id=user.id,
                    event_type="casino_penalty",
                    amount=-penalty,
                    description=f"Prediction penalty — {prop.question[:60]}",
                ))
            bet.payout = 0

    db.commit()
    return {
        "status": "resolved",
        "winners": len(winners),
        "total_paid_out": paid_out,
    }


@router.post("/predictions/generate")
def trigger_prediction_generation(
    chapter: Optional[int] = None,
    force: bool = False,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Manually trigger prediction generation for the latest (or specified) chapter."""
    _check_secret(x_admin_secret)
    from sqlalchemy import func as sqlfunc
    from app.prediction_pipeline import generate_chapter_predictions

    if chapter is None:
        chapter = db.query(sqlfunc.max(models.Chapter.number)).scalar()
    if not chapter:
        raise HTTPException(400, "No chapters in DB — run chapter detection first")

    result = generate_chapter_predictions(db, chapter, force=force)
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


# ── Chapter Pipeline ──────────────────────────────────────────────────────────

@router.post("/chapter-detect")
def chapter_detect(
    chapter: Optional[int] = None,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Trigger chapter pipeline. Pass ?chapter=1183 to skip Reddit detection entirely
    and run the pipeline for that specific chapter number."""
    _check_secret(x_admin_secret)
    from app.chapter_pipeline import detect_chapter_drop
    result = detect_chapter_drop(db, force_chapter=chapter)
    return result


@router.get("/proposed-prices")
def list_proposed_prices(
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """List pending price change proposals grouped by chapter number."""
    _check_secret(x_admin_secret)
    rows = (
        db.query(models.ProposedPriceChange)
        .filter(models.ProposedPriceChange.status == "pending")
        .order_by(
            models.ProposedPriceChange.chapter_number.desc(),
            models.ProposedPriceChange.pct_change.desc(),
        )
        .all()
    )
    chapters = db.query(models.Chapter).order_by(models.Chapter.number.desc()).limit(10).all()
    return {
        "proposals": [
            {
                "id": r.id,
                "chapter_number": r.chapter_number,
                "character_id": r.character_id,
                "character_name": r.character_name,
                "current_beri": r.current_beri,
                "proposed_beri": r.proposed_beri,
                "direction": r.direction,
                "pct_change": r.pct_change,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "recent_chapters": [
            {
                "number": c.number,
                "title": c.title,
                "reddit_url": c.reddit_url,
                "detected_at": c.detected_at.isoformat() if c.detected_at else None,
                "processed": c.processed,
            }
            for c in chapters
        ],
    }


@router.post("/proposed-prices/{proposal_id}/approve")
def approve_proposed_price(
    proposal_id: int,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Apply a proposed price change to the character."""
    _check_secret(x_admin_secret)
    proposal = db.query(models.ProposedPriceChange).filter(
        models.ProposedPriceChange.id == proposal_id
    ).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(status_code=400, detail=f"Proposal already {proposal.status}")

    char = db.query(models.Character).filter(
        models.Character.id == proposal.character_id
    ).first()
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    old_beri = char.beri
    char.beri = max(100_000, proposal.proposed_beri)
    proposal.status = "approved"
    db.commit()

    invalidate_char_cache()

    # Fire Discord price alert for significant moves (±10%+)
    if abs(proposal.pct_change) >= 10:
        try:
            announce_price_alert(char.name, proposal.pct_change if proposal.direction == "up" else -proposal.pct_change, char.beri)
        except Exception:
            pass

    return {
        "status": "approved",
        "character": char.name,
        "old_beri": old_beri,
        "new_beri": char.beri,
        "pct_change": proposal.pct_change,
        "direction": proposal.direction,
    }


@router.post("/proposed-prices/{proposal_id}/dismiss")
def dismiss_proposed_price(
    proposal_id: int,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Dismiss a price proposal without applying it."""
    _check_secret(x_admin_secret)
    proposal = db.query(models.ProposedPriceChange).filter(
        models.ProposedPriceChange.id == proposal_id
    ).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    proposal.status = "dismissed"
    db.commit()
    return {"status": "dismissed", "id": proposal_id}


@router.post("/proposed-prices/approve-all")
def approve_all_proposed_prices(
    chapter: Optional[int] = None,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Approve all pending proposals (optionally filtered to a specific chapter)."""
    _check_secret(x_admin_secret)
    q = db.query(models.ProposedPriceChange).filter(
        models.ProposedPriceChange.status == "pending"
    )
    if chapter:
        q = q.filter(models.ProposedPriceChange.chapter_number == chapter)
    proposals = q.all()

    char_ids = [p.character_id for p in proposals if p.character_id]
    chars = {c.id: c for c in db.query(models.Character).filter(
        models.Character.id.in_(char_ids)
    ).all()}

    applied = 0
    big_movers = []
    all_moves  = []
    chapter_num = chapter  # may be None if no filter
    for proposal in proposals:
        char = chars.get(proposal.character_id)
        if char:
            char.beri = max(100_000, proposal.proposed_beri)
            proposal.status = "approved"
            applied += 1
            signed = proposal.pct_change if proposal.direction == "up" else -proposal.pct_change
            all_moves.append({
                "name":      char.name,
                "pct":       proposal.pct_change,
                "new_beri":  char.beri,
                "direction": proposal.direction,
                "reason":    proposal.reason or "",
            })
            if abs(proposal.pct_change) >= 10:
                big_movers.append((char.name, signed, char.beri))
            if not chapter_num:
                chapter_num = proposal.chapter_number
            # Append chapter event to Cognitive Analysis
            arrow = "▲" if proposal.direction == "up" else "▼"
            ch_label = f"Ch.{proposal.chapter_number}" if proposal.chapter_number else "Latest chapter"
            event_line = f"{ch_label} — {arrow} {proposal.pct_change:.1f}% | {proposal.reason or 'chapter signal'}"
            char.events = (char.events + "\n" + event_line).strip() if char.events else event_line
        else:
            proposal.status = "dismissed"

    db.commit()
    invalidate_char_cache()

    # Fire individual big-mover alerts
    for name, pct, beri in big_movers:
        try:
            announce_price_alert(name, pct, beri)
        except Exception:
            pass

    # Fire full Vegapunk analysis to #chapter-intel
    if all_moves:
        try:
            announce_chapter_price_analysis(chapter_num or 0, all_moves)
        except Exception:
            pass

    return {"status": "ok", "applied": applied, "total": len(proposals)}


# ── Proposed New Characters ───────────────────────────────────────────────────

@router.get("/proposed-characters")
def list_proposed_characters(
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """List all pending proposed new characters."""
    _check_secret(x_admin_secret)
    rows = (
        db.query(models.ProposedNewCharacter)
        .filter(models.ProposedNewCharacter.status == "pending")
        .order_by(models.ProposedNewCharacter.chapter_number.desc(), models.ProposedNewCharacter.created_at)
        .all()
    )
    return [
        {
            "id": r.id,
            "chapter_number": r.chapter_number,
            "name": r.name,
            "aliases": r.aliases or [],
            "faction": r.faction,
            "category": r.category,
            "proposed_beri": r.proposed_beri,
            "base_beri": r.base_beri,
            "canon_bounty": r.canon_bounty,
            "bio": r.bio,
            "events": r.events,
            "reason": r.reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/proposed-characters")
def create_proposed_character(
    payload: dict,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Manually propose a new character for admin review."""
    _check_secret(x_admin_secret)
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    proposed_beri = float(payload.get("proposed_beri", 0) or 0)
    if proposed_beri <= 0:
        raise HTTPException(status_code=400, detail="proposed_beri must be > 0")
    row = models.ProposedNewCharacter(
        chapter_number=payload.get("chapter_number"),
        name=name,
        aliases=payload.get("aliases") or [],
        faction=payload.get("faction", ""),
        category=payload.get("category", ""),
        proposed_beri=proposed_beri,
        base_beri=payload.get("base_beri") or proposed_beri,
        canon_bounty=payload.get("canon_bounty"),
        bio=payload.get("bio", ""),
        events=payload.get("events", ""),
        reason=payload.get("reason", ""),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "ok", "id": row.id, "name": row.name}


@router.post("/proposed-characters/{proposal_id}/approve")
def approve_proposed_character(
    proposal_id: int,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Approve a proposed new character — creates the Character row and marks the proposal approved."""
    _check_secret(x_admin_secret)
    proposal = db.query(models.ProposedNewCharacter).filter(
        models.ProposedNewCharacter.id == proposal_id
    ).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != "pending":
        raise HTTPException(status_code=400, detail=f"Proposal already {proposal.status}")

    existing = db.query(models.Character).filter(models.Character.name == proposal.name).first()
    if existing:
        proposal.status = "approved"
        db.commit()
        raise HTTPException(status_code=409, detail=f"Character '{proposal.name}' already exists (id={existing.id})")

    beri = max(100_000, proposal.proposed_beri)
    char = models.Character(
        name=proposal.name,
        aliases=proposal.aliases or [],
        faction=proposal.faction or "",
        category=proposal.category or "",
        beri=beri,
        base_beri=proposal.base_beri or beri,
        canon_bounty=proposal.canon_bounty,
        status="active",
        price_history=[],
        img="",
        bio=proposal.bio or "",
        events=proposal.events or "",
        sbs=[],
        related=[],
    )
    db.add(char)
    proposal.status = "approved"
    db.commit()
    db.refresh(char)
    invalidate_char_cache()
    return {"status": "approved", "character_id": char.id, "name": char.name, "beri": char.beri}


@router.post("/proposed-characters/{proposal_id}/dismiss")
def dismiss_proposed_character(
    proposal_id: int,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Dismiss a proposed new character without adding them."""
    _check_secret(x_admin_secret)
    proposal = db.query(models.ProposedNewCharacter).filter(
        models.ProposedNewCharacter.id == proposal_id
    ).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    proposal.status = "dismissed"
    db.commit()
    return {"status": "dismissed", "id": proposal_id}


# ── Character Intel Editor ────────────────────────────────────────────────────

@router.get("/characters/search")
def search_character(
    name: str,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Find a character by name (case-insensitive partial match). Returns top 8 matches."""
    _check_secret(x_admin_secret)
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    rows = (
        db.query(models.Character)
        .filter(models.Character.name.ilike(f"%{name}%"))
        .order_by(models.Character.name)
        .limit(8)
        .all()
    )
    return [
        {
            "id": c.id,
            "name": c.name,
            "faction": c.faction,
            "beri": c.beri,
            "bio": c.bio or "",
            "events": c.events or "",
        }
        for c in rows
    ]


@router.patch("/characters/{char_id}/intel")
def update_character_intel(
    char_id: int,
    payload: dict,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Update a character's bio and/or events (cognitive analysis). Partial updates supported.
    Pass append_events=true to append to existing events instead of replacing."""
    _check_secret(x_admin_secret)
    char = db.query(models.Character).filter(models.Character.id == char_id).first()
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    if "bio" in payload:
        char.bio = payload["bio"]
    if "events" in payload:
        if payload.get("append_events"):
            existing = (char.events or "").strip()
            new_line = payload["events"].strip()
            char.events = (existing + "\n" + new_line).strip() if existing else new_line
        else:
            char.events = payload["events"]

    db.commit()
    invalidate_char_cache()
    return {
        "status": "ok",
        "id": char.id,
        "name": char.name,
        "bio": char.bio,
        "events": char.events,
    }


# ── Direct Character Add ──────────────────────────────────────────────────────

@router.post("/characters/add")
def add_character_direct(
    payload: dict,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Directly add a new character to the roster without going through the proposal queue.
    Use for retroactive additions or characters missed by the pipeline."""
    _check_secret(x_admin_secret)
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    beri = float(payload.get("beri", 0) or 0)
    if beri <= 0:
        raise HTTPException(status_code=400, detail="beri must be > 0")

    existing = db.query(models.Character).filter(models.Character.name == name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"'{name}' already exists (id={existing.id})")

    char = models.Character(
        name=name,
        aliases=payload.get("aliases") or [],
        faction=payload.get("faction", ""),
        category=payload.get("category", ""),
        beri=max(100_000, beri),
        base_beri=max(100_000, beri),
        canon_bounty=payload.get("canon_bounty"),
        status="active",
        price_history=[],
        img="",
        bio=payload.get("bio", ""),
        events=payload.get("events", ""),
        sbs=[],
        related=[],
    )
    db.add(char)
    db.commit()
    db.refresh(char)
    invalidate_char_cache()
    return {"status": "ok", "id": char.id, "name": char.name, "beri": char.beri}


# ── Character meta sync ────────────────────────────────────────────────────────

@router.post("/sync-character-meta")
def sync_character_meta(
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Read data/character_meta.json and patch bio, events, sbs, and related
    for every character in the DB that has a matching name entry in the file.
    Does NOT touch beri, price_history, or any financial fields.
    """
    _check_secret(x_admin_secret)

    meta_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "character_meta.json")
    meta_path = os.path.normpath(meta_path)

    try:
        with open(meta_path) as f:
            meta: dict = _json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="character_meta.json not found")

    chars = db.query(models.Character).all()
    updated = []
    skipped = []

    for char in chars:
        entry = meta.get(char.name)
        if not entry:
            skipped.append(char.name)
            continue
        char.bio     = entry.get("bio", char.bio or "")
        char.events  = entry.get("events", char.events or "")
        char.sbs     = entry.get("sbs", char.sbs or [])
        char.related = entry.get("related", char.related or [])
        updated.append(char.name)

    db.commit()
    invalidate_char_cache()
    return {"status": "ok", "updated": len(updated), "skipped": len(skipped), "updated_names": updated}


# ── Bot key-value store ────────────────────────────────────────────────────────

@router.get("/kv/{key}")
def get_kv(
    key: str,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    _check_secret(x_admin_secret)
    row = db.query(models.BotKV).filter(models.BotKV.key == key).first()
    return {"key": key, "value": row.value if row else ""}


@router.post("/kv/{key}")
def set_kv(
    key: str,
    payload: dict,
    x_admin_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    _check_secret(x_admin_secret)
    value = str(payload.get("value", ""))
    row = db.query(models.BotKV).filter(models.BotKV.key == key).first()
    if row:
        row.value = value
    else:
        db.add(models.BotKV(key=key, value=value))
    db.commit()
    return {"key": key, "value": value}
