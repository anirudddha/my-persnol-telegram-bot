# Jarvis — Telegram AI Assistant

A personal assistant you talk to in Telegram. Todos, reminders, memory, expense
tracking, and web research, in plain language.

> "spent 250 on lunch" · "remind me tomorrow at 8 to call the bank" · "how much did I
> spend this month?" · "remember my locker key is in my bag" · "compare these two phones"

Runs two ways from one codebase: **long polling** on your own machine, or a **webhook on
Cloud Run**. Postgres for storage, no other infrastructure.

---

## 1. Setup

Run these once, in order, from the project directory.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

If `venv` fails with an `ensurepip` error, build it pip-less and bootstrap pip — no root
needed:

```bash
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r requirements.txt
```

### Fill in `.env`

```bash
cp .env.example .env
```

| Variable | Where it comes from |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `ALLOWED_TELEGRAM_IDS` | Your **numeric** id, comma-separated for several people |
| `DATABASE_URL` | Neon or Supabase → Connection string (URI) |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) — optional, fallback only |
| `TIMEZONE` | IANA name, e.g. `Asia/Kolkata` |
| `CURRENCY` | Symbol for expenses, default `₹` |

> Secrets go in `.env`, never in `.env.example`. Only `.env` is gitignored.

`ALLOWED_TELEGRAM_IDS` needs a number like `1103318100`, **not** `@yourusername` — a
username refuses to start. Send your bot any message, then:

```bash
curl -s "https://api.telegram.org/bot$(grep '^TELEGRAM_BOT_TOKEN=' .env | cut -d= -f2-)/getUpdates" \
  | python3 -c "import sys,json; [print(u['message']['from']['id']) for u in json.load(sys.stdin)['result'] if 'message' in u]"
```

### Create the tables

```bash
.venv/bin/python -m jarvis.db
```

Prints `schema applied`. Safe to re-run — every statement is `if not exists`, which is
also how schema changes get applied. There are no migration files.

---

## 2. Running it

Foreground, while testing:

```bash
.venv/bin/python -m jarvis.main
```

Logs `jarvis up` when ready. `Ctrl-C` to stop.

Background, for daily use:

```bash
nohup .venv/bin/python -m jarvis.main > jarvis.log 2>&1 &
echo $! > jarvis.pid

tail -f jarvis.log      # watch
kill $(cat jarvis.pid)  # stop
```

> Do **not** stop it with `pkill -f jarvis.main` — that pattern matches the shell running
> the command and kills your own terminal too. Use the PID file.

Check how many copies are running with `pgrep -af 'bin/python -m jarvis'`. Run one: a
second copy fights over `getUpdates` and each gets an erratic half of your messages.

---

## 3. Talking to the bot

Plain language. There are no slash commands to memorise.

**Todos**
```
add buy milk
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
Recurring reminders support `daily` and `weekly`. If the bot was off when one was due, it
fires once on restart — not one copy per missed day.

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
spent 1800 on groceries yesterday
set my monthly budget to 20000
how much did I spend this month?
what did I spend on food?
what did I spend last month?
delete expense 12
```
Categories are picked automatically from: food, groceries, transport, bills, shopping,
health, entertainment, other. Once a budget is set, every expense reply shows how much of
it is used and warns past 80%.

**Research and overview**
```
compare these two phones
what does my day look like?
```

---

## 4. Deploying to Cloud Run

See **`DEPLOYMENT.md`** — the three setup steps, why the webhook works the way it does,
and every problem hit while getting it live.

Short version: `gcloud run deploy --source .`, then `cloud-setup.sh` wires the webhook
secret, the Telegram webhook, and the every-minute reminder schedule.

A bot cannot use a webhook and polling at once. While the webhook is set, local polling
receives nothing and logs `409 Conflict`. To go back to local:

```bash
curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
```

---

## 5. Tests

```bash
.venv/bin/pytest
```

23 tests. Pure-logic ones run anywhere; database ones skip unless `DATABASE_URL` is set,
and use a throwaway user id that is deleted afterwards — your real data is untouched.

---

## 6. Layout

```
jarvis/
  main.py      Local entrypoint: polling loop, reminder tick, and the shared
               process_update() / deliver_due_reminders() both modes call
  web.py       Cloud Run entrypoint: /telegram, /tick, /health
  handler.py   Message → LLM with tools → reply. This is the planner.
  tools.py     The 18 actions, plus the schemas the model sees
  db.py        Connection pool and three query helpers
  config.py    Env vars, validated at startup
  schema.sql   Seven tables
Dockerfile     Cloud Run image; Cloud Build uses it automatically
tests/
```

Adding a capability means writing a function in `tools.py` and adding its schema to the
`TOOLS` list. It registers itself — `handler.py` does not change.

---

## 7. Troubleshooting

**`missing required env var: X`** — `.env` is incomplete, or you launched from the wrong
directory. `.env` is read relative to where the command runs.

**`ALLOWED_TELEGRAM_IDS must be comma-separated numeric ids, not @usernames`** — use the
numeric id, see section 1.

**Bot ignores you, log says `rejected message from <id>`** — that id is not in
`ALLOWED_TELEGRAM_IDS`. Add it and restart.

**Replies are slow or stop** — free-tier rate limits. The log shows a `429` and a fallback
to Groq. Normal typing pace is fine.

**`404 ... no longer available to new users`** — Google closed that model to new API keys.
Pick another from
`curl "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY"` and
set `MODEL=` in `.env`.

**Reminders never arrive** — locally, the process is not running; on Cloud Run, the
Cloud Scheduler job is missing or its header secret is wrong.

**A duplicate entry appears** — identical entries within 2 minutes are already blocked. If
something you never typed shows up, the model invented it; tell the bot to delete it.
