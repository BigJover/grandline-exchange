import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.scheduler import run_beri_drop, WEEKLY_DROP

router = APIRouter(prefix="/admin", tags=["admin"])


def _check_secret(secret: str):
    expected = os.getenv("ADMIN_SECRET", "").strip()
    if not expected or secret.strip() != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/rename-user")
def rename_user(secret: str, old_username: str, new_username: str, db: Session = Depends(get_db)):
    """Rename a user. Protected by ADMIN_SECRET."""
    _check_secret(secret)
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
def set_character_beri(secret: str, name: str, beri: float, db: Session = Depends(get_db)):
    """Set a character's beri value by name. Protected by ADMIN_SECRET."""
    _check_secret(secret)
    char = db.query(models.Character).filter(models.Character.name == name).first()
    if not char:
        raise HTTPException(status_code=404, detail=f"Character '{name}' not found")
    old = char.beri
    char.beri = beri
    db.commit()
    return {"status": "ok", "name": char.name, "old_beri": old, "new_beri": beri}


@router.post("/buster-call")
def trigger_buster_call(
    secret: str,
    loss_pct: float = 0.20,
    marine_bonus: int = 100_000,
    db: Session = Depends(get_db),
):
    """Fire a Buster Call: hits all non-Warlord Pirates with a % loss,
    rewards all Marines with a flat bonus. Protected by ADMIN_SECRET."""
    _check_secret(secret)
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


@router.post("/beri-drop")
def trigger_beri_drop(secret: str, db: Session = Depends(get_db)):
    """Manually trigger a beri drop. Protected by ADMIN_SECRET env var."""
    _check_secret(secret)
    run_beri_drop()
    count = db.query(models.User).filter(models.User.is_bot == False).count()
    return {
        "status": "ok",
        "beri_per_user": WEEKLY_DROP,
        "users_paid": count,
        "total_distributed": WEEKLY_DROP * count,
    }
