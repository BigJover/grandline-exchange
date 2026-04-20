import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from app.database import Base, engine, SessionLocal
from app import models
from app.routers import auth, characters, trades, admin
from app.scheduler import scheduler
from app.websocket_manager import manager
from app.price_queue import updates as price_updates

Base.metadata.create_all(bind=engine)


async def price_broadcaster():
    """Every second: drain trade-triggered updates and broadcast.
       Every 10 seconds: send a full price snapshot to sync new clients."""
    snapshot_every = 10
    tick = 0
    while True:
        await asyncio.sleep(1)
        tick += 1

        batch = {}

        # Drain queue of trade-triggered updates
        while not price_updates.empty():
            try:
                u = price_updates.get_nowait()
                batch[str(u["id"])] = u["beri"]
            except Exception:
                break

        # Full snapshot every 10 s (keeps newly connected clients in sync)
        if tick >= snapshot_every:
            tick = 0
            db = SessionLocal()
            try:
                rows = db.query(models.Character.id, models.Character.beri).all()
                batch = {str(r.id): r.beri for r in rows}
            finally:
                db.close()

        if batch and manager.active:
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
