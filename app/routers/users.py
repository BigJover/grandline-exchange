from datetime import datetime, timezone, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas, auth

router = APIRouter(prefix="/users", tags=["users"])

VALID_FACTIONS = {"marine", "royalty", "pirate", "citizen", "revolutionary", "creature"}
FACTION_COOLDOWN_HOURS = 24

FACTION_BLOCKS = {
    "pirate":        ["marine"],
    "revolutionary": ["marine", "royalty"],
    "marine":        ["pirate", "revolutionary"],
}

# ── Event type labels shown in the ledger ────────────────────────────────────
EVENT_LABELS = {
    "salary":              "Marine Salary",
    "royalty_income":      "Noble Income",
    "citizen_income":      "Citizen Dividend",
    "revolutionary_income": "Revolutionary Fund",
    "plunder":             "Pirate Plunder",
    "tidal_income":        "Tidal Income",
    "base_drop":           "Weekly Drop",
    "trade_buy":           "Share Purchase",
    "trade_sell":          "Share Sale",
}


@router.patch("/me/faction", response_model=schemas.UserOut)
def set_faction(
    payload: schemas.FactionUpdate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    target = payload.faction.lower().strip()

    if target not in VALID_FACTIONS:
        raise HTTPException(status_code=400, detail="Invalid faction")

    if target == "creature" and not current_user.creature_unlocked:
        raise HTTPException(status_code=403, detail="Creature faction is locked — earn the unlock first")

    if current_user.user_faction == "creature":
        raise HTTPException(status_code=403, detail="You have lost your humanity. There is no way back.")

    if current_user.user_faction == target:
        raise HTTPException(status_code=400, detail="Already aligned with this faction")

    # 24-hour cooldown
    if current_user.last_faction_change:
        elapsed = datetime.now(timezone.utc) - current_user.last_faction_change
        if elapsed < timedelta(hours=FACTION_COOLDOWN_HOURS):
            remaining = timedelta(hours=FACTION_COOLDOWN_HOURS) - elapsed
            h = int(remaining.total_seconds() // 3600)
            m = int((remaining.total_seconds() % 3600) // 60)
            raise HTTPException(
                status_code=429,
                detail=f"Faction cooldown active — {h}h {m}m remaining",
            )

    history = list(current_user.faction_history or [])
    for past_faction, blocked_list in FACTION_BLOCKS.items():
        if past_faction in history and target in blocked_list:
            raise HTTPException(
                status_code=403,
                detail=f"Your history as a {past_faction} permanently blocks this path",
            )

    current_user.user_faction = target
    if target not in history:
        history.append(target)
    current_user.faction_history = history
    current_user.last_faction_change = datetime.now(timezone.utc)

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/me/ledger", response_model=List[schemas.LedgerEntry])
def get_ledger(
    limit: int = 60,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    # Fetch beri income events
    beri_rows = (
        db.query(models.BeriEvent)
        .filter(models.BeriEvent.user_id == current_user.id)
        .order_by(models.BeriEvent.timestamp.desc())
        .limit(limit)
        .all()
    )

    # Fetch trade events joined with character name
    trade_rows = (
        db.query(models.Transaction, models.Character.name)
        .join(models.Character, models.Transaction.character_id == models.Character.id)
        .filter(models.Transaction.user_id == current_user.id)
        .order_by(models.Transaction.timestamp.desc())
        .limit(limit)
        .all()
    )

    entries = []

    for e in beri_rows:
        entries.append(schemas.LedgerEntry(
            uid=f"e_{e.id}",
            entry_type=e.event_type,
            amount=e.amount,
            description=e.description,
            timestamp=e.timestamp,
        ))

    for trade, char_name in trade_rows:
        beri_delta = -(trade.quantity * trade.price) if trade.action == "buy" else (trade.quantity * trade.price)
        verb = "Bought" if trade.action == "buy" else "Sold"
        entries.append(schemas.LedgerEntry(
            uid=f"t_{trade.id}",
            entry_type=f"trade_{trade.action}",
            amount=beri_delta,
            description=f"{verb} {trade.quantity}\u00d7 {char_name} @ {int(trade.price):,}\u0e3f",
            timestamp=trade.timestamp,
        ))

    entries.sort(key=lambda x: x.timestamp, reverse=True)
    return entries[:limit]
