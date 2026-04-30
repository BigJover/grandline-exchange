"""
Ghost bot market makers.

seed_bots()    — idempotent startup seeding of bot accounts + share positions
run_bot_tick() — called once daily by the scheduler; applies a small, weighted
                 drift across the most active characters so prices evolve
                 organically even before the user base is large.  Changes are
                 intentionally subtle (~0.2–0.6 % per week per character).
"""

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

BOT_INITIAL_BERI = 100_000_000_000   # 100 B — not used for trading, just a placeholder
BOT_EMAIL_DOMAIN = "bot.grandline.internal"


def _seed_qty(beri: float) -> int:
    """Initial shares per bot per character, scaled by beri tier."""
    if beri >= 50_000_000: return 500
    if beri >= 10_000_000: return 200
    if beri >=  1_000_000: return 100
    return 20


# ── Seeding ───────────────────────────────────────────────────────────────────

def seed_bots():
    """
    Idempotent: create bot accounts and seed initial share holdings.
    Called once at startup — safe to run on every deploy.
    """
    for username, _bias in BOT_SPECS:
        db = SessionLocal()
        try:
            bot = db.query(models.User).filter(
                models.User.username == username
            ).first()

            if not bot:
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
                        quantity=_seed_qty(c.beri),
                    )
                    for c in characters
                ])
                db.commit()
                print(f"[Bots] Created {username} with {len(characters)} positions")

            else:
                # Bot exists — re-seed shares only if somehow wiped
                has_shares = db.query(models.Share).filter(
                    models.Share.user_id == bot.id
                ).first()
                if not has_shares:
                    characters = db.query(models.Character).all()
                    db.bulk_save_objects([
                        models.Share(
                            user_id=bot.id,
                            character_id=c.id,
                            quantity=_seed_qty(c.beri),
                        )
                        for c in characters
                    ])
                    db.commit()
                    print(f"[Bots] Re-seeded shares for {username}")

        except Exception as e:
            db.rollback()
            print(f"[Bots] {username}: {e}")
        finally:
            db.close()


# ── Daily drift ───────────────────────────────────────────────────────────────
# Price constants must stay in sync with trades.py
_IMPACT_PER_SHARE = 0.002
_IMPACT_CAP       = 0.05
_BERI_FLOOR       = 100_000

# Conservatively small — 15 chars/day, 1-2 shares per trade
# ≈ 0.2–0.6 % drift per week per actively-traded character
_TICK_CHARS = 15
_MAX_QTY    = 2
_MIN_QTY    = 1


def _weighted_sample(characters, k: int):
    """Sample k unique characters weighted by sqrt(beri) — favouring active stocks."""
    import math
    weights = [math.sqrt(max(c.beri, 1)) for c in characters]
    seen, result = set(), []
    for _ in range(k * 6):
        if len(result) >= k:
            break
        (pick,) = random.choices(characters, weights=weights, k=1)
        if pick.id not in seen:
            seen.add(pick.id)
            result.append(pick)
    return result


def _buy_probability(char: models.Character, bull_bias: float) -> float:
    """
    Base buy probability with mean reversion:
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
    Moves prices gently — real user trades will always have more impact.
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
            qty  = random.randint(_MIN_QTY, _MAX_QTY)

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

            price = max(0.01, round(char.beri / 100_000, 2))
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
            impact = min(_IMPACT_CAP, qty * _IMPACT_PER_SHARE)
            if action == "buy":
                char.beri = char.beri * (1 + impact)
            else:
                char.beri = max(_BERI_FLOOR, char.beri * (1 - impact))

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
