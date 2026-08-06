import os

from dotenv import load_dotenv

load_dotenv()

# Whether a real database is reachable decides which tests run. Everything else
# is stubbed so the pure-logic tests work on a bare checkout.
HAS_DB = bool(os.getenv("DATABASE_URL"))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/nonexistent")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("ALLOWED_TELEGRAM_IDS", "1")
