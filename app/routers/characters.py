import time
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/characters", tags=["characters"])

_CACHE_60 = {"Cache-Control": "public, max-age=60"}
_CHAR_CACHE_TTL = 60  # seconds — per-worker in-memory cache
_char_cache: dict = {"data": None, "ts": 0.0}


def invalidate_char_cache():
    """Call after any admin operation that changes character data."""
    _char_cache["data"] = None
    _char_cache["ts"] = 0.0


@router.get("/", response_model=List[schemas.CharacterListOut])
def list_characters(db: Session = Depends(get_db)):
    now = time.time()
    if _char_cache["data"] is None or now - _char_cache["ts"] > _CHAR_CACHE_TTL:
        chars = db.query(models.Character).order_by(models.Character.beri.desc()).all()
        _char_cache["data"] = [schemas.CharacterListOut.model_validate(c).model_dump() for c in chars]
        _char_cache["ts"] = now
    return JSONResponse(content=_char_cache["data"], headers=_CACHE_60)


@router.get("/{character_id}", response_model=schemas.CharacterOut)
def get_character(character_id: int, db: Session = Depends(get_db)):
    character = db.query(models.Character).filter(models.Character.id == character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    return character
