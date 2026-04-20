from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import models, schemas, auth

router = APIRouter(prefix="/trade", tags=["trade"])


def share_price(character: models.Character) -> int:
    """Canonical share price: beri market cap / 100,000."""
    return max(1, int(character.beri // 100_000))


@router.post("/", response_model=schemas.TradeResponse)
def execute_trade(
    req: schemas.TradeRequest,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    if req.action not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="action must be 'buy' or 'sell'")
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be positive")

    character = db.query(models.Character).filter(models.Character.id == req.character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")

    price = share_price(character)
    total = price * req.quantity

    share = db.query(models.Share).filter(
        models.Share.user_id == current_user.id,
        models.Share.character_id == req.character_id,
    ).first()

    if req.action == "buy":
        if current_user.beri_balance < total:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient beri — need {total:,}, have {int(current_user.beri_balance):,}",
            )
        current_user.beri_balance -= total
        if share:
            share.quantity += req.quantity
        else:
            share = models.Share(
                user_id=current_user.id,
                character_id=req.character_id,
                quantity=req.quantity,
            )
            db.add(share)

    else:  # sell
        held = share.quantity if share else 0
        if held < req.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Only holding {held} share{'s' if held != 1 else ''}, cannot sell {req.quantity}",
            )
        current_user.beri_balance += total
        share.quantity -= req.quantity

    db.add(models.Transaction(
        user_id=current_user.id,
        character_id=req.character_id,
        action=req.action,
        quantity=req.quantity,
        price=price,
    ))

    db.commit()
    db.refresh(current_user)
    if share:
        db.refresh(share)

    shares_held = share.quantity if share else 0

    return schemas.TradeResponse(
        beri_balance=current_user.beri_balance,
        shares_held=shares_held,
        price_per_share=float(price),
    )


@router.get("/portfolio", response_model=List[schemas.ShareOut])
def get_portfolio(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(models.Share)
        .filter(models.Share.user_id == current_user.id, models.Share.quantity > 0)
        .all()
    )
