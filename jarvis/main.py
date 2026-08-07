"""Long-polling entrypoint, for running Jarvis on your own machine.

The per-update and per-reminder work lives here as plain functions so the
Cloud Run webhook (`jarvis/web.py`) drives exactly the same code paths.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from . import db, tools
from .config import TELEGRAM_BOT_TOKEN
from .handler import handle_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jarvis")

API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
POLL_TIMEOUT = 30
TICK_SECONDS = 30
TELEGRAM_MAX_CHARS = 4096


async def send(client: httpx.AsyncClient, chat_id: int, text: str) -> None:
    await client.post(
        "/sendMessage", json={"chat_id": chat_id, "text": text[:TELEGRAM_MAX_CHARS]}
    )


async def already_seen(update_id: int | None) -> bool:
    """True if this update was handled before.

    Polling gets de-duplication free from the `offset` parameter, but a webhook
    does not: Telegram redelivers whenever a response is slow or fails, and an
    LLM turn is slow. Without this, one message books the same reminder twice.
    """
    if update_id is None:
        return False
    fresh = await db.fetchone(
        "insert into seen_updates (update_id) values (%s)"
        " on conflict (update_id) do nothing returning update_id",
        update_id,
    )
    return fresh is None


async def process_update(client: httpx.AsyncClient, update: dict) -> None:
    """Handle one Telegram update. Safe to call twice with the same update."""
    message = update.get("message") or {}
    text = message.get("text")
    if not text:
        return
    update_id = update.get("update_id")
    if await already_seen(update_id):
        log.info("ignoring repeat delivery of update %s", update_id)
        return
    user = message.get("from", {})
    try:
        reply = await handle_message(user["id"], text, user.get("first_name"))
        await send(client, message["chat"]["id"], reply)
    except Exception:
        log.exception("failed handling update %s", update_id)
        await send(client, message["chat"]["id"], "Something broke handling that.")


async def deliver_due_reminders(client: httpx.AsyncClient) -> int:
    """Send every reminder that has come due. Returns how many went out."""
    due = await db.fetch(
        "select id, user_id, text, due_at, recurrence from reminders"
        " where sent_at is null and due_at <= now() order by due_at"
    )
    for reminder in due:
        await send(client, reminder["user_id"], f"⏰ {reminder['text']}")
        await db.execute("update reminders set sent_at = now() where id = %s", reminder["id"])
        if reminder["recurrence"]:
            following = tools.next_occurrence(
                reminder["due_at"], reminder["recurrence"], datetime.now(timezone.utc)
            )
            if following:
                await db.execute(
                    "insert into reminders (user_id, text, due_at, recurrence)"
                    " values (%s, %s, %s, %s)",
                    reminder["user_id"],
                    reminder["text"],
                    following,
                    reminder["recurrence"],
                )
    # Keeps the de-duplication table from growing without bound.
    await db.execute("delete from seen_updates where seen_at < now() - interval '2 days'")
    return len(due)


async def poll_telegram(client: httpx.AsyncClient) -> None:
    offset = None
    while True:
        try:
            params = {"timeout": POLL_TIMEOUT}
            if offset is not None:
                params["offset"] = offset
            response = await client.get("/getUpdates", params=params)
            updates = response.json().get("result", [])
        except Exception:
            log.exception("getUpdates failed")
            await asyncio.sleep(5)
            continue

        for update in updates:
            # Advance first: a message that crashes the handler must not be
            # redelivered forever.
            offset = update["update_id"] + 1
            await process_update(client, update)


async def reminder_tick(client: httpx.AsyncClient) -> None:
    while True:
        try:
            await deliver_due_reminders(client)
        except Exception:
            log.exception("reminder tick failed")
        await asyncio.sleep(TICK_SECONDS)


async def main() -> None:
    await db.open_pool()
    # Read timeout must outlast the long poll, or every poll raises.
    async with httpx.AsyncClient(base_url=API, timeout=POLL_TIMEOUT + 10) as client:
        log.info("jarvis up")
        try:
            await asyncio.gather(poll_telegram(client), reminder_tick(client))
        finally:
            await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
