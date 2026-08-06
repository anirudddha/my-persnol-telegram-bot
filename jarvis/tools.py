"""Every action Jarvis can take. Plain async functions returning plain strings —
the model reads the string and phrases the reply itself."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import db
from .config import CURRENCY, TIMEZONE

RECURRENCE_STEP = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


async def user_tz(user_id: int) -> ZoneInfo:
    row = await db.fetchone("select timezone from users where telegram_id = %s", user_id)
    return ZoneInfo(row["timezone"] if row else TIMEZONE)


def next_occurrence(due_at: datetime, recurrence: str, now: datetime) -> datetime | None:
    """Next firing strictly after `now`, rolling forward over missed ones.

    Without the roll-forward, a bot that was offline for a week would fire seven
    catch-up copies of a daily reminder on restart.
    """
    step = RECURRENCE_STEP.get(recurrence)
    if not step:
        return None
    nxt = due_at + step
    while nxt <= now:
        nxt += step
    return nxt
    # ponytail: fixed-offset steps, so a DST boundary shifts the local hour.
    # Swap for dateutil.rrule if you ever run this in a DST timezone.


def _fmt(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%a %d %b, %H:%M")


# --- todos ---------------------------------------------------------------


async def _recent_duplicate(table: str, user_id: int, **columns) -> dict | None:
    """An identical row written moments ago is almost never a real second entry.

    Guards every write path at once: a model that re-issues a tool call while
    answering an unrelated question, a Telegram redelivery, a double tap.
    """
    where = "".join(f" and {name} = %s" for name in columns)
    return await db.fetchone(
        f"select id from {table} where user_id = %s{where}"
        " and created_at > now() - interval '2 minutes'",
        user_id,
        *columns.values(),
    )


async def create_todo(user_id: int, text: str) -> str:
    if existing := await _recent_duplicate("todos", user_id, text=text):
        return f"'{text}' is already on the list as #{existing['id']} — not adding it twice."
    row = await db.fetchone(
        "insert into todos (user_id, text) values (%s, %s) returning id", user_id, text
    )
    return f"Added todo #{row['id']}: {text}"


async def list_todos(user_id: int, include_done: bool = False) -> str:
    sql = "select id, text, done from todos where user_id = %s"
    if not include_done:
        sql += " and not done"
    rows = await db.fetch(sql + " order by created_at", user_id)
    if not rows:
        return "No todos."
    return "\n".join(
        f"#{r['id']} {'[done] ' if r['done'] else ''}{r['text']}" for r in rows
    )


async def complete_todo(user_id: int, todo_id: int) -> str:
    row = await db.fetchone(
        "update todos set done = true where id = %s and user_id = %s returning text",
        todo_id,
        user_id,
    )
    return f"Completed: {row['text']}" if row else f"No todo #{todo_id}."


async def delete_todo(user_id: int, todo_id: int) -> str:
    row = await db.fetchone(
        "delete from todos where id = %s and user_id = %s returning text", todo_id, user_id
    )
    return f"Deleted: {row['text']}" if row else f"No todo #{todo_id}."


# --- reminders -----------------------------------------------------------


async def create_reminder(
    user_id: int, text: str, due_at: str, recurrence: str | None = None
) -> str:
    tz = await user_tz(user_id)
    try:
        when = datetime.fromisoformat(due_at)
    except ValueError:
        return f"Could not read '{due_at}' as a date/time. Use ISO format, e.g. 2026-08-07T08:00:00."
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz)
    if recurrence and recurrence not in RECURRENCE_STEP:
        return f"Unsupported recurrence '{recurrence}'. Use daily or weekly."
    row = await db.fetchone(
        "insert into reminders (user_id, text, due_at, recurrence) values (%s, %s, %s, %s)"
        " returning id",
        user_id,
        text,
        when,
        recurrence,
    )
    every = f", repeating {recurrence}" if recurrence else ""
    return f"Reminder #{row['id']} set for {_fmt(when, tz)}{every}: {text}"


async def list_reminders(user_id: int) -> str:
    tz = await user_tz(user_id)
    rows = await db.fetch(
        "select id, text, due_at, recurrence from reminders"
        " where user_id = %s and sent_at is null order by due_at",
        user_id,
    )
    if not rows:
        return "No upcoming reminders."
    return "\n".join(
        f"#{r['id']} {_fmt(r['due_at'], tz)} — {r['text']}"
        + (f" (every {r['recurrence']})" if r["recurrence"] else "")
        for r in rows
    )


async def delete_reminder(user_id: int, reminder_id: int) -> str:
    row = await db.fetchone(
        "delete from reminders where id = %s and user_id = %s returning text",
        reminder_id,
        user_id,
    )
    return f"Cancelled: {row['text']}" if row else f"No reminder #{reminder_id}."


# --- memory --------------------------------------------------------------


async def save_memory(user_id: int, key: str, value: str) -> str:
    await db.execute(
        "insert into memory_items (user_id, key, value) values (%s, %s, %s)"
        " on conflict (user_id, key) do update set value = excluded.value",
        user_id,
        key,
        value,
    )
    return f"Remembered {key}: {value}"


async def recall_memory(user_id: int, query: str | None = None) -> str:
    sql = "select key, value from memory_items where user_id = %s"
    args: tuple = (user_id,)
    if query:
        sql += " and (key ilike %s or value ilike %s)"
        args += (f"%{query}%", f"%{query}%")
    rows = await db.fetch(sql + " order by created_at", *args)
    if not rows:
        return "Nothing remembered matching that."
    return "\n".join(f"{r['key']}: {r['value']}" for r in rows)


async def forget_memory(user_id: int, key: str) -> str:
    row = await db.fetchone(
        "delete from memory_items where user_id = %s and key = %s returning key", user_id, key
    )
    return f"Forgot {key}." if row else f"Nothing stored under '{key}'."


# --- expenses ------------------------------------------------------------

CATEGORIES = [
    "food",
    "groceries",
    "transport",
    "bills",
    "shopping",
    "health",
    "entertainment",
    "other",
]


def month_bounds(tz: ZoneInfo, offset: int = 0, today: datetime | None = None):
    """Half-open [start, end) of a calendar month in the user's own timezone.

    offset=0 is this month, -1 last month. Month arithmetic is done on an
    index so December and January roll the year correctly.
    """
    now = today or datetime.now(tz)
    index = now.year * 12 + (now.month - 1) + offset
    year, month = divmod(index, 12)
    nxt = index + 1
    next_year, next_month = divmod(nxt, 12)
    return (
        datetime(year, month + 1, 1, tzinfo=tz),
        datetime(next_year, next_month + 1, 1, tzinfo=tz),
    )


def _money(amount) -> str:
    return f"{CURRENCY}{amount:,.2f}".replace(".00", "")


async def _budget_note(user_id: int, tz: ZoneInfo) -> str:
    row = await db.fetchone("select monthly_budget from users where telegram_id = %s", user_id)
    budget = row and row["monthly_budget"]
    if not budget:
        return ""
    start, end = month_bounds(tz)
    spent = await db.fetchone(
        "select coalesce(sum(amount), 0) as total from expenses"
        " where user_id = %s and spent_at >= %s and spent_at < %s",
        user_id,
        start,
        end,
    )
    total = spent["total"]
    share = total / budget * 100
    note = f" — {_money(total)} of {_money(budget)} this month ({share:.0f}%)"
    if total > budget:
        return note + f", over by {_money(total - budget)}"
    if share >= 80:
        return note + ", getting close"
    return note


async def add_expense(
    user_id: int,
    amount: float,
    description: str,
    category: str = "other",
    spent_at: str | None = None,
) -> str:
    if amount <= 0:
        return "Amount must be positive."
    if category not in CATEGORIES:
        category = "other"
    tz = await user_tz(user_id)
    when = datetime.now(tz)
    if spent_at:
        try:
            when = datetime.fromisoformat(spent_at)
        except ValueError:
            return f"Could not read '{spent_at}' as a date. Use ISO format, e.g. 2026-08-06."
        if when.tzinfo is None:
            when = when.replace(tzinfo=tz)
    if existing := await _recent_duplicate(
        "expenses", user_id, amount=amount, description=description
    ):
        return (
            f"{_money(amount)} on {description} is already logged as"
            f" #{existing['id']} — not adding it twice."
        )
    row = await db.fetchone(
        "insert into expenses (user_id, amount, description, category, spent_at)"
        " values (%s, %s, %s, %s, %s) returning id",
        user_id,
        amount,
        description,
        category,
        when,
    )
    return (
        f"Logged #{row['id']}: {_money(amount)} on {description} ({category})"
        + await _budget_note(user_id, tz)
    )


async def list_expenses(user_id: int, category: str | None = None, days: int = 7) -> str:
    tz = await user_tz(user_id)
    sql = "select id, amount, description, category, spent_at from expenses" " where user_id = %s and spent_at >= now() - make_interval(days => %s)"
    args: tuple = (user_id, days)
    if category:
        sql += " and category = %s"
        args += (category,)
    rows = await db.fetch(sql + " order by spent_at desc", *args)
    if not rows:
        return f"No expenses in the last {days} days."
    total = sum(r["amount"] for r in rows)
    lines = [
        f"#{r['id']} {r['spent_at'].astimezone(tz):%d %b} {_money(r['amount'])}"
        f" — {r['description']} ({r['category']})"
        for r in rows
    ]
    return "\n".join(lines) + f"\nTotal: {_money(total)}"


async def delete_expense(user_id: int, expense_id: int) -> str:
    row = await db.fetchone(
        "delete from expenses where id = %s and user_id = %s returning amount, description",
        expense_id,
        user_id,
    )
    if not row:
        return f"No expense #{expense_id}."
    return f"Deleted {_money(row['amount'])} on {row['description']}."


async def expense_summary(user_id: int, months_ago: int = 0) -> str:
    """Category breakdown for one month, with the change against the month before."""
    tz = await user_tz(user_id)
    start, end = month_bounds(tz, -abs(months_ago))
    rows = await db.fetch(
        "select category, sum(amount) as total, count(*) as n from expenses"
        " where user_id = %s and spent_at >= %s and spent_at < %s"
        " group by category order by total desc",
        user_id,
        start,
        end,
    )
    label = f"{start:%B %Y}"
    if not rows:
        return f"No expenses recorded for {label}."

    total = sum(r["total"] for r in rows)
    lines = [f"{label}: {_money(total)}"]
    lines += [
        f"  {r['category']}: {_money(r['total'])} ({r['n']}x, {r['total'] / total * 100:.0f}%)"
        for r in rows
    ]

    prev_start, prev_end = month_bounds(tz, -abs(months_ago) - 1)
    prev = await db.fetchone(
        "select coalesce(sum(amount), 0) as total from expenses"
        " where user_id = %s and spent_at >= %s and spent_at < %s",
        user_id,
        prev_start,
        prev_end,
    )
    if prev["total"]:
        change = (total - prev["total"]) / prev["total"] * 100
        direction = "up" if change >= 0 else "down"
        lines.append(f"{direction} {abs(change):.0f}% vs {prev_start:%B} ({_money(prev['total'])})")

    budget = await db.fetchone("select monthly_budget from users where telegram_id = %s", user_id)
    if budget and budget["monthly_budget"]:
        left = budget["monthly_budget"] - total
        lines.append(
            f"Budget {_money(budget['monthly_budget'])}: "
            + (f"{_money(left)} left" if left >= 0 else f"over by {_money(-left)}")
        )
    return "\n".join(lines)


async def set_budget(user_id: int, amount: float) -> str:
    if amount <= 0:
        await db.execute(
            "update users set monthly_budget = null where telegram_id = %s", user_id
        )
        return "Monthly budget cleared."
    await db.execute(
        "update users set monthly_budget = %s where telegram_id = %s", amount, user_id
    )
    return f"Monthly budget set to {_money(amount)}."


# --- summary -------------------------------------------------------------


async def daily_summary(user_id: int) -> str:
    tz = await user_tz(user_id)
    todos = await db.fetch(
        "select id, text from todos where user_id = %s and not done order by created_at", user_id
    )
    reminders = await db.fetch(
        "select text, due_at from reminders where user_id = %s and sent_at is null"
        " and due_at < now() + interval '24 hours' order by due_at",
        user_id,
    )
    parts = [f"Open todos ({len(todos)}):"]
    parts += [f"  #{t['id']} {t['text']}" for t in todos] or ["  none"]
    parts.append("Next 24h:")
    parts += [f"  {_fmt(r['due_at'], tz)} — {r['text']}" for r in reminders] or ["  nothing"]
    return "\n".join(parts)


# --- model-facing contract ----------------------------------------------

HANDLERS = {
    f.__name__: f
    for f in (
        create_todo,
        list_todos,
        complete_todo,
        delete_todo,
        create_reminder,
        list_reminders,
        delete_reminder,
        save_memory,
        recall_memory,
        forget_memory,
        daily_summary,
        add_expense,
        list_expenses,
        delete_expense,
        expense_summary,
        set_budget,
    )
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}

TOOLS = [
    _tool("create_todo", "Add a task to the todo list.", {"text": _STR}, ["text"]),
    _tool(
        "list_todos",
        "List todos with their id numbers. Call this before completing or deleting "
        "one, so you know the id.",
        {"include_done": {"type": "boolean", "description": "Include finished todos."}},
        [],
    ),
    _tool("complete_todo", "Mark a todo as done.", {"todo_id": _INT}, ["todo_id"]),
    _tool("delete_todo", "Remove a todo entirely.", {"todo_id": _INT}, ["todo_id"]),
    _tool(
        "create_reminder",
        "Schedule a message to be sent to the user at a given time.",
        {
            "text": {**_STR, "description": "What to remind them about."},
            "due_at": {
                **_STR,
                "description": "ISO 8601 local datetime, e.g. 2026-08-07T08:00:00. "
                "Resolve relative times against the current time given in the system prompt.",
            },
            "recurrence": {
                "type": "string",
                "enum": ["daily", "weekly"],
                "description": "Omit for a one-off reminder.",
            },
        },
        ["text", "due_at"],
    ),
    _tool("list_reminders", "List pending reminders with their id numbers.", {}, []),
    _tool("delete_reminder", "Cancel a pending reminder.", {"reminder_id": _INT}, ["reminder_id"]),
    _tool(
        "save_memory",
        "Store a durable fact about the user. Use a short stable key, e.g. 'locker key'.",
        {"key": _STR, "value": _STR},
        ["key", "value"],
    ),
    _tool(
        "recall_memory",
        "Look up stored facts. Omit query to list everything remembered.",
        {"query": {**_STR, "description": "Substring to match against keys and values."}},
        [],
    ),
    _tool("forget_memory", "Delete a stored fact by its key.", {"key": _STR}, ["key"]),
    _tool("daily_summary", "Open todos plus anything due in the next 24 hours.", {}, []),
    _tool(
        "add_expense",
        "Record money the user spent. Use for messages like 'spent 250 on lunch' or "
        "'350 groceries'. Pick the category yourself from the list — do not ask.",
        {
            "amount": {"type": "number", "description": "Positive amount in the local currency."},
            "description": {**_STR, "description": "What it was spent on, a few words."},
            "category": {"type": "string", "enum": CATEGORIES},
            "spent_at": {
                **_STR,
                "description": "ISO date/datetime if the user says when "
                "('yesterday', 'on the 3rd'). Omit for right now.",
            },
        },
        ["amount", "description"],
    ),
    _tool(
        "list_expenses",
        "Individual recent expenses with their id numbers. For totals and trends use "
        "expense_summary instead.",
        {
            "days": {"type": "integer", "description": "How far back to look. Default 7."},
            "category": {"type": "string", "enum": CATEGORIES},
        },
        [],
    ),
    _tool("delete_expense", "Remove a logged expense.", {"expense_id": _INT}, ["expense_id"]),
    _tool(
        "expense_summary",
        "Monthly total, category breakdown, change against the previous month, and "
        "budget status. Use for 'how much did I spend this month'.",
        {
            "months_ago": {
                "type": "integer",
                "description": "0 for this month (default), 1 for last month.",
            }
        },
        [],
    ),
    _tool(
        "set_budget",
        "Set the user's monthly spending budget. Pass 0 to clear it.",
        {"amount": {"type": "number"}},
        ["amount"],
    ),
]
