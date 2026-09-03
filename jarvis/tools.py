"""Every action Jarvis can take. Plain async functions returning plain strings —
the model reads the string and phrases the reply itself."""

import asyncio
import ipaddress
import socket
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from ddgs import DDGS

from . import db
from .config import CURRENCY, TIMEZONE

RECURRENCE_STEP = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


async def user_tz(user_id: int) -> ZoneInfo:
    """
    Purpose: Fetches the user's saved timezone from PostgreSQL database as a ZoneInfo object.
    Called by: create_reminder(), list_reminders(), add_expense(), list_expenses(), expense_summary(), daily_summary(), _budget_note(), handler.handle_message().
    Calls: db.fetchone()
    """
    row = await db.fetchone("select timezone from users where telegram_id = %s", user_id)
    return ZoneInfo(row["timezone"] if row else TIMEZONE)


def next_occurrence(due_at: datetime, recurrence: str, now: datetime) -> datetime | None:
    """
    Purpose: Calculates the next firing datetime for daily or weekly recurring reminders strictly after now.
    Called by: main.deliver_due_reminders().
    Calls: Datetime addition via RECURRENCE_STEP
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
    """
    Purpose: Formats a datetime object into a user-friendly string (e.g. 'Mon 07 Aug, 08:00') in user's timezone.
    Called by: create_reminder(), list_reminders(), daily_summary().
    Calls: dt.astimezone(), dt.strftime()
    """
    return dt.astimezone(tz).strftime("%a %d %b, %H:%M")


# --- todos ---------------------------------------------------------------


async def _recent_duplicate(table: str, user_id: int, **columns) -> dict | None:
    """
    Purpose: Checks if an identical record was added in the past 2 minutes to prevent duplicate writes.
    Called by: create_todo(), add_expense().
    Calls: db.fetchone()
    """
    where = "".join(f" and {name} = %s" for name in columns)
    return await db.fetchone(
        f"select id from {table} where user_id = %s{where}"
        " and created_at > now() - interval '2 minutes'",
        user_id,
        *columns.values(),
    )


async def create_todo(user_id: int, text: str) -> str:
    """
    Purpose: Creates a new todo task in the database for the user.
    Called by: handler._run_tool() (via AI tool call 'create_todo').
    Calls: _recent_duplicate(), db.fetchone()
    """
    if existing := await _recent_duplicate("todos", user_id, text=text):
        return f"'{text}' is already on the list as #{existing['id']} — not adding it twice."
    row = await db.fetchone(
        "insert into todos (user_id, text) values (%s, %s) returning id", user_id, text
    )
    return f"Added todo #{row['id']}: {text}"


async def list_todos(user_id: int, include_done: bool = False) -> str:
    """
    Purpose: Lists all active (and optionally completed) todo tasks for the user.
    Called by: handler._run_tool() (via AI tool call 'list_todos').
    Calls: db.fetch()
    """
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
    """
    Purpose: Marks a specific todo task as completed in the database.
    Called by: handler._run_tool() (via AI tool call 'complete_todo').
    Calls: db.fetchone()
    """
    row = await db.fetchone(
        "update todos set done = true where id = %s and user_id = %s returning text",
        todo_id,
        user_id,
    )
    return f"Completed: {row['text']}" if row else f"No todo #{todo_id}."


async def delete_todo(user_id: int, todo_id: int) -> str:
    """
    Purpose: Removes a todo task from the database by ID.
    Called by: handler._run_tool() (via AI tool call 'delete_todo').
    Calls: db.fetchone()
    """
    row = await db.fetchone(
        "delete from todos where id = %s and user_id = %s returning text", todo_id, user_id
    )
    return f"Deleted: {row['text']}" if row else f"No todo #{todo_id}."


# --- reminders -----------------------------------------------------------


async def create_reminder(
    user_id: int, text: str, due_at: str, recurrence: str | None = None
) -> str:
    """
    Purpose: Schedules a new one-off or recurring reminder in the reminders table.
    Called by: handler._run_tool() (via AI tool call 'create_reminder').
    Calls: user_tz(), _fmt(), db.fetchone()
    """
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
    """
    Purpose: Lists all upcoming pending (unsent) reminders for the user.
    Called by: handler._run_tool() (via AI tool call 'list_reminders').
    Calls: user_tz(), _fmt(), db.fetch()
    """
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
    """
    Purpose: Cancels and removes a scheduled reminder from the database.
    Called by: handler._run_tool() (via AI tool call 'delete_reminder').
    Calls: db.fetchone()
    """
    row = await db.fetchone(
        "delete from reminders where id = %s and user_id = %s returning text",
        reminder_id,
        user_id,
    )
    return f"Cancelled: {row['text']}" if row else f"No reminder #{reminder_id}."


# --- memory --------------------------------------------------------------


async def save_memory(user_id: int, key: str, value: str) -> str:
    """
    Purpose: Stores or updates a durable key-value fact about the user in memory_items.
    Called by: handler._run_tool() (via AI tool call 'save_memory').
    Calls: db.execute()
    """
    await db.execute(
        "insert into memory_items (user_id, key, value) values (%s, %s, %s)"
        " on conflict (user_id, key) do update set value = excluded.value",
        user_id,
        key,
        value,
    )
    return f"Remembered {key}: {value}"


async def recall_memory(user_id: int, query: str | None = None) -> str:
    """
    Purpose: Retrieves stored facts matching an optional search query string.
    Called by: handler._run_tool() (via AI tool call 'recall_memory').
    Calls: db.fetch()
    """
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
    """
    Purpose: Deletes a stored key-value fact from memory_items.
    Called by: handler._run_tool() (via AI tool call 'forget_memory').
    Calls: db.fetchone()
    """
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
    """
    Purpose: Calculates start and end timestamps for a calendar month in user's timezone.
    Called by: _budget_note(), expense_summary().
    Calls: datetime arithmetic
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
    """
    Purpose: Formats a numeric amount into a currency string (e.g. '₹250').
    Called by: _budget_note(), add_expense(), list_expenses(), delete_expense(), expense_summary(), set_budget().
    Calls: String formatting
    """
    return f"{CURRENCY}{amount:,.2f}".replace(".00", "")


async def _budget_note(user_id: int, tz: ZoneInfo) -> str:
    """
    Purpose: Computes current monthly spending against budget limit and returns warning status.
    Called by: add_expense().
    Calls: db.fetchone(), month_bounds(), _money()
    """
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
    """
    Purpose: Records a monetary expense in the database and returns a budget note.
    Called by: handler._run_tool() (via AI tool call 'add_expense').
    Calls: user_tz(), _recent_duplicate(), db.fetchone(), _budget_note(), _money()
    """
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
    """
    Purpose: Lists individual recent expense transactions for a specified number of days/category.
    Called by: handler._run_tool() (via AI tool call 'list_expenses').
    Calls: user_tz(), db.fetch(), _money()
    """
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
    """
    Purpose: Deletes a logged expense record from the database by ID.
    Called by: handler._run_tool() (via AI tool call 'delete_expense').
    Calls: db.fetchone(), _money()
    """
    row = await db.fetchone(
        "delete from expenses where id = %s and user_id = %s returning amount, description",
        expense_id,
        user_id,
    )
    if not row:
        return f"No expense #{expense_id}."
    return f"Deleted {_money(row['amount'])} on {row['description']}."


async def expense_summary(user_id: int, months_ago: int = 0) -> str:
    """
    Purpose: Calculates category breakdown, monthly totals, month-over-month % change, and budget remaining for a given month.
    Called by: handler._run_tool() (via AI tool call 'expense_summary').
    Calls: user_tz(), month_bounds(), db.fetch(), db.fetchone(), _money()
    """
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
    """
    Purpose: Sets or clears (if 0) the user's monthly expense budget in the database.
    Called by: handler._run_tool() (via AI tool call 'set_budget').
    Calls: db.execute(), _money()
    """
    if amount <= 0:
        await db.execute(
            "update users set monthly_budget = null where telegram_id = %s", user_id
        )
        return "Monthly budget cleared."
    await db.execute(
        "update users set monthly_budget = %s where telegram_id = %s", amount, user_id
    )
    return f"Monthly budget set to {_money(amount)}."


# --- meals & calories ---------------------------------------------------

MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack", "drink", "other"]


def day_bounds(tz: ZoneInfo, offset_days: int = 0, today: datetime | None = None) -> tuple[datetime, datetime]:
    """
    Purpose: Calculates start and end timestamps (midnight to midnight) of a day in user's timezone.
    Called by: _calorie_target_note(), calorie_summary(), list_meals().
    Calls: datetime arithmetic
    """
    now = today or datetime.now(tz)
    base_date = (now + timedelta(days=offset_days)).date()
    start = datetime(base_date.year, base_date.month, base_date.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


async def _calorie_target_note(user_id: int, tz: ZoneInfo) -> str:
    """
    Purpose: Computes today's total consumed calories against daily target and returns status string.
    Called by: add_meal().
    Calls: db.fetchone(), day_bounds()
    """
    row = await db.fetchone("select daily_calorie_target from users where telegram_id = %s", user_id)
    target = row and row["daily_calorie_target"]
    if not target:
        return ""
    start, end = day_bounds(tz)
    consumed_row = await db.fetchone(
        "select coalesce(sum(calories), 0) as total from meals"
        " where user_id = %s and logged_at >= %s and logged_at < %s",
        user_id,
        start,
        end,
    )
    total = int(consumed_row["total"])
    share = (total / target) * 100
    remaining = target - total
    note = f" — {total:,} of {target:,} kcal today ({share:.0f}%)"
    if total > target:
        return note + f", over by {total - target:,} kcal"
    return note + f", {remaining:,} kcal left"


async def add_meal(
    user_id: int,
    food_item: str,
    calories: int,
    meal_type: str = "other",
    logged_at: str | None = None,
) -> str:
    """
    Purpose: Records a food meal entry with approximate calories in the database.
    Called by: handler._run_tool() (via AI tool call 'add_meal').
    Calls: user_tz(), _recent_duplicate(), db.fetchone(), _calorie_target_note()
    """
    if calories <= 0:
        return "Calories must be a positive integer."
    if meal_type not in MEAL_TYPES:
        meal_type = "other"
    tz = await user_tz(user_id)
    when = datetime.now(tz)
    if logged_at:
        try:
            when = datetime.fromisoformat(logged_at)
        except ValueError:
            return f"Could not read '{logged_at}' as a date. Use ISO format, e.g. 2026-09-02T13:00:00."
        if when.tzinfo is None:
            when = when.replace(tzinfo=tz)
    if existing := await _recent_duplicate(
        "meals", user_id, food_item=food_item, calories=calories
    ):
        return (
            f"'{food_item}' ({calories} kcal) is already logged as"
            f" #{existing['id']} — not adding it twice."
        )
    row = await db.fetchone(
        "insert into meals (user_id, food_item, calories, meal_type, logged_at)"
        " values (%s, %s, %s, %s, %s) returning id",
        user_id,
        food_item,
        calories,
        meal_type,
        when,
    )
    return (
        f"Logged #{row['id']}: {food_item} ({meal_type}, ~{calories} kcal)"
        + await _calorie_target_note(user_id, tz)
    )


async def list_meals(user_id: int, days: int = 1) -> str:
    """
    Purpose: Lists recently logged meals with calories and meal categories.
    Called by: handler._run_tool() (via AI tool call 'list_meals').
    Calls: user_tz(), db.fetch()
    """
    tz = await user_tz(user_id)
    rows = await db.fetch(
        "select id, food_item, calories, meal_type, logged_at from meals"
        " where user_id = %s and logged_at >= now() - make_interval(days => %s)"
        " order by logged_at desc",
        user_id,
        days,
    )
    if not rows:
        return f"No meals logged in the last {days} day(s)."
    total = sum(r["calories"] for r in rows)
    lines = [
        f"#{r['id']} {r['logged_at'].astimezone(tz):%d %b %H:%M} ~{r['calories']} kcal"
        f" — {r['food_item']} ({r['meal_type']})"
        for r in rows
    ]
    return "\n".join(lines) + f"\nTotal: {total:,} kcal"


async def delete_meal(user_id: int, meal_id: int) -> str:
    """
    Purpose: Removes a logged meal from the database by ID.
    Called by: handler._run_tool() (via AI tool call 'delete_meal').
    Calls: db.fetchone()
    """
    row = await db.fetchone(
        "delete from meals where id = %s and user_id = %s returning food_item, calories",
        meal_id,
        user_id,
    )
    if not row:
        return f"No meal #{meal_id}."
    return f"Deleted {row['food_item']} (~{row['calories']} kcal)."


async def calorie_summary(user_id: int, days_ago: int = 0) -> str:
    """
    Purpose: Generates a daily summary of calorie intake broken down by meal type vs daily target.
    Called by: handler._run_tool() (via AI tool call 'calorie_summary').
    Calls: user_tz(), day_bounds(), db.fetch(), db.fetchone()
    """
    tz = await user_tz(user_id)
    start, end = day_bounds(tz, -abs(days_ago))
    rows = await db.fetch(
        "select meal_type, sum(calories) as total, count(*) as count from meals"
        " where user_id = %s and logged_at >= %s and logged_at < %s"
        " group by meal_type order by total desc",
        user_id,
        start,
        end,
    )
    label = f"{start:%A, %d %B %Y}"
    if not rows:
        return f"No meals logged for {label}."

    total = sum(r["total"] for r in rows)
    lines = [f"Calories for {label}: {total:,} kcal"]
    lines += [
        f"  {r['meal_type']}: {r['total']:,} kcal ({r['count']} item{'s' if r['count'] > 1 else ''})"
        for r in rows
    ]

    target_row = await db.fetchone(
        "select daily_calorie_target from users where telegram_id = %s", user_id
    )
    if target_row and target_row["daily_calorie_target"]:
        target = target_row["daily_calorie_target"]
        left = target - total
        lines.append(
            f"Daily Goal {target:,} kcal: "
            + (f"{left:,} kcal left" if left >= 0 else f"over by {-left:,} kcal")
        )
    return "\n".join(lines)


async def set_calorie_target(user_id: int, target_calories: int) -> str:
    """
    Purpose: Sets or clears (if <= 0) the user's daily calorie intake target in the users table.
    Called by: handler._run_tool() (via AI tool call 'set_calorie_target').
    Calls: db.execute()
    """
    if target_calories <= 0:
        await db.execute(
            "update users set daily_calorie_target = null where telegram_id = %s", user_id
        )
        return "Daily calorie target cleared."
    await db.execute(
        "update users set daily_calorie_target = %s where telegram_id = %s",
        target_calories,
        user_id,
    )
    return f"Daily calorie target set to {target_calories:,} kcal."


# --- research ------------------------------------------------------------

SEARCH_RESULTS = 5
PAGE_CHAR_LIMIT = 6000
PAGE_BYTE_LIMIT = 2_000_000


class _PageText(HTMLParser):
    """Just enough HTML stripping to summarise an article. No parser dependency."""

    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form", "aside"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if not self._depth and (text := data.strip()):
            self.parts.append(text)


def _safe_url(url: str) -> str | None:
    """
    Purpose: Validates that a web URL is a public http(s) address and prevents SSRF access to internal IPs.
    Called by: read_url().
    Calls: urlparse(), socket.getaddrinfo(), ipaddress.ip_address()
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "Only http and https addresses can be read."
    try:
        for *_, sockaddr in socket.getaddrinfo(parsed.hostname, None):
            if ipaddress.ip_address(sockaddr[0]).is_global is False:
                return "That address is on a private network and will not be fetched."
    except (socket.gaierror, ValueError):
        return f"Could not resolve {parsed.hostname}."
    return None


async def search_web(user_id: int, query: str, max_results: int = SEARCH_RESULTS) -> str:
    """
    Purpose: Performs DuckDuckGo web search in a non-blocking thread pool and returns search result snippets.
    Called by: handler._run_tool() (via AI tool call 'search_web').
    Calls: asyncio.to_thread(), DDGS().text()
    """
    try:
        # ddgs is blocking, so keep it off the event loop that also serves Telegram.
        results = await asyncio.to_thread(
            lambda: DDGS().text(query, max_results=max(1, min(max_results, 10)))
        )
    except Exception as exc:
        return f"Search failed: {exc}"
    if not results:
        return f"No results for '{query}'."
    return "\n\n".join(
        f"[{i}] {r['title']}\n{r['href']}\n{r.get('body', '')}"
        for i, r in enumerate(results, 1)
    )


async def read_url(user_id: int, url: str) -> str:
    """
    Purpose: Fetches a public web page, strips HTML tags using _PageText, and returns readable plain text.
    Called by: handler._run_tool() (via AI tool call 'read_url').
    Calls: _safe_url(), httpx.AsyncClient.get(), _PageText HTML parser
    """
    if problem := _safe_url(url):
        return problem
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Jarvis)"})
            response.raise_for_status()
    except Exception as exc:
        return f"Could not fetch {url}: {exc}"
    if len(response.content) > PAGE_BYTE_LIMIT:
        return f"{url} is too large to read."
    if "html" not in response.headers.get("content-type", "") and not response.text.strip():
        return f"{url} returned no readable text."

    parser = _PageText()
    parser.feed(response.text)
    text = " ".join(parser.parts)
    if not text:
        return f"No readable text found at {url}."
    if len(text) > PAGE_CHAR_LIMIT:
        text = text[:PAGE_CHAR_LIMIT] + " […truncated]"
    return text


# --- summary -------------------------------------------------------------


async def daily_summary(user_id: int) -> str:
    """
    Purpose: Fetches all open todo items and reminders due in the next 24 hours for the user.
    Called by: handler._run_tool() (via AI tool call 'daily_summary').
    Calls: user_tz(), db.fetch(), _fmt()
    """
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
        search_web,
        read_url,
        add_meal,
        list_meals,
        delete_meal,
        calorie_summary,
        set_calorie_target,
    )
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    """
    Purpose: Constructs a standardized function-calling tool schema dict for LLM consumption.
    Called by: Top-level TOOLS list initialization.
    Calls: Dictionary constructors
    """
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
            "spends": {"type": "string", "description": "Optional category filter."},
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
    _tool(
        "add_meal",
        "Record food or drink the user consumed with estimated calories in kcal. "
        "Estimate calories reasonably based on portions described. "
        "Pick meal_type from breakfast, lunch, dinner, snack, drink, other.",
        {
            "food_item": {**_STR, "description": "What was eaten/drunk with portion (e.g. '2 rotis and a bowl of dal')."},
            "calories": {"type": "integer", "description": "Approximate calories in kcal (e.g. 350)."},
            "meal_type": {"type": "string", "enum": MEAL_TYPES, "description": "Meal category."},
            "logged_at": {
                **_STR,
                "description": "ISO date/datetime if user ate at a specific time ('yesterday at 2pm'). Omit for now.",
            },
        },
        ["food_item", "calories"],
    ),
    _tool(
        "list_meals",
        "List recently logged meals and drinks with id numbers and calories.",
        {"days": {"type": "integer", "description": "How many days back to look. Default 1."}},
        [],
    ),
    _tool("delete_meal", "Remove a logged meal by its id number.", {"meal_id": _INT}, ["meal_id"]),
    _tool(
        "calorie_summary",
        "Show total calorie intake for today (or past days) broken down by meal type, with progress against daily calorie goal.",
        {"days_ago": {"type": "integer", "description": "0 for today (default), 1 for yesterday."}},
        [],
    ),
    _tool(
        "set_calorie_target",
        "Set the user's daily calorie intake goal in kcal (e.g. 2000). Pass 0 to clear.",
        {"target_calories": _INT},
        ["target_calories"],
    ),
    _tool(
        "search_web",
        "Search the web and get titles, URLs and short snippets. Use for anything "
        "current, factual or outside your knowledge — prices, news, releases, "
        "comparisons. Snippets are short: call read_url on a result when you need "
        "the detail.",
        {
            "query": {**_STR, "description": "Search terms, not a full sentence."},
            "max_results": {"type": "integer", "description": "1-10, default 5."},
        },
        ["query"],
    ),
    _tool(
        "read_url",
        "Fetch a web page and return its text, for summarising an article or getting "
        "detail a search snippet only hinted at.",
        {"url": {**_STR, "description": "Full http(s) URL."}},
        ["url"],
    ),
]
