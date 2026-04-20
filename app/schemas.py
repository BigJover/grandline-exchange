from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any
from datetime import datetime


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

    model_config = {"from_attributes": True}


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
