from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from bot import ws_clients

log = logging.getLogger(__name__)

app = FastAPI(title="SteelHearts Sentiment Dashboard")

STATIC_DIR = Path(__file__).parent / "static"


# ── Models ──


class KeyMomentCreate(BaseModel):
    timestamp: str
    label: str
    description: str = ""
    moment_type: str = "announcement"


class ReclassifyRequest(BaseModel):
    sentiment: str
    score: float


# ── REST Endpoints ──


@app.get("/api/overview")
async def api_overview(sentiment: Optional[str] = Query(default=None)):
    return await db.overview(sentiment_filter=sentiment)


@app.get("/api/timeline")
async def api_timeline(hours: int = Query(default=72, ge=1, le=720)):
    data = await db.timeline(hours=hours)
    moments = await db.get_key_moments(hours=hours)
    return {"timeline": data, "moments": moments}


@app.get("/api/users")
async def api_users(
    limit: int = Query(default=20, ge=1, le=100),
    sentiment: Optional[str] = Query(default=None),
):
    return await db.top_users(limit=limit, sentiment_filter=sentiment)


@app.get("/api/recent")
async def api_recent(
    limit: int = Query(default=50, ge=1, le=200),
    channel: Optional[str] = Query(default=None),
    sentiment: Optional[str] = Query(default=None),
):
    return await db.recent_messages(limit=limit, channel=channel, sentiment_filter=sentiment)


@app.get("/api/channels")
async def api_channels():
    return await db.channel_list()


# ── Reclassify ──


@app.put("/api/messages/{message_id}/sentiment")
async def api_reclassify(message_id: str, body: ReclassifyRequest):
    if body.sentiment not in ("positive", "negative", "neutral"):
        return {"error": "Invalid sentiment"}, 400
    score = max(-1.0, min(1.0, body.score))
    await db.update_sentiment(
        discord_message_id=message_id,
        sentiment=body.sentiment,
        score=score,
        keywords="[]",
    )
    return {"status": "updated", "sentiment": body.sentiment, "score": score}


# ── Key Moments ──


@app.get("/api/moments")
async def api_moments(hours: int = Query(default=720, ge=1)):
    return await db.get_key_moments(hours=hours)


@app.post("/api/moments")
async def api_create_moment(moment: KeyMomentCreate):
    moment_id = await db.insert_key_moment(
        timestamp=moment.timestamp,
        label=moment.label,
        description=moment.description,
        moment_type=moment.moment_type,
    )
    return {"id": moment_id, "status": "created"}


@app.delete("/api/moments/{moment_id}")
async def api_delete_moment(moment_id: int):
    await db.delete_key_moment(moment_id)
    return {"status": "deleted"}


# ── WebSocket ──


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    log.info("WebSocket client connected (%d total)", len(ws_clients))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)
        log.info("WebSocket client disconnected (%d total)", len(ws_clients))


# ── Static files ──


@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
