from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON
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
    canon_bounty = Column(Float, nullable=True)
    status = Column(String, default="active")
    notes = Column(String, default="")
    rank = Column(String, default="")
    price_history = Column(JSON, default=list)
    img = Column(String, default="")
    bio = Column(String, default="")
    events = Column(String, default="")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    beri_balance = Column(Float, default=100_000)
    is_bot = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user_faction = Column(String, nullable=True)
    faction_history = Column(JSON, default=list)
    badges = Column(JSON, default=list)
    creature_unlocked = Column(Boolean, default=False)
    last_faction_change = Column(DateTime(timezone=True), nullable=True)

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
