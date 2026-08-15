# Deploying Jarvis to Google Cloud Run

What we changed, what broke, and how it got fixed. Written so you can follow it again
in six months without re-deriving any of it.

---

## The core problem

Locally, Jarvis works by **asking**. It runs a loop that calls Telegram every few seconds:
*"anything new for me?"* Telegram never has to reach your laptop, which is why there was
no URL, no secret, and no extra setup.

Cloud Run cannot work that way. It is built for websites: it **sleeps when nobody is
using it** and only wakes up when an HTTP request arrives. A loop that sits there asking
Telegram for messages gets shut down the moment traffic stops.

So on Cloud Run everything runs backwards:

```
LOCAL       Jarvis ──"anything for me?"──▶ Telegram        (Jarvis asks)

CLOUD RUN   Telegram ──"here's a message"──▶ Jarvis        (Telegram tells)
            Cloud Scheduler ──"check reminders"──▶ Jarvis   (a clock tells)
```

Two consequences follow from that, and they are the whole reason this document exists:

1. Telegram needs a **public address** to push messages to — so we needed an HTTP server.
2. Nothing runs between messages — so **reminders need an outside clock**.

---

## What changed in the code

### 1. A web entrypoint — `jarvis/web.py`

Three endpoints:

| Endpoint | Who calls it | What it does |
|---|---|---|
| `POST /telegram` | Telegram | Handles one incoming message |
| `POST /tick` | Cloud Scheduler, every minute | Sends any reminders that came due |
| `GET /health` | You | Says whether the app is alive |

`jarvis/main.py` (local polling) still exists and is unchanged in behaviour. Both files
call the same two functions — `process_update()` and `deliver_due_reminders()` — so the
bot behaves identically whichever way it runs. Only the delivery mechanism differs.

### 2. A secret on the webhook

The `/telegram` URL is on the open internet, so **anyone could POST to it** and pretend to
be Telegram — logging fake expenses, reading your memory. Telegram solves this by sending
a secret you choose in a header (`X-Telegram-Bot-Api-Secret-Token`) with every request.
`/telegram` checks it and rejects anything that doesn't match.

`/tick` is protected the same way, with an `X-Jarvis-Secret` header.

If the secret is not configured at all, both endpoints return **503 rather than accepting
unverified requests**. Failing closed is deliberate — see the problems section.

### 3. Duplicate protection — the `seen_updates` table

Local polling gets this for free. Telegram's `getUpdates` takes an `offset`, so once you
have read a message it is never handed to you again.

**Webhooks have no offset.** Telegram retries whenever a response is slow or fails, and an
LLM reply takes several seconds. Without protection, one "spent 250 on lunch" becomes two
expenses.

So every update's ID is recorded in a `seen_updates` table on arrival. A repeat delivery
finds the ID already there and returns immediately.

Measured: first delivery 4.0s, the same update redelivered 0.28s and ignored.

### 4. Reminders are claimed atomically

The reminder sweep used to be: read the due list → send them → mark them sent. With one
process that is fine. With two it is a race — both read the same list before either marks
anything, and **both send**.

That matters on Cloud Run specifically, because it **runs several instances when busy**.
It also matters if you leave a local bot running against the same database.

The sweep is now a single statement that claims and returns rows at once:

```sql
update reminders set sent_at = now()
where sent_at is null and due_at <= now()
returning id, user_id, text, due_at, recurrence
```

Row locking means the second sweeper claims nothing and stays quiet. If the send then
fails, the row is un-claimed so the next sweep retries rather than losing it silently.
Two tests cover both halves.

### 5. `Dockerfile`

Standard Python image, non-root user, listens on Cloud Run's `$PORT`. One tuning flag:
`LITELLM_LOCAL_MODEL_COST_MAP=True`, because LiteLLM otherwise downloads a pricing table
on every cold start.

Measured on this app: **1.5s to start, 222 MB peak memory** — comfortably inside Cloud
Run's 512 MB default.

---

## The three setup steps

The code alone is not enough. Cloud Run needs to be told three things, **once**. This is
not per-deploy; after this, pushing code just redeploys.

### Step 1 — Give the service a secret

```bash
gcloud run services update my-persnol-telegram-bot \
  --region asia-south1 \
  --update-env-vars "WEBHOOK_SECRET=<your-secret>"
```

Use `--update-env-vars`, **not** `--set-env-vars`. The second one replaces the whole set
and would wipe your API keys.

### Step 2 — Tell Telegram where to push

```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://<your-service-url>/telegram" \
  -d "secret_token=<your-secret>"
```

### Step 3 — Give it a clock

Without this the bot answers messages perfectly and **silently never fires a reminder**.

```bash
gcloud scheduler jobs create http jarvis-tick \
  --location asia-south1 --schedule "* * * * *" \
  --uri "https://<your-service-url>/tick" \
  --http-method POST --headers "X-Jarvis-Secret=<your-secret>"
```

All three must use the **same secret**. Every minute is the finest Cloud Scheduler
allows, so a reminder can be up to a minute late.

`cloud-setup.sh` does all three and then verifies them.

---

## Problems we hit

### "No branch matching the configured branch pattern could be found"

Cloud Run's *continuously deploy from a repository* option created a Cloud Build trigger
watching `^main$`. The repository's branch is `master`. Nothing matched, so there was
nothing to build.

**Fix:** either rename the branch (`git branch -m master main && git push -u origin main`)
or edit the trigger's pattern to `^master$`.

**Worth knowing:** continuous deployment is optional. `gcloud run deploy --source .`
builds and deploys straight from a directory with no repository, trigger, or branch
involved. Easier to get working first, since you are debugging one system instead of two.

### The service was up but the bot was silent

`/health` returned `{"ok":true}` — the container had built, started, reached Neon, and
created its tables. But the bot answered nothing.

Two separate causes:

1. **`WEBHOOK_SECRET` was never set on the service.** `/telegram` returned
   `{"detail":"WEBHOOK_SECRET is not configured"}` with status **503**. The endpoint was
   refusing every request because it had no way to verify any of them.
2. **No webhook was registered.** `getWebhookInfo` showed `"url": ""` and **4 pending
   updates**. Telegram had nowhere to deliver them and was holding them in a queue.

So even a correct request would have been rejected, and no request was arriving anyway.

**How to tell these apart quickly:**

| Response from `/telegram` | Meaning |
|---|---|
| `503` | `WEBHOOK_SECRET` is not set on the service |
| `403` | Secret is set — this is correct for a request without one |
| `404` | Wrong URL, or the app did not start |

Visiting the root URL `/` gives **404 by design** — the app only serves `/health`,
`/telegram` and `/tick`. That 404 is not a failure.

### Private repository, so Cloud Shell could not clone the script

**Fix:** Cloud Shell's **⋮ menu → Upload** puts a local file straight into the shell — no
git, no credentials. Everything can also be done from the console UI with no terminal:
Cloud Run → Edit & Deploy New Revision → Variables for step 1, a browser URL for step 2,
and Cloud Scheduler → Create Job for step 3.

### Note on multiple services in one project

Every command above is scoped to one service **by name**. Nothing loops over services.
The only project-wide action is enabling the Cloud Scheduler API, which just makes a
service available and changes nothing already running. Cloud Run also deploys as a new
revision, so anything can be rolled back from the console.

---

## Verifying it worked

```bash
U=https://<your-service-url>
curl -s "$U/health"                                            # {"ok":true}
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$U/telegram" -d '{}'   # 403
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

In `getWebhookInfo`, the field that matters is **`last_error_message`**. If it is absent,
Telegram is delivering successfully. `pending_update_count` should be 0.

**The real test is a reminder.** Nothing runs between messages, so if a reminder arrives
unprompted, Cloud Scheduler is definitively working:

```
remind me in 2 minutes to stretch
```

Results from the actual deployment:

- `/health` — 200 in 49 ms
- `/telegram` without a secret — 403
- Webhook — registered, 0 pending, no `last_error_message`
- `seen_updates` — 11 rows, and only the webhook writes those
- Reminder due at `14:19:00` was **sent at `14:19:02`** — two seconds late

Also confirmed: **web search works from Cloud Run.** This was the one thing expected to
break, since DuckDuckGo often blocks datacenter IPs. From `asia-south1` it does not.

---

## Going back to local

A bot cannot use a webhook and polling at the same time. While a webhook is set, local
polling receives nothing and logs `409 Conflict`.

```bash
curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
```

Then run `python -m jarvis.main` as before.

Running both at once is **safe but pointless**: messages go only to Cloud Run, and
reminders are claimed atomically so they are still sent exactly once.

---

## Things to expect

**The first message after a quiet period takes 8–10 seconds.** Cloud Run scales to zero,
so the container has to start. Telegram may retry during that window; `seen_updates`
means you still get exactly one reply. `--min-instances 1` removes the delay but bills
around the clock.

**Secrets passed with `--set-env-vars` are visible** in the service description and your
shell history. Secret Manager is the right answer for anything long-lived.

**Cloud Run gives a service two URLs** — a newer `service-projectnumber.region.run.app`
and an older hash-based `service-hash-code.a.run.app`. Both work and point at the same
service, so do not be alarmed if the webhook shows a different one than you expect.
