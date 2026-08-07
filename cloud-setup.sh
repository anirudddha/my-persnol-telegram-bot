#!/usr/bin/env bash
# One-time Cloud Run wiring: webhook secret, Telegram webhook, reminder schedule.
# Safe to re-run — it rotates the secret and updates everything to match.
#
# Run it in Google Cloud Shell:
#   bash cloud-setup.sh
#
# Contains no secrets; the bot token is typed in when it runs.

set -euo pipefail

SERVICE=my-persnol-telegram-bot
REGION=asia-south1
JOB=jarvis-tick

read -rsp "Telegram bot token (from @BotFather): " TOKEN
echo
[ -n "$TOKEN" ] || { echo "A token is required."; exit 1; }

SECRET=$(openssl rand -hex 24)

echo
echo "==> Enabling Cloud Scheduler (no-op if already on)"
gcloud services enable cloudscheduler.googleapis.com --quiet || true

echo "==> Setting WEBHOOK_SECRET on $SERVICE"
# --update-env-vars adds this one key; --set-env-vars would wipe the others.
gcloud run services update "$SERVICE" \
  --region "$REGION" \
  --update-env-vars "WEBHOOK_SECRET=$SECRET" \
  --quiet

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')
echo "==> Service URL: $URL"

echo "==> Pointing Telegram at $URL/telegram"
curl -sS -X POST "https://api.telegram.org/bot$TOKEN/setWebhook" \
  -d "url=$URL/telegram" \
  -d "secret_token=$SECRET"
echo

echo "==> Scheduling the reminder sweep (every minute)"
if gcloud scheduler jobs describe "$JOB" --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$JOB" --location "$REGION" \
    --uri "$URL/tick" --http-method POST \
    --update-headers "X-Jarvis-Secret=$SECRET" --quiet
else
  gcloud scheduler jobs create http "$JOB" --location "$REGION" \
    --schedule "* * * * *" --uri "$URL/tick" --http-method POST \
    --headers "X-Jarvis-Secret=$SECRET" --quiet
fi

echo
echo "==> Checking it worked"
echo -n "health:  "; curl -sS "$URL/health"; echo
echo -n "webhook: "; curl -sS "https://api.telegram.org/bot$TOKEN/getWebhookInfo"; echo
echo -n "tick:    "; curl -sS -X POST "$URL/tick" -H "X-Jarvis-Secret: $SECRET"; echo

echo
echo "Done. Send your bot a message."
echo "Expect: health {\"ok\":true}, webhook url filled in with no last_error_message,"
echo "tick {\"sent\":N}. While the webhook is set, local polling receives nothing."
