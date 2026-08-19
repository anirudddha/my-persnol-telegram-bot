"""HTTP entrypoint, for Web Chat interface and Cloud Run webhook.

Cloud Run scales to zero and expects an HTTP server, so long polling cannot be
used there. Instead Telegram pushes updates to /telegram, and Cloud Scheduler
pokes /tick to deliver due reminders. Both call the same functions as the
polling entrypoint.

This module also serves the Telegram-style Web Chat UI and REST API.
"""

import pathlib
import secrets
import sys
from contextlib import asynccontextmanager

if sys.platform == "win32":
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import db
from .config import ALLOWED_TELEGRAM_IDS, CURRENCY, TIMEZONE, WEBHOOK_SECRET
from .handler import handle_message
from .main import API, deliver_due_reminders, log, process_update, reminder_tick

STATIC_DIR = pathlib.Path(__file__).parent / "static"


class ChatMessageRequest(BaseModel):
    text: str
    telegram_id: int | None = None
    name: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.open_pool()
    try:
        # Idempotent, and it saves reaching a remote database with psql just to
        # deploy. Two instances starting together can collide on the DDL, which
        # is harmless — the tables exist either way.
        await db.apply_schema()
    except Exception:
        log.warning("schema setup skipped", exc_info=True)
    app.state.telegram = httpx.AsyncClient(base_url=API, timeout=30)
    # Start background reminder sweep loop in web mode too
    tick_task = asyncio.create_task(reminder_tick(app.state.telegram))
    log.info("jarvis web up (with reminder loop active)")
    yield
    tick_task.cancel()
    try:
        await tick_task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    await app.state.telegram.aclose()
    await db.close_pool()


app = FastAPI(lifespan=lifespan)


def _authorise(supplied: str | None) -> None:
    """Both webhook endpoints face the open internet, so neither may be left unguarded."""
    if not WEBHOOK_SECRET:
        raise HTTPException(503, "WEBHOOK_SECRET is not configured")
    if not secrets.compare_digest(supplied or "", WEBHOOK_SECRET):
        raise HTTPException(403, "bad secret")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Serve the Telegram-styled Web Chat UI."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return index_file.read_text(encoding="utf-8")
    return "<h1>Jarvis Web Chat</h1><p>Static index.html not found.</p>"


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/user")
async def get_user_info() -> dict:
    """Returns default user ID and configuration for the web UI."""
    default_id = next(iter(ALLOWED_TELEGRAM_IDS)) if ALLOWED_TELEGRAM_IDS else None
    return {
        "default_id": default_id,
        "allowed_ids": list(ALLOWED_TELEGRAM_IDS),
        "timezone": TIMEZONE,
        "currency": CURRENCY,
    }


@app.get("/api/messages")
async def get_messages(telegram_id: int | None = None, limit: int = 50) -> list[dict]:
    """Fetches recent conversation history directly from PostgreSQL."""
    user_id = telegram_id or (next(iter(ALLOWED_TELEGRAM_IDS)) if ALLOWED_TELEGRAM_IDS else None)
    if not user_id:
        return []

    rows = await db.fetch(
        "select role, content, created_at from messages where user_id = %s order by created_at asc limit %s",
        user_id,
        limit,
    )
    return [
        {
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        for r in rows
    ]


@app.post("/api/chat")
async def chat(payload: ChatMessageRequest) -> dict:
    """Handles an incoming message from the web UI through the Jarvis planner."""
    user_id = payload.telegram_id or (next(iter(ALLOWED_TELEGRAM_IDS)) if ALLOWED_TELEGRAM_IDS else None)
    if not user_id:
        raise HTTPException(400, "No valid telegram_id configured")

    reply = await handle_message(user_id, payload.text, payload.name or "Web User")
    return {
        "reply": reply,
        "user_id": user_id,
    }


@app.post("/telegram")
async def telegram(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    _authorise(x_telegram_bot_api_secret_token)
    # Handled inline rather than in a background task: Cloud Run throttles the
    # CPU once a response is returned, so a backgrounded turn may never finish.
    # Telegram may retry a slow turn, which `already_seen` absorbs.
    await process_update(request.app.state.telegram, await request.json())
    return {"ok": True}


@app.post("/tick")
async def tick(request: Request, x_jarvis_secret: str | None = Header(default=None)) -> dict:
    _authorise(x_jarvis_secret)
    return {"sent": await deliver_due_reminders(request.app.state.telegram)}


if __name__ == "__main__":
    import asyncio
    import sys
    import uvicorn

    if sys.platform == "win32":
        import selectors

        config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="info")
        server = uvicorn.Server(config)
        asyncio.run(
            server.serve(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        uvicorn.run(app, host="127.0.0.1", port=8000)


