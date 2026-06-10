from datetime import datetime, timezone, timedelta

WARLORD_WEEKLY_COST = 1_000_000  # 1M beri per week
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas, auth

router = APIRouter(prefix="/users", tags=["users"])

VALID_FACTIONS = {"marine", "royalty", "pirate", "citizen", "revolutionary", "creature"}
FACTION_COOLDOWN_HOURS = 24
ROYALTY_BERI_MINIMUM = 500_000

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

    if target == "royalty" and current_user.beri_balance < ROYALTY_BERI_MINIMUM:
        raise HTTPException(
            status_code=403,
            detail=f"Royalty requires {ROYALTY_BERI_MINIMUM:,}฿ — you have {int(current_user.beri_balance):,}฿",
        )

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


@router.post("/me/warlord", response_model=schemas.UserOut)
def subscribe_warlord(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Pay 1M beri for 7 days of Warlord protection. Stackable on active subscription."""
    if current_user.user_faction != "pirate":
        raise HTTPException(status_code=400, detail="Warlord status is only available to Pirates")

    if current_user.beri_balance < WARLORD_WEEKLY_COST:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient beri \u2014 need {WARLORD_WEEKLY_COST:,}\u0e3f",
        )

    now = datetime.now(timezone.utc)
    base = (
        current_user.warlord_until
        if current_user.warlord_until and current_user.warlord_until > now
        else now
    )
    current_user.warlord_until = base + timedelta(days=7)
    current_user.beri_balance -= WARLORD_WEEKLY_COST

    expiry_str = current_user.warlord_until.strftime("%b %d, %Y")
    db.add(models.BeriEvent(
        user_id=current_user.id,
        event_type="warlord_subscribe",
        amount=-WARLORD_WEEKLY_COST,
        description=f"Warlord registration \u2014 immunity active until {expiry_str}",
    ))

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/leaderboard", response_model=List[schemas.LeaderboardEntry])
def get_leaderboard(
    limit: int = 50,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 100))
    now = datetime.now(timezone.utc)
    top = (
        db.query(models.User)
        .filter(models.User.is_bot == False)
        .order_by(models.User.beri_balance.desc())
        .limit(limit)
        .all()
    )

    entries = []
    current_in_top = False
    for rank, u in enumerate(top, 1):
        is_current = u.id == current_user.id
        if is_current:
            current_in_top = True
        entries.append(schemas.LeaderboardEntry(
            rank=rank,
            username=u.username,
            user_faction=u.user_faction,
            beri_balance=u.beri_balance,
            warlord_active=bool(u.warlord_until and u.warlord_until > now),
            is_current=is_current,
        ))

    if not current_in_top:
        higher_count = (
            db.query(models.User)
            .filter(models.User.is_bot == False, models.User.beri_balance > current_user.beri_balance)
            .count()
        )
        entries.append(schemas.LeaderboardEntry(
            rank=higher_count + 1,
            username=current_user.username,
            user_faction=current_user.user_faction,
            beri_balance=current_user.beri_balance,
            warlord_active=bool(current_user.warlord_until and current_user.warlord_until > now),
            is_current=True,
        ))

    return entries


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
