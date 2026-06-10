"""
Ghost bot market makers.

seed_bots()      — idempotent startup seeding of bot accounts + share positions.
                   Also tops up existing bots whose holdings fell below tier minimums.
run_bot_tick()   — called once daily by the scheduler; applies weighted drift so
                   prices evolve organically before a real user base exists.
reseed_bots()    — wipe all bot share holdings and rebuild from scratch (admin use).

Volatility tiers (by base_beri):
  Tier 1 (≥20 M):  top story chars — drift 3–5 shares/tick,  5 000 seed shares/bot
  Tier 2 (≥3 M):   relevant chars  — drift 2–3 shares/tick,  2 000 seed shares/bot
  Tier 3 (≥500 K): secondary cast  — drift 1–2 shares/tick,    800 seed shares/bot
  Tier 4 (< 500K): minor/defeated  — drift 1   share/tick,     200 seed shares/bot

Tick covers ~50 characters per day → every character drifts roughly once per week.
"""

import math
import random

from app.database import SessionLocal
from app import models

# ── Bot roster ────────────────────────────────────────────────────────────────

BOT_SPECS = [
    # (username,              bull_bias)  -1=full bear, 0=neutral, +1=full bull
    ("MarketBot_Sengoku",    0.0),
    ("MarketBot_Whitebeard", 0.3),
    ("MarketBot_Garp",       0.1),
    ("MarketBot_Roger",      0.5),
    ("MarketBot_Rayleigh",  -0.2),
]

BOT_INITIAL_BERI = 100_000_000_000   # 100 B — not used for gameplay, just placeholder
BOT_EMAIL_DOMAIN = "bot.grandline.internal"

# ── Volatility tiers ──────────────────────────────────────────────────────────

def _vol_tier(beri: float) -> int:
    """Return volatility tier 1–4 based on character beri value."""
    if beri >= 20_000_000: return 1
    if beri >=  3_000_000: return 2
    if beri >=    500_000: return 3
    return 4

# Seed shares per bot per character, and max qty per tick trade
_TIER_SEED = {1: 5_000, 2: 2_000, 3: 800, 4: 200}
_TIER_QTY  = {1: (3, 5), 2: (2, 3), 3: (1, 2), 4: (1, 1)}


def _seed_qty(beri: float) -> int:
    return _TIER_SEED[_vol_tier(beri)]


# ── Seeding ───────────────────────────────────────────────────────────────────

def seed_bots():
    """
    Idempotent: create bot accounts and seed/top-up share holdings.
    Safe to call on every deploy — only adds missing shares, never removes.
    """
    for username, _bias in BOT_SPECS:
        db = SessionLocal()
        try:
            bot = db.query(models.User).filter(
                models.User.username == username
            ).first()

            if not bot:
                # First time — create account and full share positions
                bot = models.User(
                    username=username,
                    email=f"{username}@{BOT_EMAIL_DOMAIN}",
                    password_hash="__bot__",
                    beri_balance=BOT_INITIAL_BERI,
                    is_bot=True,
                )
                db.add(bot)
                db.flush()
                characters = db.query(models.Character).all()
                db.bulk_save_objects([
                    models.Share(
                        user_id=bot.id,
                        character_id=c.id,
                        quantity=_seed_qty(c.base_beri or c.beri),
                    )
                    for c in characters
                ])
                db.commit()
                print(f"[Bots] Created {username} with {len(characters)} positions")

            else:
                # Bot exists — top up any positions below the tier minimum
                characters = db.query(models.Character).all()
                char_map = {c.id: c for c in characters}
                existing = {
                    s.character_id: s
                    for s in db.query(models.Share).filter(
                        models.Share.user_id == bot.id
                    ).all()
                }
                topped, created = 0, 0
                for c in characters:
                    target = _seed_qty(c.base_beri or c.beri)
                    share = existing.get(c.id)
                    if share is None:
                        db.add(models.Share(
                            user_id=bot.id,
                            character_id=c.id,
                            quantity=target,
                        ))
                        created += 1
                    elif share.quantity < target:
                        share.quantity = target
                        topped += 1
                if created or topped:
                    db.commit()
                    print(f"[Bots] {username}: topped up {topped} positions, created {created} new")

        except Exception as e:
            db.rollback()
            print(f"[Bots] {username}: {e}")
        finally:
            db.close()


def reseed_bots():
    """
    Hard reseed: wipe all bot share holdings and rebuild from tier targets.
    Called by admin endpoint — not run automatically.
    """
    db = SessionLocal()
    try:
        bot_usernames = [u for u, _ in BOT_SPECS]
        bots = db.query(models.User).filter(
            models.User.username.in_(bot_usernames)
        ).all()
        if not bots:
            print("[Bots] No bot accounts found — run seed_bots() first")
            return

        bot_ids = [b.id for b in bots]
        deleted = db.query(models.Share).filter(
            models.Share.user_id.in_(bot_ids)
        ).delete(synchronize_session=False)
        db.flush()

        characters = db.query(models.Character).all()
        new_shares = []
        for bot in bots:
            for c in characters:
                new_shares.append(models.Share(
                    user_id=bot.id,
                    character_id=c.id,
                    quantity=_seed_qty(c.base_beri or c.beri),
                ))
        db.bulk_save_objects(new_shares)
        db.commit()
        print(f"[Bots] Reseed complete — wiped {deleted} rows, wrote {len(new_shares)} new")
    except Exception as e:
        db.rollback()
        print(f"[Bots] Reseed error: {e}")
    finally:
        db.close()


# ── Daily drift ───────────────────────────────────────────────────────────────
# Price constants stay in sync with trades.py
_IMPACT_PER_SHARE = 0.002
_IMPACT_CAP       = 0.05
_BERI_FLOOR       = 100_000

# 50 chars/day → every char drifts ~once per week across 342 chars
_TICK_CHARS = 50


def _weighted_sample(characters, k: int):
    """
    Sample k unique characters weighted by sqrt(beri).
    Tier-1 chars get picked more often but not exclusively.
    """
    weights = [math.sqrt(max(c.beri, 1)) for c in characters]
    seen, result = set(), []
    for _ in range(k * 8):
        if len(result) >= k:
            break
        (pick,) = random.choices(characters, weights=weights, k=1)
        if pick.id not in seen:
            seen.add(pick.id)
            result.append(pick)
    return result


def _buy_probability(char: models.Character, bull_bias: float) -> float:
    """
    Buy probability with mean reversion:
    pumped stocks (>1.5× base) get sell pressure, dipped stocks (<0.7× base) get buy pressure.
    """
    p = 0.55 + bull_bias * 0.3
    if char.base_beri and char.base_beri > 0:
        ratio = char.beri / char.base_beri
        if ratio > 1.5:   p -= 0.20
        elif ratio < 0.7: p += 0.20
    return max(0.10, min(0.90, p))


def run_bot_tick():
    """
    Execute one daily round of ghost-bot trades.
    Each character gets a tier-appropriate qty so high-profile chars
    move more per tick. Moves are still subtle — real user trades
    always have more impact.
    """
    db = SessionLocal()
    try:
        bots = db.query(models.User).filter(models.User.is_bot == True).all()
        if not bots:
            return

        characters = db.query(models.Character).all()
        if not characters:
            return

        bias_map = {username: bias for username, bias in BOT_SPECS}
        selected = _weighted_sample(characters, min(_TICK_CHARS, len(characters)))
        price_push = []

        for char in selected:
            bot  = random.choice(bots)
            bias = bias_map.get(bot.username, 0.0)

            tier = _vol_tier(char.base_beri or char.beri)
            min_q, max_q = _TIER_QTY[tier]
            qty = random.randint(min_q, max_q)

            action = "buy" if random.random() < _buy_probability(char, bias) else "sell"

            # Can't sell shares we don't hold
            if action == "sell":
                share = db.query(models.Share).filter(
                    models.Share.user_id == bot.id,
                    models.Share.character_id == char.id,
                ).first()
                held = share.quantity if share else 0
                if held <= 0:
                    action = "buy"
                elif held < qty:
                    qty = held

            # Same post-impact execution pricing as trades.py
            impact = min(_IMPACT_CAP, qty * _IMPACT_PER_SHARE)
            if action == "buy":
                new_beri = char.beri * (1 + impact)
            else:
                new_beri = max(_BERI_FLOOR, char.beri * (1 - impact))
            price = max(0.01, round(new_beri / 100_000, 2))
            cost  = price * qty

            if action == "buy":
                if bot.beri_balance < cost:
                    continue
                bot.beri_balance -= cost
                share = db.query(models.Share).filter(
                    models.Share.user_id == bot.id,
                    models.Share.character_id == char.id,
                ).first()
                if share:
                    share.quantity += qty
                else:
                    db.add(models.Share(
                        user_id=bot.id, character_id=char.id, quantity=qty
                    ))
            else:
                bot.beri_balance += cost
                share = db.query(models.Share).filter(
                    models.Share.user_id == bot.id,
                    models.Share.character_id == char.id,
                ).first()
                if share:
                    share.quantity -= qty

            # Move the market — same formula as trades.py
            char.beri = new_beri

            price_push.append({"id": char.id, "beri": char.beri})

        db.commit()

        # Push to WebSocket broadcaster
        from app.price_queue import updates as _q
        for upd in price_push:
            try:
                _q.put_nowait(upd)
            except Exception:
                pass

        print(f"[BotTick] {len(price_push)} trades fired")

    except Exception as e:
        db.rollback()
        print(f"[BotTick] Error: {e}")
    finally:
        db.close()
