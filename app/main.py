import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import os

limiter = Limiter(key_func=get_remote_address)

from app.database import Base, engine, SessionLocal, get_db
from app import models
from app.routers import auth, characters, trades, admin, users, requests as requests_router, comments as comments_router, casino as casino_router, profile as profile_router, trivia as trivia_router
from app.scheduler import scheduler
from app.websocket_manager import manager
from app.price_queue import updates as price_updates


def run_column_migrations():
    """Add new columns to existing tables without Alembic. Safe to run on every startup."""
    from sqlalchemy import text
    is_postgres = str(engine.url).startswith("postgres")
    user_migrations = [
        ("user_faction",       "TEXT"),
        ("faction_history",    "TEXT DEFAULT '[]'"),
        ("badges",             "TEXT DEFAULT '[]'"),
        ("creature_unlocked",  "BOOLEAN DEFAULT FALSE" if is_postgres else "INTEGER DEFAULT 0"),
        ("last_faction_change", "TIMESTAMPTZ" if is_postgres else "DATETIME"),
        ("warlord_until",       "TIMESTAMPTZ" if is_postgres else "DATETIME"),
        ("is_admin",           "BOOLEAN DEFAULT FALSE" if is_postgres else "INTEGER DEFAULT 0"),
        ("profile_picture",    "TEXT"),
        ("referral_code",      "TEXT"),
        ("referred_by_id",     "INTEGER"),
        ("referral_beri_earned", "DOUBLE PRECISION DEFAULT 0" if is_postgres else "FLOAT DEFAULT 0"),
        ("trivia_streak",      "INTEGER DEFAULT 0"),
        ("trivia_last_week",   "TEXT"),
        ("free_play_used_at",  "TIMESTAMPTZ" if is_postgres else "DATETIME"),
    ]
    char_migrations = [
        ("base_beri", "DOUBLE PRECISION" if is_postgres else "FLOAT"),
        ("sbs",       "JSON"             if is_postgres else "TEXT"),
        ("related",   "JSON"             if is_postgres else "TEXT"),
        ("full_name", "TEXT"),
        ("title",     "TEXT"),
    ]
    char_req_migrations = [
        ("discord_message_id", "TEXT"),
    ]
    proposition_migrations = [
        ("is_chapter_prediction", "BOOLEAN DEFAULT FALSE" if is_postgres else "INTEGER DEFAULT 0"),
        ("chapter_drop_time",     "TIMESTAMPTZ" if is_postgres else "DATETIME"),
        ("is_break_week",         "BOOLEAN DEFAULT FALSE" if is_postgres else "INTEGER DEFAULT 0"),
        ("source_chapter",        "INTEGER"),
        ("llm_confidence",        "DOUBLE PRECISION" if is_postgres else "FLOAT"),
        ("llm_reasoning",         "TEXT"),
    ]
    proposed_price_migrations = [
        ("signal_scores", "JSON" if is_postgres else "TEXT"),
    ]
    chapter_migrations = [
        ("next_is_break", "BOOLEAN DEFAULT FALSE" if is_postgres else "INTEGER DEFAULT 0"),
    ]
    prop_bet_migrations = [
        ("is_free_play",    "BOOLEAN DEFAULT FALSE" if is_postgres else "INTEGER DEFAULT 0"),
        ("multiplier",      "DOUBLE PRECISION DEFAULT 1.0" if is_postgres else "FLOAT DEFAULT 1.0"),
        ("penalty_amount",  "DOUBLE PRECISION DEFAULT 0" if is_postgres else "FLOAT DEFAULT 0"),
        ("doubled_down",    "BOOLEAN DEFAULT FALSE" if is_postgres else "INTEGER DEFAULT 0"),
        ("sale_discount",   "DOUBLE PRECISION DEFAULT 0.0" if is_postgres else "FLOAT DEFAULT 0.0"),
    ]
    with engine.connect() as conn:
        for col, col_type in user_migrations:
            try:
                if is_postgres:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                else:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        for col, col_type in char_migrations:
            try:
                if is_postgres:
                    conn.execute(text(f"ALTER TABLE characters ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                else:
                    conn.execute(text(f"ALTER TABLE characters ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        for col, col_type in char_req_migrations:
            try:
                if is_postgres:
                    conn.execute(text(f"ALTER TABLE character_requests ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                else:
                    conn.execute(text(f"ALTER TABLE character_requests ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        for col, col_type in proposition_migrations:
            try:
                if is_postgres:
                    conn.execute(text(f"ALTER TABLE propositions ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                else:
                    conn.execute(text(f"ALTER TABLE propositions ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        for col, col_type in chapter_migrations:
            try:
                if is_postgres:
                    conn.execute(text(f"ALTER TABLE chapters ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                else:
                    conn.execute(text(f"ALTER TABLE chapters ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        for col, col_type in proposed_price_migrations:
            try:
                if is_postgres:
                    conn.execute(text(f"ALTER TABLE proposed_price_changes ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                else:
                    conn.execute(text(f"ALTER TABLE proposed_price_changes ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        for col, col_type in prop_bet_migrations:
            try:
                if is_postgres:
                    conn.execute(text(f"ALTER TABLE proposition_bets ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                else:
                    conn.execute(text(f"ALTER TABLE proposition_bets ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass


def backfill_base_beri():
    """Populate base_beri from seed JSON for characters that don't have it yet.
    Runs once per new character row — subsequent calls are no-ops."""
    import json as _json
    from sqlalchemy import text
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "characters.json")
    try:
        with open(data_path) as f:
            chars = _json.load(f)
        seed_beri = {c["name"]: float(c.get("beri", 0)) for c in chars if c.get("beri")}
    except Exception:
        return
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, name FROM characters WHERE base_beri IS NULL")).fetchall()
        for row in rows:
            original = seed_beri.get(row[1])
            if original:
                conn.execute(
                    text("UPDATE characters SET base_beri = :b WHERE id = :id"),
                    {"b": original, "id": row[0]},
                )
        conn.commit()


def patch_character_meta():
    """Sync character data from seed files on every startup.
    - bio: only fills if empty (set once, never overwritten)
    - events, sbs, related: always synced from character_meta.json
    - price_history: merges new chapter entries without overwriting existing
    - beri: NOT touched — the live DB value is the source of truth
    - full_name, title: always synced from characters.json
    """
    import json as _json
    meta_path = os.path.join(os.path.dirname(__file__), "..", "data", "character_meta.json")
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "characters.json")
    try:
        with open(meta_path) as f:
            meta = _json.load(f)
        with open(data_path) as f:
            seed = {c["name"]: c for c in _json.load(f)}
    except Exception:
        return
    db = SessionLocal()
    try:
        chars = db.query(models.Character).all()
        for char in chars:
            m = meta.get(char.name)
            if m:
                if not char.bio:
                    char.bio = m.get("bio", "")
                # Merge events: meta file is the base, but lines that exist only in
                # the DB (chapter-pipeline appends, admin appends) must survive deploys.
                meta_events = m.get("events", "")
                existing_events = char.events or ""
                if existing_events and existing_events != meta_events:
                    meta_lines = set(meta_events.split("\n"))
                    extra = [
                        ln for ln in existing_events.split("\n")
                        if ln.strip() and ln not in meta_lines
                    ]
                    char.events = (
                        meta_events + ("\n" + "\n".join(extra) if extra else "")
                    ).strip()
                else:
                    char.events = meta_events
                char.sbs     = m.get("sbs", [])
                char.related = m.get("related", [])
            s = seed.get(char.name)
            if s:
                char.full_name = s.get("full_name") or None
                char.title     = s.get("title") or None
                # Merge new price_history entries (identified by chapter number)
                existing_chapters = {
                    e["chapter"] for e in (char.price_history or [])
                    if isinstance(e, dict) and "chapter" in e
                }
                new_entries = [
                    e for e in s.get("price_history", [])
                    if isinstance(e, dict) and e.get("chapter") not in existing_chapters
                ]
                if new_entries:
                    char.price_history = (char.price_history or []) + new_entries
                    # beri is intentionally NOT updated here — the live DB value
                    # is the source of truth and evolves through user/bot trading.
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"patch_character_meta error: {e}")
    finally:
        db.close()


def backfill_referral_codes():
    """Assign referral codes to any existing users who don't have one yet."""
    import uuid as _uuid
    db = SessionLocal()
    try:
        users = db.query(models.User).filter(models.User.referral_code == None).all()
        for u in users:
            code = _uuid.uuid4().hex[:8].upper()
            while db.query(models.User).filter(models.User.referral_code == code).first():
                code = _uuid.uuid4().hex[:8].upper()
            u.referral_code = code
        if users:
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"backfill_referral_codes error: {e}")
    finally:
        db.close()


def run_index_migrations():
    """Create missing indexes on FK and frequently-filtered columns. Safe on every startup."""
    from sqlalchemy import text
    indexes = [
        ("ix_shares_user_id",              "shares",           "user_id"),
        ("ix_shares_character_id",         "shares",           "character_id"),
        ("ix_transactions_user_id",        "transactions",     "user_id"),
        ("ix_transactions_character_id",   "transactions",     "character_id"),
        ("ix_beri_events_user_id",         "beri_events",      "user_id"),
        ("ix_users_beri_balance",          "users",            "beri_balance"),
        ("ix_users_is_bot",                "users",            "is_bot"),
        ("ix_comments_character_id",       "comments",         "character_id"),
        ("ix_comments_week",               "comments",         "week"),
        ("ix_comment_likes_user_id",       "comment_likes",    "user_id"),
        ("ix_comment_likes_comment_id",    "comment_likes",    "comment_id"),
        ("ix_prop_bets_proposition_id",    "proposition_bets", "proposition_id"),
        ("ix_prop_bets_user_id",           "proposition_bets", "user_id"),
        ("ix_characters_beri",             "characters",       "beri"),
    ]
    is_postgres = str(engine.url).startswith("postgres")
    with engine.connect() as conn:
        for idx_name, table, col in indexes:
            try:
                if is_postgres:
                    conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({col})"
                    ))
                else:
                    conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({col})"
                    ))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass


def seed_new_characters():
    """Insert any characters present in characters.json but missing from the DB.
    Safe to run on every startup — skips characters that already exist by name."""
    import json as _json
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "characters.json")
    try:
        with open(data_path) as f:
            chars = _json.load(f)
    except Exception:
        return
    db = SessionLocal()
    try:
        existing = {r[0] for r in db.execute(models.Character.__table__.select()).fetchall()
                    if hasattr(r, '__iter__')}
        # Use name-based lookup to be safe
        existing_names = {c.name for c in db.query(models.Character.name).all()}
        added = 0
        for c in chars:
            if c.get("name") in existing_names:
                continue
            db.add(models.Character(
                name=c.get("name", ""),
                aliases=c.get("aliases", []),
                faction=c.get("faction", ""),
                category=c.get("category", ""),
                beri=float(c.get("beri", 0)),
                canon_bounty=c.get("canon_bounty"),
                status=c.get("status", "active"),
                notes=c.get("notes", ""),
                rank=c.get("rank"),
                price_history=c.get("price_history", []),
                img=c.get("img", ""),
                bio=c.get("bio", ""),
                events=c.get("events", ""),
                sbs=c.get("sbs", []),
                related=c.get("related", []),
                full_name=c.get("full_name"),
                title=c.get("title"),
            ))
            added += 1
        if added:
            db.commit()
            print(f"[seed_new_characters] Added {added} new character(s)")
    except Exception as e:
        db.rollback()
        print(f"[seed_new_characters] Error: {e}")
    finally:
        db.close()


run_column_migrations()
Base.metadata.create_all(bind=engine)
run_index_migrations()
backfill_base_beri()
patch_character_meta()
backfill_referral_codes()
seed_new_characters()

from app.bots import seed_bots
seed_bots()


def seed_trivia_questions():
    """Insert trivia questions from data/trivia_questions.json that aren't already in the DB.
    Identified by question text — safe to run on every startup."""
    import json as _json
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "trivia_questions.json")
    try:
        with open(data_path) as f:
            questions = _json.load(f)
    except Exception:
        return
    db = SessionLocal()
    try:
        existing = {r[0] for r in db.query(models.TriviaQuestion.question).all()}
        added = 0
        for q in questions:
            if q.get("question") in existing:
                continue
            db.add(models.TriviaQuestion(
                category=q.get("category", "lore"),
                question=q.get("question", ""),
                correct_answer=q.get("correct", ""),
                wrong_answers=q.get("wrong", []),
                active=q.get("active", True),
            ))
            added += 1
        if added:
            db.commit()
            print(f"[seed_trivia_questions] Added {added} new question(s)")
    except Exception as e:
        db.rollback()
        print(f"[seed_trivia_questions] Error: {e}")
    finally:
        db.close()


seed_trivia_questions()


def seed_character_drop_proposals():
    """Insert Ch.1184 character drop proposals (Reuven, Candelle) if not already present."""
    db = SessionLocal()
    try:
        proposals = [
            {
                "chapter_number": 1184,
                "name": "King Reuven",
                "faction": "Esperia Kingdom",
                "category": "Royalty",
                "proposed_beri": 2_000_000_000,
                "bio": "The king of the Esperia Kingdom and Brook's former liege. Brook served him directly and considered him the source of everything he had. Revealed in a flashback during Ch.1184.",
                "events": "Ch.1184 — Debut | Appears in Brook's Esperia Kingdom flashback as the king Brook swore loyalty to.",
                "reason": "Ch.1184 debut — Brook flashback, Esperia Kingdom",
            },
            {
                "chapter_number": 1184,
                "name": "Queen Candelle",
                "faction": "Esperia Kingdom",
                "category": "Royalty",
                "proposed_beri": 2_000_000_000,
                "bio": "A figure of tremendous power from the Esperia Kingdom, connected to Brook's past. In the present day she appears in a private meeting with a Gorosei member, suggesting ties to the World Government that predate her public persona.",
                "events": "Ch.1184 — Debut | Appears in Brook's Esperia Kingdom flashback. Also seen in a present-day meeting with one of the Gorosei (Saturn), hinting at a deep World Government connection.\nAnalyst Note: Popular community theory — Candelle may be related to Dracule Mihawk. The physical profile and her willingness to meet privately with the Gorosei has led to widespread speculation about a Mihawk bloodline link, possibly placing her within the lineage that produced the world's greatest swordsman.",
                "reason": "Ch.1184 debut — Brook flashback + present-day Gorosei meeting",
            },
        ]
        existing_names = {
            r[0] for r in db.query(models.ProposedNewCharacter.name).filter(
                models.ProposedNewCharacter.chapter_number == 1184
            ).all()
        }
        known_chars = {r[0] for r in db.query(models.Character.name).all()}
        added = 0
        for p in proposals:
            if p["name"] in existing_names or p["name"] in known_chars:
                continue
            db.add(models.ProposedNewCharacter(**p))
            added += 1
        if added:
            db.commit()
            print(f"[seed_character_drop_proposals] Added {added} proposed new character(s)")
    except Exception as e:
        db.rollback()
        print(f"[seed_character_drop_proposals] Error: {e}")
    finally:
        db.close()


seed_character_drop_proposals()


def correct_beri_outliers():
    """One-time corrections for characters whose seeded beri was miscalibrated.
    Each entry fires at most once ever — applied corrections are recorded in bot_kv
    so restarts never re-apply them and wipe trading-driven price movement.

    List intentionally empty: all price changes go through the admin panel now.
    The ch.1181/1182 calibration entries were applied in production long ago and
    were re-firing on every deploy, resetting Dragon/Arlong/Luffy. Add an entry
    here only for a deliberate one-off correction — it will apply exactly once."""
    from sqlalchemy import text
    corrections: list = [
        # (name, new_beri, only_if_above)
    ]
    with engine.connect() as conn:
        for name, target, threshold in corrections:
            try:
                kv_key = f"beri_correction:{name}:{int(target)}"
                done = conn.execute(
                    text("SELECT 1 FROM bot_kv WHERE key = :k"), {"k": kv_key}
                ).fetchone()
                if done:
                    continue
                row = conn.execute(
                    text("SELECT beri FROM characters WHERE name = :n"), {"n": name}
                ).fetchone()
                if row and row[0] > threshold:
                    conn.execute(
                        text("UPDATE characters SET beri = :b, base_beri = :b WHERE name = :n"),
                        {"b": float(target), "n": name},
                    )
                    print(f"[Correction] {name}: {int(row[0]):,} → {target:,}")
                # Mark handled either way — if the live value is already at/below the
                # threshold the correction is unnecessary, permanently.
                conn.execute(
                    text("INSERT INTO bot_kv (key, value) VALUES (:k, '1')"), {"k": kv_key}
                )
                conn.commit()
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                print(f"[Correction] {name}: {e}")


correct_beri_outliers()


def patch_ch1181_price_history():
    """One-time patch to update ch.1181 price_history labels for Luffy and Loki."""
    label_patches = {
        "Luffy": {"chapter": 1181, "new_label": "Monster Trio / Fighting Seriously", "new_beri": 4_500_000_000},
        "Loki":  {"chapter": 1181, "new_label": "Nidhogg + Monster Trio"},
    }
    db = SessionLocal()
    try:
        for name, patch in label_patches.items():
            char = db.query(models.Character).filter(models.Character.name == name).first()
            if not char or not char.price_history:
                continue
            ph = list(char.price_history)
            changed = False
            for entry in ph:
                if isinstance(entry, dict) and entry.get("chapter") == patch["chapter"]:
                    if entry.get("label") != patch["new_label"]:
                        entry["label"] = patch["new_label"]
                        changed = True
                    if "new_beri" in patch and entry.get("beri") != patch["new_beri"]:
                        entry["beri"] = patch["new_beri"]
                        changed = True
            if changed:
                char.price_history = ph
                db.commit()
                print(f"[patch_ch1181] Updated {name} ch.1181 label")
    except Exception as e:
        db.rollback()
        print(f"[patch_ch1181] Error: {e}")
    finally:
        db.close()


patch_ch1181_price_history()

# Cache HTML pages in memory at startup — avoids disk read on every request
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
_html_cache: dict[str, str] = {}
for _page in ("index.html", "community.html", "casino.html", "profile.html", "admin.html"):
    try:
        with open(os.path.join(STATIC_DIR, _page)) as _f:
            _html_cache[_page] = _f.read()
    except Exception:
        pass


async def price_broadcaster():
    """Every 5 s: drain trade-triggered updates and broadcast.
       Every 60 s: send a full price snapshot — only when clients are connected."""
    from sqlalchemy import text as _text

    def _snapshot() -> dict:
        # Runs in a worker thread — the sync DB call must NEVER run on the event
        # loop: it froze all in-flight requests for seconds on every 60 s tick.
        with engine.connect() as conn:
            rows = conn.execute(_text("SELECT id, beri FROM characters")).fetchall()
            return {str(r[0]): r[1] for r in rows}

    snapshot_every = 12   # 12 × 5 s = 60 s between snapshots
    tick = 0
    while True:
        await asyncio.sleep(5)
        tick += 1

        # Skip all work when nobody is listening
        if not manager.active:
            tick = 0
            continue

        batch = {}

        # Drain queue of trade-triggered updates
        while not price_updates.empty():
            try:
                u = price_updates.get_nowait()
                batch[str(u["id"])] = u["beri"]
            except Exception:
                break

        # Full snapshot every 60 s — off the event loop
        if tick >= snapshot_every:
            tick = 0
            try:
                batch = await asyncio.to_thread(_snapshot)
            except Exception as e:
                print(f"[Broadcaster] snapshot failed (non-fatal): {e}")

        if batch:
            await manager.broadcast({"type": "prices", "data": batch})


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    broadcaster = asyncio.create_task(price_broadcaster())
    yield
    broadcaster.cancel()
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Grand Line Stock Exchange API",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(GZipMiddleware, minimum_size=1000)

_allowed_origin = os.getenv("ALLOWED_ORIGIN", "https://grandline-exchange-production.up.railway.app")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_allowed_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Admin-Secret"],
)

app.include_router(auth.router)
app.include_router(characters.router)
app.include_router(trades.router)
app.include_router(admin.router)
app.include_router(users.router)
app.include_router(requests_router.router)
app.include_router(comments_router.router)
app.include_router(casino_router.router)
app.include_router(profile_router.router)
app.include_router(trivia_router.router)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive; client can send pings
    except (WebSocketDisconnect, Exception):
        manager.disconnect(websocket)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@app.get("/")
def root():
    return HTMLResponse(content=_html_cache["index.html"], headers=_NO_CACHE)


@app.get("/community")
def community_page():
    return HTMLResponse(content=_html_cache["community.html"], headers=_NO_CACHE)


@app.get("/community/discord-feed")
def community_discord_feed(limit: int = 20, db=Depends(get_db)):
    """Public feed of recent Discord intelligence events for the community tab."""
    from app.models import DiscordEvent
    events = (
        db.query(DiscordEvent)
        .order_by(DiscordEvent.created_at.desc())
        .limit(min(limit, 50))
        .all()
    )
    return [
        {
            "id": e.id,
            "author": e.author or "Anonymous",
            "content": (e.content or "")[:280],
            "characters_detected": e.characters_detected or [],
            "chapter_num": e.chapter_num,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


@app.get("/casino")
def casino_page():
    return HTMLResponse(content=_html_cache["casino.html"], headers=_NO_CACHE)


@app.get("/profile")
def profile_page():
    return HTMLResponse(content=_html_cache["profile.html"], headers=_NO_CACHE)


@app.get("/transmission")
def get_transmission(db: Session = Depends(get_db)):
    """Returns the latest published chapter transmission for the site header dropdown."""
    tx = db.query(models.ChapterTransmission).order_by(
        models.ChapterTransmission.published_at.desc()
    ).first()
    if not tx:
        return None
    return {
        "chapter_number": tx.chapter_number,
        "uplink_label":   tx.uplink_label,
        "summary":        tx.summary,
        "movers":         tx.movers or [],
        "published_at":   tx.published_at.isoformat() if tx.published_at else None,
    }


@app.get("/public/latest-chapter")
def get_latest_chapter(db: Session = Depends(get_db)):
    """Returns the highest chapter number detected by the pipeline (no auth required).
    Used by the frontend to auto-update 'Last Uplink' without a manual HTML edit."""
    from sqlalchemy import func as _func
    max_ch = db.query(_func.max(models.Chapter.number)).scalar()
    return {"chapter": max_ch}


@app.get("/panel")
def admin_panel(request: Request, db: Session = Depends(get_db)):
    """Serve admin panel — only to logged-in admin users."""
    from app.auth import get_optional_user
    user = get_optional_user(request, db)
    if not user or not user.is_admin:
        return HTMLResponse(
            content="<html><body style='background:#0a0318;color:#ff6b6b;font-family:monospace;"
                    "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
                    "<div style='text-align:center'><h2>403 — ACCESS DENIED</h2>"
                    "<p style='color:#7a7a9a;margin-top:8px'>Admin privileges required.</p></div>"
                    "</body></html>",
            status_code=403,
            headers=_NO_CACHE,
        )
    return HTMLResponse(content=_html_cache["admin.html"], headers=_NO_CACHE)
