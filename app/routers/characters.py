from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/characters", tags=["characters"])


@router.get("/", response_model=List[schemas.CharacterOut])
def list_characters(db: Session = Depends(get_db)):
    return db.query(models.Character).order_by(models.Character.beri.desc()).all()


@router.get("/{character_id}", response_model=schemas.CharacterOut)
def get_character(character_id: int, db: Session = Depends(get_db)):
    character = db.query(models.Character).filter(models.Character.id == character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    return character
