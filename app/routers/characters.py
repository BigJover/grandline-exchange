import time
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/characters", tags=["characters"])

_CACHE_HEADERS = {"Cache-Control": "private, max-age=30"}
_CHAR_CACHE_TTL = 30  # 30s — keeps browser cache short so price updates land quickly
_char_cache: dict        = {"data": None, "ts": 0.0}
_ticker_cache: dict      = {"data": None, "ts": 0.0}
_detail_cache: dict[int, tuple] = {}   # id → (payload_dict, timestamp)
_DETAIL_TTL = 30  # seconds — short TTL; invalidated on trade/admin


def invalidate_char_cache():
    """Call after any admin operation that changes character data."""
    _char_cache["data"]   = None
    _char_cache["ts"]     = 0.0
    _ticker_cache["data"] = None
    _ticker_cache["ts"]   = 0.0
    _detail_cache.clear()


def invalidate_detail(character_id: int):
    """Call after a trade so the detail endpoint reflects the new price."""
    _detail_cache.pop(character_id, None)


@router.get("/ticker")
def ticker_data(db: Session = Depends(get_db)):
    """Slim payload for the live-feed ticker on casino/community pages.
    Returns only the fields chartStats() needs: id, name, beri, base_beri,
    and the first + last price_history entries (enough for trend direction)."""
    now = time.time()
    if _ticker_cache["data"] is None or now - _ticker_cache["ts"] > _CHAR_CACHE_TTL:
        chars = db.query(
            models.Character.id,
            models.Character.name,
            models.Character.beri,
            models.Character.base_beri,
            models.Character.price_history,
        ).all()
        result = []
        for c in chars:
            ph = c.price_history or []
            slim_ph = ([ph[0]] if ph else []) + ([ph[-1]] if len(ph) > 1 else [])
            result.append({
                "id":           c.id,
                "name":         c.name,
                "beri":         c.beri,
                "base_beri":    c.base_beri,
                "price_history": slim_ph,
            })
        _ticker_cache["data"] = result
        _ticker_cache["ts"]   = now
    return JSONResponse(content=_ticker_cache["data"], headers=_CACHE_HEADERS)


@router.get("/", response_model=List[schemas.CharacterListOut])
def list_characters(db: Session = Depends(get_db)):
    now = time.time()
    if _char_cache["data"] is None or now - _char_cache["ts"] > _CHAR_CACHE_TTL:
        chars = db.query(models.Character).order_by(models.Character.beri.desc()).all()
        _char_cache["data"] = [schemas.CharacterListOut.model_validate(c).model_dump() for c in chars]
        _char_cache["ts"] = now
    return JSONResponse(content=_char_cache["data"], headers=_CACHE_HEADERS)


@router.get("/{character_id}", response_model=schemas.CharacterOut)
def get_character(character_id: int, db: Session = Depends(get_db)):
    now = time.time()
    cached = _detail_cache.get(character_id)
    if cached and now - cached[1] < _DETAIL_TTL:
        return JSONResponse(content=cached[0])
    character = db.query(models.Character).filter(models.Character.id == character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")
    payload = schemas.CharacterOut.model_validate(character).model_dump(mode="json")
    _detail_cache[character_id] = (payload, now)
    return JSONResponse(content=payload)
