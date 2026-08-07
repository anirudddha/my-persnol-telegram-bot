# Jarvis — Telegram AI Assistant

A personal assistant you talk to in Telegram. Todos, reminders, memory, expense
tracking, and web research, in plain language.

> "spent 250 on lunch" · "remind me tomorrow at 8 to call the bank" · "how much did I
> spend this month?" · "remember my locker key is in my bag" · "compare these two phones"

Runs two ways from one codebase: **long polling** on your own machine (section 2), or a
**webhook on Cloud Run** (section 4). Postgres for storage, no other infrastructure.

**Built:** MVP 1 (todos, reminders, memory), MVP 2 (expenses), MVP 3 (research).
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

Check how many copies are running:

```bash
pgrep -af 'bin/python -m jarvis'
```

A second copy will not double-send reminders — the sweep claims them atomically — but
both will fight over `getUpdates`, so each gets an erratic half of your messages. Run one.

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

## 4. Deploying to Google Cloud Run

Cloud Run scales to zero and expects an HTTP server, so **long polling cannot be used
there** — a polling loop gets shut down the moment there is no traffic. On Cloud Run the
bot runs the other way round:

```
Telegram  ──push──▶  POST /telegram   ┐
                                      ├─▶  same handler as local polling
Cloud Scheduler ─────▶  POST /tick    ┘     (every minute, sends due reminders)
```

`jarvis/main.py` (polling) and `jarvis/web.py` (webhook) call the same
`process_update()` and `deliver_due_reminders()`, so behaviour is identical.

### 4.1 Deploy

Cloud Build picks up the `Dockerfile` automatically. From the project directory:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com cloudscheduler.googleapis.com

# Invent a webhook secret and keep it — Telegram and Cloud Scheduler both need it.
WEBHOOK_SECRET=$(openssl rand -hex 24); echo "$WEBHOOK_SECRET"

gcloud run deploy jarvis \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --set-env-vars "TELEGRAM_BOT_TOKEN=...,DATABASE_URL=...,GEMINI_API_KEY=...,GROQ_API_KEY=...,ALLOWED_TELEGRAM_IDS=1103318100,TIMEZONE=Asia/Kolkata,WEBHOOK_SECRET=$WEBHOOK_SECRET"
```

`--allow-unauthenticated` is required — Telegram cannot present a Google credential.
Both endpoints check the secret header themselves, so they are not actually open.

The tables are created automatically on first start; no `psql` needed.

> Prefer Secret Manager over `--set-env-vars` for the real keys. Anything passed this way
> is visible in the service description and in your shell history.

### 4.2 Point Telegram at it

```bash
URL=$(gcloud run services describe jarvis --region asia-south1 --format 'value(status.url)')

curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=$URL/telegram" \
  -d "secret_token=$WEBHOOK_SECRET"
```

Telegram sends the secret back in the `X-Telegram-Bot-Api-Secret-Token` header on every
request, which is what `/telegram` checks.

Check it took:

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

`pending_update_count` should be 0 and `last_error_message` absent.

### 4.3 Schedule the reminders

Without this, reminders never fire — nothing is running between messages.

```bash
gcloud scheduler jobs create http jarvis-tick \
  --location asia-south1 \
  --schedule "* * * * *" \
  --uri "$URL/tick" \
  --http-method POST \
  --headers "X-Jarvis-Secret=$WEBHOOK_SECRET"
```

Every minute is the finest Cloud Scheduler allows, so a reminder can be up to a minute
late. Three jobs are free.

### 4.4 What if both local and Cloud Run are running?

Nothing breaks, but the local one becomes useless:

- **Messages** go only to Cloud Run. Telegram allows one delivery mode at a time, so
  while a webhook is set the local `getUpdates` returns `409 Conflict` and receives
  nothing. It retries in a loop and fills `jarvis.log` with errors.
- **Reminders** are sent once, not twice. Both processes sweep the same table, but the
  sweep claims rows with a single `UPDATE ... RETURNING`, so row locking means only one
  wins. The same protection covers two Cloud Run instances, which happens whenever it
  scales out.

So it is safe, just pointless. Stop the local one to keep the logs clean.

### 4.5 Going back to local polling

```bash
curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
```

A bot cannot use a webhook and `getUpdates` at the same time — while a webhook is set,
local polling receives nothing.

### 4.6 Checking on it

```bash
gcloud run services logs tail jarvis --region asia-south1
curl "$URL/health"                       # {"ok":true}, no secret needed
```

| Symptom | Cause |
|---|---|
| Telegram silent, `getWebhookInfo` shows 403 | `WEBHOOK_SECRET` differs from the one given to `setWebhook` |
| Container fails to start | A missing env var — the log names it exactly |
| Reminders never arrive | Cloud Scheduler job missing, or its header secret is wrong |
| Replies arrive twice | Two deliveries of one update; `seen_updates` should prevent it — check the table exists |

---

## 5. Tests

```bash
.venv/bin/pytest
```

18 tests. Pure-logic ones run anywhere; database ones skip unless `DATABASE_URL` is set,
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

## 8. Design notes

**Why long polling locally.** No public URL, no ngrok, no webhook secret, and Telegram's
`offset` gives update de-duplication for free. Cloud Run cannot use it — it scales to
zero and expects HTTP — so `web.py` adds the webhook over the same functions.

**Why reminders are polled from the database** rather than an in-process scheduler.
Cloud Run scales to zero and runs multiple instances; an in-process scheduler would fire
N times on N instances, or never on a sleeping container. Because the due list lives in
the database, the same sweep works as a local loop and as a `/tick` endpoint.

**Why `seen_updates` exists.** A webhook has no `offset`. Telegram retries whenever a
response is slow, and an LLM turn takes seconds — so without an idempotency check, one
message logs the expense twice. The table records each `update_id` and the second
delivery returns immediately.

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
