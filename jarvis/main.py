"""Long-polling entrypoint. No web server, no public URL, no webhook secret —
Telegram's `offset` also gives us update de-duplication for free."""

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
            message = update.get("message") or {}
            text = message.get("text")
            if not text:
                continue
            user = message.get("from", {})
            try:
                reply = await handle_message(
                    user["id"], text, user.get("first_name")
                )
                await send(client, message["chat"]["id"], reply)
            except Exception:
                log.exception("failed handling update %s", update["update_id"])
                await send(client, message["chat"]["id"], "Something broke handling that.")


async def reminder_tick(client: httpx.AsyncClient) -> None:
    while True:
        try:
            due = await db.fetch(
                "select id, user_id, text, due_at, recurrence from reminders"
                " where sent_at is null and due_at <= now() order by due_at"
            )
            for reminder in due:
                await send(client, reminder["user_id"], f"⏰ {reminder['text']}")
                await db.execute(
                    "update reminders set sent_at = now() where id = %s", reminder["id"]
                )
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
