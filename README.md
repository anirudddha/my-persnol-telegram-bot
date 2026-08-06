# Jarvis — Telegram AI Assistant

A personal assistant you talk to in Telegram. Todos, reminders, memory, and expense
tracking, in plain language.

> "spent 250 on lunch" · "remind me tomorrow at 8 to call the bank" · "how much did I
> spend this month?" · "remember my locker key is in my bag"

One Python process, long polling, Postgres. No web server, no public URL, no webhook.

**Built:** MVP 1 (todos, reminders, memory) and MVP 2 (expenses).
See `Jarvis_AI_Master_Build_Plan.md` for the full roadmap and what is deliberately
not built yet.

---

## 1. First-time setup

Run these once, in order, from the project directory.

### 1.1 Create the environment

This machine's Python has no `ensurepip`, so `venv` cannot install pip itself. Build
the venv pip-less and bootstrap pip into it. **No root needed** — you do *not* need
`sudo apt install python3.14-venv`.

```bash
cd /home/blackperl/all-project-files/telegram-bot
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r requirements.txt
```

On any machine with a normal Python, plain `python3 -m venv .venv` works instead.

### 1.2 Fill in `.env`

```bash
cp .env.example .env
```

Then edit `.env`:

| Variable | Where it comes from |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `ALLOWED_TELEGRAM_IDS` | Your **numeric** id — see 1.3 below |
| `DATABASE_URL` | Neon or Supabase → Connection string (URI) |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) — optional, fallback only |
| `TIMEZONE` | IANA name, e.g. `Asia/Kolkata` |
| `CURRENCY` | Symbol for expenses, default `₹` |

> **Secrets go in `.env`, never in `.env.example`.** Only `.env` is gitignored.
> `.env.example` is the committed template and must stay blank.

### 1.3 Get your numeric Telegram ID

`ALLOWED_TELEGRAM_IDS` needs a number like `1103318100`, **not** `@yourusername`.
A username will refuse to start.

Send your bot any message in Telegram, then run:

```bash
curl -s "https://api.telegram.org/bot$(grep '^TELEGRAM_BOT_TOKEN=' .env | cut -d= -f2-)/getUpdates" \
  | python3 -c "import sys,json; [print(u['message']['from']['id']) for u in json.load(sys.stdin)['result'] if 'message' in u]"
```

Put the number into `.env` as `ALLOWED_TELEGRAM_IDS=`. Comma-separate for several people.

### 1.4 Create the database tables

```bash
.venv/bin/python -m jarvis.db
```

Prints `schema applied`. Safe to re-run any time — every statement is `if not exists`,
which is also how schema changes get applied. There are no migration files.

---

## 2. Running it

### Foreground — while testing, `Ctrl-C` to stop

```bash
.venv/bin/python -m jarvis.main
```

Logs `jarvis up` when ready.

### Background — normal daily use

```bash
nohup .venv/bin/python -m jarvis.main > jarvis.log 2>&1 &
echo $! > jarvis.pid
```

Watch it:

```bash
tail -f jarvis.log
```

Stop it:

```bash
kill $(cat jarvis.pid)
```

> Do **not** stop it with `pkill -f jarvis.main`. That pattern matches the shell running
> the command, so it kills your own terminal along with the bot. Use the PID file.

Confirm exactly one copy is running — two instances double-send every reminder:

```bash
pgrep -af 'bin/python -m jarvis'
```

---

## 3. Talking to the bot

Plain language. There are no slash commands to memorise.

**Todos**
```
add buy milk
remind me to pay rent          → asks for a time
show my tasks
mark 3 as done
delete todo 3
```

**Reminders**
```
remind me in 20 minutes to check the oven
remind me tomorrow at 8 to call the bank
remind me every morning at 7 to take my medicine
what reminders do I have?
cancel reminder 4
```
Recurring reminders support `daily` and `weekly`. If the bot was off when one was due,
it fires once on restart — not one copy per missed day.

**Memory**
```
remember my locker key is in the side pocket of my bag
where is my locker key?
what do you remember about me?
forget my locker key
```

**Expenses**
```
spent 250 on lunch
350 auto to office
spent 1800 on groceries yesterday
set my monthly budget to 20000
how much did I spend this month?
what did I spend on food?
show my recent expenses
what did I spend last month?
delete expense 12
```
Categories are chosen automatically from: food, groceries, transport, bills, shopping,
health, entertainment, other. Once a budget is set, every expense reply shows how much
of it is used, warns past 80%, and says how far over you are.

**Overview**
```
what does my day look like?
```

---

## 4. Tests

```bash
.venv/bin/pytest
```

18 tests. Pure-logic ones run anywhere; database ones skip unless `DATABASE_URL` is set,
and use a throwaway user id that is deleted afterwards — your real data is untouched.

---

## 5. Layout

```
jarvis/
  main.py      Polling loop + the 30s reminder tick
  handler.py   Message → LLM with tools → reply. This is the planner.
  tools.py     The 16 actions, plus the schemas the model sees
  db.py        Connection pool and three query helpers
  config.py    Env vars, validated at startup
  schema.sql   Six tables
tests/
```

Adding a capability means writing a function in `tools.py` and adding its schema to the
`TOOLS` list. It registers itself — `handler.py` does not change.

---

## 6. Troubleshooting

**`missing required env var: X`** — `.env` is incomplete, or you are running from the
wrong directory. `.env` is read relative to where you launch the command.

**`ALLOWED_TELEGRAM_IDS must be comma-separated numeric ids, not @usernames`** — see 1.3.

**Bot ignores you, log says `rejected message from <id>`** — that id is not in
`ALLOWED_TELEGRAM_IDS`. Add it and restart.

**Replies are slow or stop** — free-tier rate limits. The log will show a `429` and a
fallback to Groq. Both providers throttle under burst; normal typing pace is fine.

**`404 ... no longer available to new users`** — Google closed that model to new API
keys. Pick another from `curl "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY"`
and set `MODEL=` in `.env`.

**Reminders never arrive** — the process is not running. It must stay up; reminders are
delivered by its 30-second tick loop.

**A duplicate todo or expense appears** — identical entries within 2 minutes are already
blocked. If something you never typed shows up, the model invented it; tell the bot to
delete it and consider `MODEL=gemini/gemini-3.5-flash` in `.env`, which is stronger but
throttles sooner.

---

## 7. Design notes

**Why long polling and no FastAPI.** No public URL, no ngrok, no webhook secret, and
Telegram's `offset` gives update de-duplication for free. The move to Cloud Run adds a
webhook endpoint calling the same `handle_message()`.

**Why reminders are polled from the database** rather than an in-process scheduler.
Cloud Run scales to zero and runs multiple instances — an in-process scheduler would
fire N times on N instances, or never on a sleeping container. This design becomes a
`/tick` endpoint driven by Cloud Scheduler with a ten-line change.

**Why one model with 16 tools** instead of separate todo/reminder/expense agents. Three
agent classes mean three LLM calls and three times the latency for the same reply. The
agent split earns its place at MVP 3 (research), where a domain needs its own system
prompt and multi-turn reasoning.

**Why `gemini-3.5-flash-lite`.** Measured on this key: `gemini-2.5-flash` returns 404,
`gemini-3.6-flash` allows 5 requests/minute and one user message costs 2–3 of them, and
`flash-lite` handled 8 rapid tool-calling requests without throttling. Groq is the
fallback and trips its own 12k tokens/minute limit only under burst.

**Why writes are de-duplicated.** Under a long tool list the model sometimes re-issues a
call it already made. `_recent_duplicate()` in `tools.py` refuses an identical row
written within 2 minutes, which also covers Telegram redelivery and double taps.
