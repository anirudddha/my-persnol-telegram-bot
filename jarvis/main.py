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
    """
    Purpose: Sends a text message back to a Telegram chat via Telegram Bot API.
    Called by: process_update() and deliver_due_reminders().
    Calls: httpx.AsyncClient.post()
    """
    await client.post(
        "/sendMessage", json={"chat_id": chat_id, "text": text[:TELEGRAM_MAX_CHARS]}
    )


async def already_seen(update_id: int | None) -> bool:
    """
    Purpose: Prevents processing duplicate Telegram messages by tracking seen update IDs in the database.
    Called by: process_update().
    Calls: db.fetchone()
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
    """
    Purpose: Receives a single Telegram update message, sends it to handler.handle_message(), and replies via send().
    Called by: poll_telegram() (and webhook endpoint).
    Calls: already_seen(), handler.handle_message(), send()
    """
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
    """
    Purpose: Sweeps database for pending reminders due <= now(), delivers them to Telegram, and handles recurring ones.
    Called by: reminder_tick() (and scheduled tick endpoint).
    Calls: db.fetch(), send(), db.execute(), tools.next_occurrence()
    """
    claimed = await db.fetch(
        "update reminders set sent_at = now()"
        " where sent_at is null and due_at <= now()"
        " returning id, user_id, text, due_at, recurrence"
    )
    for reminder in claimed:
        try:
            await send(client, reminder["user_id"], f"⏰ {reminder['text']}")
        except Exception:
            # Un-claim, so the next sweep retries rather than losing it silently.
            await db.execute(
                "update reminders set sent_at = null where id = %s", reminder["id"]
            )
            raise
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
    return len(claimed)


async def poll_telegram(client: httpx.AsyncClient) -> None:
    """
    Purpose: Continuously polls Telegram /getUpdates API in an infinite loop to receive new user messages.
    Called by: main().
    Calls: httpx.AsyncClient.get(), process_update(), asyncio.sleep()
    """
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
    """
    Purpose: Runs an infinite background timer loop waking up every 30 seconds to check and send due reminders.
    Called by: main().
    Calls: deliver_due_reminders(), asyncio.sleep()
    """
    while True:
        try:
            await deliver_due_reminders(client)
        except Exception:
            log.exception("reminder tick failed")
        await asyncio.sleep(TICK_SECONDS)


async def main() -> None:
    """
    Purpose: Application entrypoint. Starts DB pool, opens HTTP client, and starts polling + reminder loops concurrently.
    Called by: Script runner (if __name__ == "__main__").
    Calls: db.open_pool(), httpx.AsyncClient(), asyncio.gather(poll_telegram(), reminder_tick()), db.close_pool()
    """
    await db.open_pool()
    # Read timeout must outlast the long poll, or every poll raises.
    async with httpx.AsyncClient(base_url=API, timeout=POLL_TIMEOUT + 10) as client:
        log.info("jarvis up")
        try:
            await asyncio.gather(poll_telegram(client), reminder_tick(client))
        finally:
            await db.close_pool()


if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        import selectors

        asyncio.run(
            main(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(main())

