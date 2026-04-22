from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List, Any
from datetime import datetime
import json as _json


# ── Characters ──────────────────────────────────────────────────────────────

class PricePoint(BaseModel):
    chapter: int
    label: str
    beri: float


class CharacterOut(BaseModel):
    id: int
    name: str
    aliases: List[str]
    faction: str
    category: str
    beri: float
    canon_bounty: Optional[float]
    status: str
    rank: Optional[str]
    price_history: List[Any]
    img: str
    bio: str
    events: str

    model_config = {"from_attributes": True}


# ── Auth ─────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    beri_balance: float
    created_at: datetime
    user_faction: Optional[str] = None
    faction_history: List[str] = []
    badges: List[str] = []
    creature_unlocked: bool = False
    last_faction_change: Optional[datetime] = None
    warlord_until: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator("faction_history", "badges", mode="before")
    @classmethod
    def coerce_to_list(cls, v: Any) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            try:
                return _json.loads(v)
            except Exception:
                return []
        return list(v)

    @field_validator("creature_unlocked", mode="before")
    @classmethod
    def coerce_to_bool(cls, v: Any) -> bool:
        if v is None:
            return False
        return bool(v)


class FactionUpdate(BaseModel):
    faction: str


# ── Ledger ───────────────────────────────────────────────────────────────────

class LedgerEntry(BaseModel):
    uid: str
    entry_type: str
    amount: float
    description: str
    timestamp: datetime


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


# ── Trading ──────────────────────────────────────────────────────────────────

class TradeRequest(BaseModel):
    character_id: int
    action: str       # "buy" or "sell"
    quantity: int


class TradeResponse(BaseModel):
    beri_balance: float
    shares_held: int
    price_per_share: float


class ShareOut(BaseModel):
    character_id: int
    quantity: int

    model_config = {"from_attributes": True}
