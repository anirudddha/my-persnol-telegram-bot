import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    """
    Purpose: Reads an environment variable and ensures it is set. Exits program if missing.
    Called by: Top-level module variables (TELEGRAM_BOT_TOKEN, DATABASE_URL, GEMINI_API_KEY, ALLOWED_TELEGRAM_IDS).
    Calls: os.getenv(), SystemExit().
    """
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"missing required env var: {name} — see .env.example")
    return value


TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
DATABASE_URL = _required("DATABASE_URL")
GEMINI_API_KEY = _required("GEMINI_API_KEY")

# Optional: without it there is simply no fallback when Gemini errors.
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

try:
    ALLOWED_TELEGRAM_IDS = {
        int(part) for part in _required("ALLOWED_TELEGRAM_IDS").split(",") if part.strip()
    }
except ValueError as exc:
    raise SystemExit(
        f"ALLOWED_TELEGRAM_IDS must be comma-separated numeric ids, not @usernames ({exc})"
    ) from None

TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
CURRENCY = os.getenv("CURRENCY", "₹")

# Only needed by the Cloud Run webhook; polling locally does not use it.
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
MODEL = os.getenv("MODEL", "gemini/gemini-3.5-flash-lite")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "groq/llama-3.3-70b-versatile")
