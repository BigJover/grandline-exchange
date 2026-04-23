import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app import models, schemas
from app.scheduler import run_beri_drop, WEEKLY_DROP

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

    total_pool = sum(b.amount for b in bets)
    winner_pool = sum(b.amount for b in bets if b.option_index == correct_option)
    house_take = round(total_pool * prop.house_cut, 2)
    prize_pool = total_pool - house_take

    winners, losers, total_paid = 0, 0, 0.0

    for bet in bets:
        user = db.query(models.User).filter(models.User.id == bet.user_id).first()
        if not user:
            continue
        if bet.option_index == correct_option and winner_pool > 0:
            payout = round((bet.amount / winner_pool) * prize_pool, 2)
            bet.payout = payout
            user.beri_balance += payout
            total_paid += payout
            db.add(models.BeriEvent(
                user_id=user.id,
                event_type="casino_win",
                amount=payout,
                description=(
                    f"Casino win — \"{prop.question}\" → "
                    f"\"{prop.options[correct_option]}\" "
                    f"({payout:,.0f}฿ on {bet.amount:,.0f}฿ bet)"
                ),
            ))
            winners += 1
        else:
            bet.payout = 0.0
            db.add(models.BeriEvent(
                user_id=user.id,
                event_type="casino_loss",
                amount=0,
                description=(
                    f"Casino loss — \"{prop.question}\" → "
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
            "closes_at": str(p.closes_at) if p.closes_at else None,
            "total_pool": total,
            "pool_breakdown": per_option,
            "correct_option": p.options[p.correct_option] if p.correct_option is not None else None,
        })
    return result
