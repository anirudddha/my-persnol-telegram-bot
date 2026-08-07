from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from jarvis import db, tools

from .conftest import HAS_DB

TEST_USER = 999_999_999

needs_db = pytest.mark.skipif(not HAS_DB, reason="set DATABASE_URL to run database tests")


# --- pure logic: runs anywhere -------------------------------------------


def test_next_occurrence_steps_forward():
    due = datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)
    now = datetime(2026, 8, 6, 8, 0, 30, tzinfo=timezone.utc)
    assert tools.next_occurrence(due, "daily", now) == due + timedelta(days=1)
    assert tools.next_occurrence(due, "weekly", now) == due + timedelta(weeks=1)


def test_next_occurrence_skips_missed_firings():
    """Offline for a week should mean one next reminder, not seven catch-ups."""
    due = datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)
    now = due + timedelta(days=7, hours=1)
    assert tools.next_occurrence(due, "daily", now) == datetime(
        2026, 8, 14, 8, 0, tzinfo=timezone.utc
    )


def test_next_occurrence_rejects_unknown_recurrence():
    assert tools.next_occurrence(datetime.now(timezone.utc), "hourly", datetime.now(timezone.utc)) is None


def test_assistant_turn_drops_provider_specific_fields():
    """Gemini attaches `images` to its reply; echoing that to Groq is a 400."""
    from types import SimpleNamespace

    from jarvis.handler import _as_assistant_turn

    call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="list_todos", arguments=None),
        index=0,
    )
    gemini_reply = SimpleNamespace(content=None, tool_calls=[call], images=["data:..."])

    turn = _as_assistant_turn(gemini_reply)
    assert set(turn) == {"role", "content", "tool_calls"}
    assert turn["tool_calls"][0] == {
        "id": "call_1",
        "type": "function",
        "function": {"name": "list_todos", "arguments": "{}"},
    }
    assert set(_as_assistant_turn(SimpleNamespace(content="hi", tool_calls=None))) == {
        "role",
        "content",
    }


def test_month_bounds_rolls_over_years():
    tz = ZoneInfo("Asia/Kolkata")
    jan = datetime(2026, 1, 15, tzinfo=tz)
    # Previous month of January is December of the year before.
    assert tools.month_bounds(tz, -1, jan) == (
        datetime(2025, 12, 1, tzinfo=tz),
        datetime(2026, 1, 1, tzinfo=tz),
    )
    dec = datetime(2026, 12, 15, tzinfo=tz)
    assert tools.month_bounds(tz, 0, dec) == (
        datetime(2026, 12, 1, tzinfo=tz),
        datetime(2027, 1, 1, tzinfo=tz),
    )
    aug = datetime(2026, 8, 7, tzinfo=tz)
    assert tools.month_bounds(tz, 0, aug) == (
        datetime(2026, 8, 1, tzinfo=tz),
        datetime(2026, 9, 1, tzinfo=tz),
    )


def test_month_bounds_are_local_not_utc():
    """A purchase at 01:00 IST on the 1st belongs to that month, not the previous."""
    tz = ZoneInfo("Asia/Kolkata")
    start, _ = tools.month_bounds(tz, 0, datetime(2026, 8, 7, tzinfo=tz))
    assert start.utcoffset().total_seconds() == 5.5 * 3600
    assert datetime(2026, 8, 1, 1, 0, tzinfo=tz) >= start


async def test_read_url_refuses_private_and_non_http_addresses():
    """The model picks this URL, possibly influenced by a page it just read."""
    for blocked in (
        "http://localhost:8080/admin",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "not a url",
    ):
        result = await tools.read_url(1, blocked)
        assert "will not be fetched" in result or "Only http" in result or "resolve" in result, (
            f"{blocked} was not blocked: {result}"
        )


def test_page_text_strips_markup_and_scripts():
    parser = tools._PageText()
    parser.feed(
        "<html><head><style>body{color:red}</style></head>"
        "<body><nav>Menu Home</nav><h1>Real Title</h1>"
        "<script>alert('x')</script><p>Body text here.</p></body></html>"
    )
    text = " ".join(parser.parts)
    assert "Real Title" in text and "Body text here." in text
    assert "color:red" not in text and "alert" not in text and "Menu Home" not in text


def test_every_tool_schema_has_a_handler():
    """A schema without a handler is a tool the model can call into a void."""
    named = {t["function"]["name"] for t in tools.TOOLS}
    assert named == set(tools.HANDLERS)


# --- against a real database ---------------------------------------------


@pytest.fixture(scope="session")
async def database():
    await db.open_pool()
    await db.apply_schema()
    yield
    await db.close_pool()


@pytest.fixture
async def user(database):
    await db.execute("delete from users where telegram_id = %s", TEST_USER)
    await db.execute(
        "insert into users (telegram_id, name, timezone) values (%s, 'test', 'Asia/Kolkata')",
        TEST_USER,
    )
    yield TEST_USER
    # Cascades to todos, reminders, memory_items, messages.
    await db.execute("delete from users where telegram_id = %s", TEST_USER)


@needs_db
async def test_todo_roundtrip(user):
    await tools.create_todo(user, "buy milk")
    assert "buy milk" in await tools.list_todos(user)

    todo_id = int((await tools.list_todos(user)).split()[0].lstrip("#"))
    assert "Completed" in await tools.complete_todo(user, todo_id)
    assert "buy milk" not in await tools.list_todos(user)
    assert "buy milk" in await tools.list_todos(user, include_done=True)

    assert "No todo" in await tools.complete_todo(user, 10**9)


@needs_db
async def test_memory_roundtrip(user):
    await tools.save_memory(user, "locker key", "in the side pocket of my bag")
    assert "side pocket" in await tools.recall_memory(user, "locker")
    assert "Nothing remembered" in await tools.recall_memory(user, "passport")

    await tools.save_memory(user, "locker key", "moved to the drawer")
    recalled = await tools.recall_memory(user, "locker")
    assert "drawer" in recalled and "side pocket" not in recalled

    await tools.forget_memory(user, "locker key")
    assert "Nothing remembered" in await tools.recall_memory(user, "locker")


@needs_db
async def test_reminder_due_query_respects_time(user):
    now = datetime.now(timezone.utc)
    await tools.create_reminder(user, "past one", (now - timedelta(minutes=5)).isoformat())
    await tools.create_reminder(user, "future one", (now + timedelta(hours=5)).isoformat())

    due = await db.fetch(
        "select text from reminders where user_id = %s and sent_at is null and due_at <= now()",
        user,
    )
    assert [r["text"] for r in due] == ["past one"]
    assert "future one" in await tools.list_reminders(user)


@needs_db
async def test_naive_reminder_time_uses_user_timezone(user):
    """'08:00' means 08:00 where the user lives, not 08:00 UTC."""
    await tools.create_reminder(user, "stretch", "2026-08-07T08:00:00")
    row = await db.fetchone(
        "select due_at from reminders where user_id = %s and text = 'stretch'", user
    )
    assert row["due_at"] == datetime(2026, 8, 7, 2, 30, tzinfo=timezone.utc)  # IST is UTC+5:30


@needs_db
async def test_expense_roundtrip_and_summary(user):
    await tools.add_expense(user, 250, "lunch", "food")
    await tools.add_expense(user, 1200, "week shop", "groceries")
    await tools.add_expense(user, 80, "bus", "transport")

    listed = await tools.list_expenses(user)
    assert "lunch" in listed and "1,200" in listed

    summary = await tools.expense_summary(user)
    assert "1,530" in summary  # 250 + 1200 + 80
    assert "groceries" in summary
    # Highest spend must sort first.
    assert summary.index("groceries") < summary.index("transport")

    only_food = await tools.list_expenses(user, category="food")
    assert "lunch" in only_food and "bus" not in only_food


@needs_db
async def test_identical_writes_within_the_window_are_refused(user):
    """The model sometimes re-issues a tool call it already made."""
    first = await tools.add_expense(user, 1200, "bought a shirt", "shopping")
    second = await tools.add_expense(user, 1200, "bought a shirt", "shopping")
    assert "Logged" in first and "already logged" in second
    rows = await db.fetch(
        "select id from expenses where user_id = %s and description = 'bought a shirt'", user
    )
    assert len(rows) == 1

    assert "Added todo" in await tools.create_todo(user, "buy milk")
    assert "already on the list" in await tools.create_todo(user, "buy milk")

    # A genuinely different entry still goes through.
    assert "Logged" in await tools.add_expense(user, 1200, "second shirt", "shopping")


@needs_db
async def test_expense_rejects_bad_input(user):
    assert "must be positive" in await tools.add_expense(user, -50, "refund")
    assert "Could not read" in await tools.add_expense(user, 10, "x", "food", "last tuesday")
    assert "No expense" in await tools.delete_expense(user, 10**9)
    # An unknown category is filed rather than refused — the user still gets their record.
    assert "(other)" in await tools.add_expense(user, 10, "mystery", "crypto")


@needs_db
async def test_budget_warns_only_once_over_threshold(user):
    await tools.set_budget(user, 1000)
    assert "of ₹1,000" in await tools.add_expense(user, 100, "tea", "food")
    assert "getting close" in await tools.add_expense(user, 750, "shoes", "shopping")
    assert "over by ₹150" in await tools.add_expense(user, 300, "cab", "transport")

    assert "cleared" in await tools.set_budget(user, 0)
    assert "of ₹" not in await tools.add_expense(user, 10, "gum", "food")


@needs_db
async def test_last_month_summary_is_separate_from_this_month(user):
    tz = ZoneInfo("Asia/Kolkata")
    last_start, _ = tools.month_bounds(tz, -1)
    await tools.add_expense(user, 500, "old dinner", "food", last_start.isoformat())
    await tools.add_expense(user, 70, "new coffee", "food")

    assert "500" in await tools.expense_summary(user, months_ago=1)
    this_month = await tools.expense_summary(user)
    assert "70" in this_month
    assert "old dinner" not in this_month
    # 70 against 500 last month is a fall of 86%.
    assert "down 86%" in this_month


@needs_db
async def test_concurrent_sweeps_send_a_reminder_only_once(user):
    """A local bot and Cloud Run — or two Cloud Run instances — share one database."""
    import asyncio

    from jarvis.main import deliver_due_reminders

    sent: list[str] = []

    class RecordingClient:
        async def post(self, _path, json):
            sent.append(json["text"])

    await tools.create_reminder(
        user, "stretch", (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    )
    client = RecordingClient()
    counts = await asyncio.gather(deliver_due_reminders(client), deliver_due_reminders(client))

    assert len([t for t in sent if "stretch" in t]) == 1, f"sent twice: {sent}"
    assert sorted(counts) == [0, 1]


@needs_db
async def test_failed_send_leaves_reminder_for_the_next_sweep(user):
    """Claiming a reminder must not lose it when Telegram is unreachable."""
    from jarvis.main import deliver_due_reminders

    class BrokenClient:
        async def post(self, _path, json):
            raise RuntimeError("telegram down")

    await tools.create_reminder(
        user, "call bank", (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    )
    with pytest.raises(RuntimeError):
        await deliver_due_reminders(BrokenClient())

    row = await db.fetchone(
        "select sent_at from reminders where user_id = %s and text = 'call bank'", user
    )
    assert row["sent_at"] is None, "reminder was marked sent despite the send failing"


@needs_db
async def test_no_arg_tool_call_survives_null_arguments(user):
    """Groq sends the string "null" instead of "{}" for zero-argument tools."""
    from jarvis.handler import _run_tool

    for raw in ("null", "", "{}"):
        assert await _run_tool(user, "list_todos", raw) == "No todos."
    assert "no such tool" in await _run_tool(user, "nope", "{}")


@needs_db
async def test_bad_reminder_time_is_reported_not_raised(user):
    assert "Could not read" in await tools.create_reminder(user, "x", "next tuesday-ish")
    assert "Unsupported recurrence" in await tools.create_reminder(
        user, "x", "2026-08-07T08:00:00", "hourly"
    )
