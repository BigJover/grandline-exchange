import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import os

limiter = Limiter(key_func=get_remote_address)

from app.database import Base, engine, SessionLocal
from app import models
from app.routers import auth, characters, trades, admin, users, requests as requests_router, comments as comments_router, casino as casino_router
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
    ]
    char_migrations = [
        ("base_beri", "DOUBLE PRECISION" if is_postgres else "FLOAT"),
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


run_column_migrations()
Base.metadata.create_all(bind=engine)
backfill_base_beri()


async def price_broadcaster():
    """Every 5 s: drain trade-triggered updates and broadcast.
       Every 60 s: send a full price snapshot — only when clients are connected."""
    import gc
    from sqlalchemy import text as _text
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

        # Full snapshot every 60 s — raw SQL, no ORM objects held in memory
        if tick >= snapshot_every:
            tick = 0
            with engine.connect() as conn:
                rows = conn.execute(_text("SELECT id, beri FROM characters")).fetchall()
                batch = {str(r[0]): r[1] for r in rows}
            gc.collect()

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(characters.router)
app.include_router(trades.router)
app.include_router(admin.router)
app.include_router(users.router)
app.include_router(requests_router.router)
app.include_router(comments_router.router)
app.include_router(casino_router.router)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive; client can send pings
    except (WebSocketDisconnect, Exception):
        manager.disconnect(websocket)


STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    with open(os.path.join(STATIC_DIR, "index.html"), "r") as f:
        content = f.read()
    return HTMLResponse(
        content=content,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/casino")
def casino_page():
    with open(os.path.join(STATIC_DIR, "casino.html"), "r") as f:
        content = f.read()
    return HTMLResponse(
        content=content,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
