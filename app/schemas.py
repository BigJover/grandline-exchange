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
    base_beri: Optional[float] = None
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
    is_admin: bool = False
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


# ── Leaderboard ──────────────────────────────────────────────────────────────

class LeaderboardEntry(BaseModel):
    rank: int
    username: str
    user_faction: Optional[str] = None
    beri_balance: float
    warlord_active: bool = False
    is_current: bool = False


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
    new_beri: float


class ShareOut(BaseModel):
    character_id: int
    quantity: int

    model_config = {"from_attributes": True}


# ── Casino ────────────────────────────────────────────────────────────────────

class PropositionCreate(BaseModel):
    question: str
    category: str = ""
    options: List[str]                  # min 2 options
    house_cut: float = 0.05
    closes_at: Optional[datetime] = None


class OptionOdds(BaseModel):
    index: int
    label: str
    total_bet: float
    implied_pct: float                  # % of pool on this option


class PropositionOut(BaseModel):
    id: int
    question: str
    category: str
    options: List[str]
    status: str
    house_cut: float
    closes_at: Optional[datetime]
    created_at: datetime
    resolved_at: Optional[datetime]
    correct_option: Optional[int]
    odds: List[OptionOdds]              # live pool breakdown
    total_pool: float
    user_bet_option: Optional[int]      # which option the requesting user backed
    user_bet_amount: Optional[float]

    model_config = {"from_attributes": True}


class BetRequest(BaseModel):
    proposition_id: int
    option_index: int
    amount: float


class BetOut(BaseModel):
    proposition_id: int
    option_index: int
    amount: float
    new_balance: float

    model_config = {"from_attributes": True}
