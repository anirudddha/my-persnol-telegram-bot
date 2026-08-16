"""Message in, reply out. One model, one tool-calling loop — that is the whole planner."""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import litellm

from . import db, tools
from .config import ALLOWED_TELEGRAM_IDS, FALLBACK_MODEL, GROQ_API_KEY, MODEL, TIMEZONE

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
HISTORY_LIMIT = 10

SYSTEM_PROMPT = """You are Jarvis, {name}'s personal assistant in Telegram.

The current local time is {now} ({tz}). Resolve every relative time — "tomorrow at 8",
"in 2 minutes", "next Monday" — against that, and always pass reminders an absolute
ISO datetime.

Rules:
- Earlier messages are context, not a to-do queue. Act only on the newest message;
  never re-run an action you already took, and never re-add something already added.
- A question is never an instruction to record something. When the user asks what they
  spent, or what is on their list, only read — do not call add_expense or create_todo
  while answering. Never invent an amount or an item the user did not state.
- Be concise. Telegram messages, not essays. No markdown headers.
- Prefer doing over explaining: call a tool rather than describing what you would do.
- Never claim to have stored, scheduled or completed something unless a tool returned
  success. Never invent stored facts — call recall_memory instead of guessing.
- Save to memory only durable, useful facts, and prefer things the user asked you to
  remember.
- Ask a follow-up only when the request is genuinely ambiguous. If a reminder has no
  clear time, ask for one rather than guessing.
- For anything current, priced, or outside what you reliably know, search rather than
  answering from memory. Say when a search found nothing instead of filling the gap.
- Summarise search results in your own words and end with the URLs you actually used.
  Never cite a source you did not open or a snippet you did not receive."""


async def _complete(messages: list[dict]):
    """
    Purpose: Calls Gemini LLM via LiteLLM for completion, with automatic fallback to Groq Llama if primary model fails.
    Called by: handle_message().
    Calls: litellm.acompletion()
    """
    try:
        return await litellm.acompletion(model=MODEL, messages=messages, tools=tools.TOOLS)
    except Exception:
        if not GROQ_API_KEY:
            raise
        log.warning("%s failed, falling back to %s", MODEL, FALLBACK_MODEL, exc_info=True)
        return await litellm.acompletion(
            model=FALLBACK_MODEL, messages=messages, tools=tools.TOOLS
        )


def _as_assistant_turn(message) -> dict:
    """
    Purpose: Rebuilds clean, portable assistant message dict (role, content, tool_calls) stripping provider-specific extra fields.
    Called by: handle_message().
    Calls: Dictionary constructors
    """
    turn = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        turn["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments or "{}",
                },
            }
            for call in message.tool_calls
        ]
    return turn


async def _run_tool(user_id: int, name: str, raw_args: str) -> str:
    """
    Purpose: Looks up and executes a tool function from tools.HANDLERS, passing JSON-parsed arguments safely.
    Called by: handle_message().
    Calls: Tool function in jarvis.tools (e.g., create_todo, add_expense, search_web, etc.)
    """
    handler = tools.HANDLERS.get(name)
    if not handler:
        return f"Error: no such tool '{name}'."
    try:
        # Groq sends the literal string "null" for no-argument tools, which
        # json.loads turns into None rather than a dict.
        args = json.loads(raw_args or "{}") or {}
        return await handler(user_id, **args)
    except Exception as exc:
        # Hand the failure back to the model so it can retry or explain, rather
        # than killing the turn.
        log.exception("tool %s failed", name)
        return f"Error running {name}: {exc}"


async def handle_message(telegram_id: int, text: str, name: str | None = None) -> str:
    """
    Purpose: Main entry point for processing a user's Telegram message through the AI model and tool-execution loop.
    Called by: jarvis.main (process_update()).
    Calls: db.execute(), db.fetch(), tools.user_tz(), _complete(), _as_assistant_turn(), _run_tool()
    """
    if telegram_id not in ALLOWED_TELEGRAM_IDS:
        log.warning("rejected message from %s", telegram_id)
        return "This is a private assistant and you are not on its list."

    await db.execute(
        "insert into users (telegram_id, name, timezone) values (%s, %s, %s)"
        " on conflict (telegram_id) do update set name = coalesce(excluded.name, users.name)",
        telegram_id,
        name,
        TIMEZONE,
    )
    await db.execute(
        "insert into messages (user_id, role, content) values (%s, 'user', %s)", telegram_id, text
    )

    history = await db.fetch(
        "select role, content from messages where user_id = %s order by created_at desc limit %s",
        telegram_id,
        HISTORY_LIMIT,
    )
    tz = await tools.user_tz(telegram_id)
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(
                name=name or "your user",
                now=datetime.now(tz).strftime("%A %d %B %Y, %H:%M"),
                tz=tz.key if isinstance(tz, ZoneInfo) else TIMEZONE,
            ),
        }
    ] + [{"role": r["role"], "content": r["content"]} for r in reversed(history)]

    reply = ""
    for _ in range(MAX_TOOL_ROUNDS):
        response = await _complete(messages)
        message = response.choices[0].message
        messages.append(_as_assistant_turn(message))
        if not message.tool_calls:
            reply = message.content or ""
            break
        for call in message.tool_calls:
            result = await _run_tool(telegram_id, call.function.name, call.function.arguments)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    else:
        # Ran out of rounds mid-tool-loop. The work may well have happened; say so
        # honestly rather than inventing a confirmation.
        reply = "I got stuck working through that. Ask me to check what actually got saved."

    reply = reply or "Done."
    await db.execute(
        "insert into messages (user_id, role, content) values (%s, 'assistant', %s)",
        telegram_id,
        reply,
    )
    return reply
