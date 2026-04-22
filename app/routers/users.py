from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas, auth

router = APIRouter(prefix="/users", tags=["users"])

VALID_FACTIONS = {"marine", "royalty", "pirate", "citizen", "revolutionary", "creature"}

# If a user has ever been in key faction, these target factions are permanently blocked
FACTION_BLOCKS = {
    "pirate":        ["marine"],
    "revolutionary": ["marine", "royalty"],
    "marine":        ["pirate", "revolutionary"],
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

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user
