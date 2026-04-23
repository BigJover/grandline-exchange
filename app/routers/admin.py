import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app import models
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
