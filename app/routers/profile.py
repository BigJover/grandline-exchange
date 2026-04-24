from datetime import datetime, timezone, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas, auth

router = APIRouter(prefix="/profile", tags=["profile"])

USERNAME_CHANGE_COOLDOWN_DAYS = 30


@router.get("/me/full", response_model=schemas.ProfileFull)
def get_profile_full(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    # Portfolio
    shares = (
        db.query(models.Share, models.Character)
        .join(models.Character, models.Share.character_id == models.Character.id)
        .filter(models.Share.user_id == current_user.id, models.Share.quantity > 0)
        .all()
    )
    portfolio = [
        schemas.PortfolioEntry(
            character_id=char.id,
            character_name=char.name,
            character_img=char.img or "",
            quantity=share.quantity,
            current_price=char.beri,
            total_value=share.quantity * char.beri,
        )
        for share, char in shares
    ]

    # Leaderboard rank
    rank_count = (
        db.query(models.User)
        .filter(models.User.is_bot == False, models.User.beri_balance > current_user.beri_balance)
        .count()
    )
    leaderboard_rank = rank_count + 1
    total_users = db.query(models.User).filter(models.User.is_bot == False).count()

    # Referral stats
    referrals_count = (
        db.query(models.User)
        .filter(models.User.referred_by_id == current_user.id)
        .count()
    )
    referral_stats = schemas.ReferralStats(
        referral_code=current_user.referral_code or "",
        referrals_count=referrals_count,
        beri_earned=current_user.referral_beri_earned or 0,
    )

    return schemas.ProfileFull(
        user=current_user,
        portfolio=portfolio,
        leaderboard_rank=leaderboard_rank,
        total_users=total_users,
        referral_stats=referral_stats,
    )


@router.put("/me/picture", response_model=schemas.UserOut)
def update_profile_picture(
    payload: schemas.ProfilePictureUpdate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    pic = payload.profile_picture.strip()
    if not pic:
        raise HTTPException(status_code=400, detail="profile_picture cannot be empty")
    # Allow: "char:<id>" references or any http/https URL
    if not (pic.startswith("char:") or pic.startswith("http://") or pic.startswith("https://")):
        raise HTTPException(status_code=400, detail="profile_picture must be a URL or char:<id> reference")
    current_user.profile_picture = pic
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/me/username", response_model=schemas.UserOut)
def update_username(
    payload: schemas.UsernameUpdate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    new_name = payload.username.strip()
    if len(new_name) < 3 or len(new_name) > 32:
        raise HTTPException(status_code=400, detail="Username must be 3–32 characters")
    if not new_name.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Username may only contain letters, numbers, _ and -")
    if new_name.lower() == current_user.username.lower():
        raise HTTPException(status_code=400, detail="That is already your username")
    if db.query(models.User).filter(models.User.username == new_name).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    current_user.username = new_name
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/me/referral-stats", response_model=schemas.ReferralStats)
def get_referral_stats(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    referrals_count = (
        db.query(models.User)
        .filter(models.User.referred_by_id == current_user.id)
        .count()
    )
    return schemas.ReferralStats(
        referral_code=current_user.referral_code or "",
        referrals_count=referrals_count,
        beri_earned=current_user.referral_beri_earned or 0,
    )
