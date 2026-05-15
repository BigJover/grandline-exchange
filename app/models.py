from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Character(Base):
    __tablename__ = "characters"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    aliases = Column(JSON, default=list)
    faction = Column(String, default="")
    category = Column(String, default="")
    beri = Column(Float, nullable=False)
    base_beri = Column(Float, nullable=True)   # original seeded value, never updated
    canon_bounty = Column(Float, nullable=True)
    status = Column(String, default="active")
    notes = Column(String, default="")
    rank = Column(String, default="")
    price_history = Column(JSON, default=list)
    img = Column(String, default="")
    bio = Column(String, default="")
    events = Column(String, default="")
    sbs = Column(JSON, default=list)
    related = Column(JSON, default=list)
    full_name = Column(String, nullable=True)
    title = Column(String, nullable=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    beri_balance = Column(Float, default=100_000)
    is_bot = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user_faction = Column(String, nullable=True)
    faction_history = Column(JSON, default=list)
    badges = Column(JSON, default=list)
    creature_unlocked = Column(Boolean, default=False)
    last_faction_change = Column(DateTime(timezone=True), nullable=True)
    warlord_until = Column(DateTime(timezone=True), nullable=True)

    profile_picture = Column(String, nullable=True)          # URL or "char:<id>"
    referral_code = Column(String, unique=True, nullable=True)
    referred_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    referral_beri_earned = Column(Float, default=0)

    # Trivia
    trivia_streak = Column(Integer, default=0)               # consecutive full-week completions
    trivia_last_week = Column(String, nullable=True)         # ISO week of last full-correct week, e.g. "2026-W18"
    free_play_used_at = Column(DateTime(timezone=True), nullable=True)  # last chapter prediction free play

    shares = relationship("Share", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")
    beri_events = relationship("BeriEvent", back_populates="user")


class Share(Base):
    __tablename__ = "shares"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    quantity = Column(Integer, default=0)

    user = relationship("User", back_populates="shares")
    character = relationship("Character")


class BeriEvent(Base):
    __tablename__ = "beri_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_type = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(String, default="")
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="beri_events")


class CharacterRequest(Base):
    __tablename__ = "character_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String, default="")
    name = Column(String, nullable=False)
    aliases = Column(String, default="")
    faction = Column(String, default="")
    category = Column(String, default="")
    proposed_beri = Column(Float, default=0)
    canon_bounty = Column(Float, default=0)
    reason = Column(String, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PriceRequest(Base):
    __tablename__ = "price_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String, default="")
    character_name = Column(String, nullable=False)
    proposed_beri = Column(Float, nullable=False)
    reason = Column(String, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    username = Column(String, nullable=False)
    body = Column(String, nullable=False)
    likes = Column(Integer, default=0)
    week = Column(String, nullable=False)  # "2026-W17"
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CommentLike(Base):
    __tablename__ = "comment_likes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False)


class Proposition(Base):
    __tablename__ = "propositions"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(String, nullable=False)
    category = Column(String, default="")         # chapter / arc / slander / meta / endgame
    options = Column(JSON, default=list)           # ["Yes", "No"] or multi-choice
    correct_option = Column(Integer, nullable=True) # index into options; set on resolve
    status = Column(String, default="open")        # open | closed | resolved
    house_cut = Column(Float, default=0.05)        # fraction kept by the house
    closes_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Chapter prediction fields
    is_chapter_prediction = Column(Boolean, default=False)
    chapter_drop_time = Column(DateTime(timezone=True), nullable=True)  # when chapter releases
    is_break_week = Column(Boolean, default=False)

    bets = relationship("PropositionBet", back_populates="proposition")


class PropositionBet(Base):
    __tablename__ = "proposition_bets"

    id = Column(Integer, primary_key=True, index=True)
    proposition_id = Column(Integer, ForeignKey("propositions.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    option_index = Column(Integer, nullable=False)  # which option the user backed
    amount = Column(Float, nullable=False)
    payout = Column(Float, nullable=True)           # filled in when proposition resolves
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Chapter prediction bet metadata
    is_free_play = Column(Boolean, default=False)   # free daily prediction, no stake deducted
    multiplier = Column(Float, default=1.0)         # early prediction bonus multiplier
    penalty_amount = Column(Float, default=0)       # extra loss if wrong (late bets)
    doubled_down = Column(Boolean, default=False)   # break week 2× stake toggle
    sale_discount = Column(Float, default=0.0)      # house cut discount: 0.20 normal sale, 0.50 break week sale

    proposition = relationship("Proposition", back_populates="bets")
    user = relationship("User")


class TriviaQuestion(Base):
    __tablename__ = "trivia_questions"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String, nullable=False)       # lore | geography | backstory
    question = Column(String, nullable=False)
    correct_answer = Column(String, nullable=False)
    wrong_answers = Column(JSON, default=list)       # list of 3 strings
    active = Column(Boolean, default=True)
    week_assigned = Column(String, nullable=True)   # "2026-W18" — set when selected for a week
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    answers = relationship("TriviaAnswer", back_populates="question")


class TriviaAnswer(Base):
    __tablename__ = "trivia_answers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("trivia_questions.id"), nullable=False)
    week = Column(String, nullable=False)           # "2026-W18"
    is_correct = Column(Boolean, nullable=False)
    beri_change = Column(Float, nullable=False)     # positive = earned, negative = lost
    answered_at = Column(DateTime(timezone=True), server_default=func.now())

    question = relationship("TriviaQuestion", back_populates="answers")
    user = relationship("User")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    action = Column(String, nullable=False)  # "buy" or "sell"
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="transactions")
    character = relationship("Character")


class ChapterTransmission(Base):
    """Admin-published chapter transmission — the live content in the TRANSMISSION dropdown."""
    __tablename__ = "chapter_transmissions"

    id = Column(Integer, primary_key=True, index=True)
    chapter_number = Column(Integer, nullable=False)
    uplink_label = Column(String, default="")    # e.g. "Uplink: Ch.1182 ◈ Elbaf Node Active"
    summary = Column(Text, default="")
    movers = Column(JSON, default=list)          # [{"name": str, "direction": "up"|"down"|"new"}]
    reddit_context = Column(JSON, default=list)  # top post titles stored for reference
    published_at = Column(DateTime(timezone=True), server_default=func.now())


class DiscordEvent(Base):
    __tablename__ = "discord_events"

    id = Column(Integer, primary_key=True, index=True)
    channel = Column(String, default="")
    author = Column(String, default="")
    content = Column(Text, default="")
    characters_detected = Column(JSON, default=list)   # canonical character names found in message
    chapter_num = Column(Integer, nullable=True)        # chapter number detected, if any
    source_url = Column(String, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Chapter(Base):
    """A One Piece chapter detected via Reddit polling."""
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(Integer, unique=True, nullable=False, index=True)
    title = Column(String, default="")
    reddit_url = Column(String, default="")
    detected_at = Column(DateTime(timezone=True), server_default=func.now())
    processed = Column(Boolean, default=False)   # True once price proposals have been generated


class ProposedPriceChange(Base):
    """A price change proposed by the chapter pipeline, awaiting admin approval."""
    __tablename__ = "proposed_price_changes"

    id = Column(Integer, primary_key=True, index=True)
    chapter_number = Column(Integer, nullable=False, index=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=True)
    character_name = Column(String, nullable=False)
    current_beri = Column(Float, nullable=False)
    proposed_beri = Column(Float, nullable=False)
    direction = Column(String, nullable=False)   # "up" | "down"
    pct_change = Column(Float, nullable=False)   # e.g. 5.0 = +5%
    reason = Column(String, default="")
    status = Column(String, default="pending")   # pending | approved | dismissed
    created_at = Column(DateTime(timezone=True), server_default=func.now())
