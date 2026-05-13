from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List, Any
from datetime import datetime
import json as _json


# ── Characters ──────────────────────────────────────────────────────────────

class PricePoint(BaseModel):
    chapter: int
    label: str
    beri: float


class CharacterListOut(BaseModel):
    """Slim schema for the list endpoint — excludes bio/events/sbs/related."""
    id: int
    name: str
    aliases: List[str]
    faction: str
    category: str
    beri: float
    base_beri: Optional[float] = None
    canon_bounty: Optional[float] = None
    status: str
    rank: Optional[str] = None
    price_history: List[Any]
    img: str
    full_name: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None

    @field_validator('aliases', 'price_history', mode='before')
    @classmethod
    def coerce_none_to_list(cls, v):
        return v if v is not None else []

    model_config = {"from_attributes": True}


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
    sbs: List[Any] = []
    related: List[str] = []
    full_name: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None

    @field_validator('sbs', 'related', 'aliases', 'price_history', mode='before')
    @classmethod
    def coerce_none_to_list(cls, v):
        return v if v is not None else []

    model_config = {"from_attributes": True}


# ── Auth ─────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    referral_code: Optional[str] = None


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
    profile_picture: Optional[str] = None
    referral_code: Optional[str] = None
    referred_by_id: Optional[int] = None
    referral_beri_earned: float = 0
    free_play_used_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator("referral_beri_earned", mode="before")
    @classmethod
    def coerce_earned_to_float(cls, v: Any) -> float:
        if v is None:
            return 0.0
        return float(v)

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

class TransmissionPublish(BaseModel):
    chapter_number: int
    uplink_label: str          # e.g. "Uplink: Ch.1182 ◈ Elbaf Node Active"
    summary: str
    movers: List[dict]         # [{"name": str, "direction": "up"|"down"|"new"}]
    reddit_context: List[str] = []


class PropositionCreate(BaseModel):
    question: str
    category: str = ""
    options: List[str]                  # min 2 options
    house_cut: float = 0.05
    closes_at: Optional[datetime] = None
    is_chapter_prediction: bool = False
    chapter_drop_time: Optional[datetime] = None
    is_break_week: bool = False


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
    is_chapter_prediction: bool = False
    chapter_drop_time: Optional[datetime] = None
    is_break_week: bool = False
    user_bet_multiplier: Optional[float] = None
    user_bet_is_free_play: bool = False
    user_bet_doubled_down: bool = False
    user_bet_penalty_amount: float = 0.0
    sale_discount: float = 0.0        # active sale discount fraction (0.0 = no sale)

    model_config = {"from_attributes": True}


class BetRequest(BaseModel):
    proposition_id: int
    option_index: int
    amount: float
    is_free_play: bool = False
    doubled_down: bool = False


class BetOut(BaseModel):
    proposition_id: int
    option_index: int
    amount: float
    new_balance: float
    multiplier: float = 1.0
    is_free_play: bool = False

    model_config = {"from_attributes": True}


# ── Trivia ────────────────────────────────────────────────────────────────────

class TriviaQuestionOut(BaseModel):
    id: int
    category: str
    question: str
    choices: List[str]                  # shuffled list of 4 options, no labels
    week: str
    already_answered: bool = False
    answered_correctly: Optional[bool] = None

    model_config = {"from_attributes": True}


class TriviaAnswerRequest(BaseModel):
    question_id: int
    answer: str                         # must match one of the shuffled choices exactly


class TriviaAnswerOut(BaseModel):
    correct: bool
    beri_change: float
    new_balance: float
    streak: int
    multiplier: float
    week_complete: bool


class TriviaStreakOut(BaseModel):
    streak: int
    multiplier: float
    last_week: Optional[str]
    this_week_answers: int              # 0-3 answered so far this week
    this_week_correct: int              # 0-3 correct so far


# ── Profile ───────────────────────────────────────────────────────────────────

class ProfilePictureUpdate(BaseModel):
    profile_picture: str   # URL or "char:<id>"


class UsernameUpdate(BaseModel):
    username: str


class ReferralStats(BaseModel):
    referral_code: str
    referrals_count: int
    beri_earned: float


class DiscordEventIn(BaseModel):
    channel: str = ""
    author: str = ""
    content: str
    source_url: Optional[str] = None


class PortfolioEntry(BaseModel):
    character_id: int
    character_name: str
    character_img: str
    quantity: int
    current_price: float
    total_value: float

    model_config = {"from_attributes": True}


class ProfileFull(BaseModel):
    user: UserOut
    portfolio: List[PortfolioEntry]
    leaderboard_rank: int
    total_users: int
    referral_stats: ReferralStats
