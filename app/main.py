import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from app.database import Base, engine, SessionLocal
from app import models
from app.routers import auth, characters, trades, admin, users, requests as requests_router, comments as comments_router
from app.scheduler import scheduler
from app.websocket_manager import manager
from app.price_queue import updates as price_updates


def run_column_migrations():
    """Add new columns to existing tables without Alembic. Safe to run on every startup."""
    from sqlalchemy import text
    is_postgres = str(engine.url).startswith("postgres")
    migrations = [
        ("user_faction",       "TEXT"),
        ("faction_history",    "TEXT DEFAULT '[]'"),
        ("badges",             "TEXT DEFAULT '[]'"),
        ("creature_unlocked",  "BOOLEAN DEFAULT FALSE" if is_postgres else "INTEGER DEFAULT 0"),
        ("last_faction_change", "TIMESTAMPTZ" if is_postgres else "DATETIME"),
        ("warlord_until",       "TIMESTAMPTZ" if is_postgres else "DATETIME"),
        ("is_admin",           "BOOLEAN DEFAULT FALSE" if is_postgres else "INTEGER DEFAULT 0"),
    ]
    with engine.connect() as conn:
        for col, col_type in migrations:
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


run_column_migrations()
Base.metadata.create_all(bind=engine)


async def price_broadcaster():
    """Every 5 s: drain trade-triggered updates and broadcast.
       Every 30 s: send a full price snapshot — only when clients are connected."""
    snapshot_every = 6   # 6 × 5 s = 30 s between snapshots
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

        # Full snapshot every 30 s to sync newly connected clients
        if tick >= snapshot_every:
            tick = 0
            db = SessionLocal()
            try:
                rows = db.query(models.Character.id, models.Character.beri).all()
                batch = {str(r.id): r.beri for r in rows}
            finally:
                db.close()

        if batch:
            await manager.broadcast({"type": "prices", "data": batch})


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    broadcaster = asyncio.create_task(price_broadcaster())
    yield
    broadcaster.cancel()
    scheduler.shutdown(wait=False)


app = FastAPI(title="Grand Line Stock Exchange API", lifespan=lifespan)

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
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
