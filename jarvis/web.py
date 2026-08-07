"""HTTP entrypoint, for Cloud Run.

Cloud Run scales to zero and expects an HTTP server, so long polling cannot be
used there. Instead Telegram pushes updates to /telegram, and Cloud Scheduler
pokes /tick to deliver due reminders. Both call the same functions as the
polling entrypoint.
"""

import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, HTTPException, Request

from . import db
from .config import WEBHOOK_SECRET
from .main import API, deliver_due_reminders, log, process_update


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
    log.info("jarvis web up")
    yield
    await app.state.telegram.aclose()
    await db.close_pool()


app = FastAPI(lifespan=lifespan)


def _authorise(supplied: str | None) -> None:
    """Both endpoints face the open internet, so neither may be left unguarded."""
    if not WEBHOOK_SECRET:
        raise HTTPException(503, "WEBHOOK_SECRET is not configured")
    if not secrets.compare_digest(supplied or "", WEBHOOK_SECRET):
        raise HTTPException(403, "bad secret")


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


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
