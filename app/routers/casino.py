from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas, auth

router = APIRouter(prefix="/casino", tags=["casino"])

MIN_BET = 100          # minimum beri per real bet
FREE_PLAY_CREDIT = 5_000  # nominal stake for free play bets (funded by house)


def _prediction_multiplier(chapter_drop_time: datetime, now: datetime) -> tuple[float, float]:
    """Return (multiplier, penalty_amount_fraction) based on when the bet is placed
    relative to the chapter drop time.

    Multiplier tiers (hours_before_drop):
      > 72h  → 2.0×
      > 48h  → 1.7×
      > 24h  → 1.4×
      > 0h   → 1.2×
       0–24h after → 1.0× (safe zone, no penalty)
      > 24h after  → 1.0× with 30% penalty on loss
    """
    hours_before = (chapter_drop_time - now).total_seconds() / 3600
    if hours_before > 72:
        return 2.0, 0.0
    if hours_before > 48:
        return 1.7, 0.0
    if hours_before > 24:
        return 1.4, 0.0
    if hours_before > 0:
        return 1.2, 0.0
    if hours_before > -24:
        return 1.0, 0.0       # safe zone after drop
    return 1.0, 0.30           # late bet: 30% penalty if wrong


def _build_odds(prop: models.Proposition, user_id: Optional[int] = None) -> schemas.PropositionOut:
    """Compute live pool breakdown and attach user's current bet if any."""
    bets = prop.bets or []
    totals = [0.0] * len(prop.options)
    for b in bets:
        if 0 <= b.option_index < len(totals):
            totals[b.option_index] += b.amount

    total_pool = sum(totals)

    odds = [
        schemas.OptionOdds(
            index=i,
            label=prop.options[i],
            total_bet=totals[i],
            implied_pct=round(totals[i] / total_pool * 100, 1) if total_pool > 0 else round(100 / len(prop.options), 1),
        )
        for i in range(len(prop.options))
    ]

    user_bet = next((b for b in bets if b.user_id == user_id), None) if user_id else None

    return schemas.PropositionOut(
        id=prop.id,
        question=prop.question,
        category=prop.category,
        options=prop.options,
        status=prop.status,
        house_cut=prop.house_cut,
        closes_at=prop.closes_at,
        created_at=prop.created_at,
        resolved_at=prop.resolved_at,
        correct_option=prop.correct_option,
        odds=odds,
        total_pool=total_pool,
        user_bet_option=user_bet.option_index if user_bet else None,
        user_bet_amount=user_bet.amount if user_bet else None,
        is_chapter_prediction=bool(prop.is_chapter_prediction),
        chapter_drop_time=prop.chapter_drop_time,
        is_break_week=bool(prop.is_break_week),
        user_bet_multiplier=user_bet.multiplier if user_bet else None,
    )


@router.get("/", response_model=List[schemas.PropositionOut])
def list_propositions(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(auth.get_optional_user),
):
    """List propositions. Optionally filter by status: open | closed | resolved."""
    q = db.query(models.Proposition).order_by(models.Proposition.created_at.desc())
    if status:
        q = q.filter(models.Proposition.status == status)
    props = q.limit(50).all()
    uid = current_user.id if current_user else None
    return [_build_odds(p, uid) for p in props]


@router.get("/{prop_id}", response_model=schemas.PropositionOut)
def get_proposition(
    prop_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(auth.get_optional_user),
):
    prop = db.query(models.Proposition).filter(models.Proposition.id == prop_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Proposition not found")
    uid = current_user.id if current_user else None
    return _build_odds(prop, uid)


@router.post("/bet", response_model=schemas.BetOut)
def place_bet(
    req: schemas.BetRequest,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    prop = db.query(models.Proposition).filter(models.Proposition.id == req.proposition_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Proposition not found")
    if prop.status != "open":
        raise HTTPException(status_code=400, detail="Betting is closed for this proposition")
    if prop.closes_at and datetime.now(timezone.utc) >= prop.closes_at:
        raise HTTPException(status_code=400, detail="Betting period has ended")
    if req.option_index < 0 or req.option_index >= len(prop.options):
        raise HTTPException(status_code=400, detail="Invalid option")

    # One bet per user per proposition — no changing sides after placing
    existing = db.query(models.PropositionBet).filter(
        models.PropositionBet.proposition_id == req.proposition_id,
        models.PropositionBet.user_id == current_user.id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="You have already placed a bet on this proposition")

    now = datetime.now(timezone.utc)

    # ── Free play handling ────────────────────────────────────────────────────
    use_free_play = req.is_free_play and bool(prop.is_chapter_prediction)
    if use_free_play:
        # One free play per 24 hours across all chapter predictions
        if current_user.free_play_used_at:
            cooldown_ends = current_user.free_play_used_at + timedelta(hours=24)
            if now < cooldown_ends:
                remaining = int((cooldown_ends - now).total_seconds() / 3600)
                raise HTTPException(
                    status_code=400,
                    detail=f"Free play recharges in ~{remaining}h"
                )
        bet_amount = float(FREE_PLAY_CREDIT)
        current_user.free_play_used_at = now
        # No beri deducted — house covers the free play credit
    else:
        if req.amount < MIN_BET:
            raise HTTPException(status_code=400, detail=f"Minimum bet is {MIN_BET:,}฿")
        if current_user.beri_balance < req.amount:
            raise HTTPException(status_code=400, detail="Insufficient beri")
        bet_amount = float(req.amount)

    # ── Chapter prediction multiplier ─────────────────────────────────────────
    multiplier = 1.0
    penalty_fraction = 0.0
    if prop.is_chapter_prediction and prop.chapter_drop_time:
        multiplier, penalty_fraction = _prediction_multiplier(prop.chapter_drop_time, now)

    penalty_amount = round(bet_amount * penalty_fraction) if not use_free_play else 0

    # ── Break-week double down ────────────────────────────────────────────────
    doubled_down = req.doubled_down and bool(prop.is_break_week) and not use_free_play
    if doubled_down:
        multiplier = max(multiplier, 2.0)
        penalty_amount = int(bet_amount)    # lose extra if wrong

    # ── Deduct stake and log event ────────────────────────────────────────────
    if not use_free_play:
        current_user.beri_balance -= bet_amount
        label = prop.options[req.option_index]
        extra = ""
        if multiplier > 1.0:
            extra = f" ({multiplier}× multiplier)"
        if doubled_down:
            extra = " (doubled down)"
        db.add(models.BeriEvent(
            user_id=current_user.id,
            event_type="casino_bet",
            amount=-bet_amount,
            description=f"Prediction bet — {bet_amount:,.0f}฿ on \"{label}\"{extra}",
        ))

    bet = models.PropositionBet(
        proposition_id=prop.id,
        user_id=current_user.id,
        option_index=req.option_index,
        amount=bet_amount,
        is_free_play=use_free_play,
        multiplier=multiplier,
        penalty_amount=penalty_amount,
        doubled_down=doubled_down,
    )
    db.add(bet)
    db.commit()
    db.refresh(current_user)

    return schemas.BetOut(
        proposition_id=prop.id,
        option_index=req.option_index,
        amount=bet_amount,
        new_balance=current_user.beri_balance,
        multiplier=multiplier,
        is_free_play=use_free_play,
    )
