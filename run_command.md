================================================================================
 Jarvis — Telegram & Web AI Assistant: Run Commands & Setup Guide
================================================================================

--------------------------------------------------------------------------------
STEP 1: Create and Activate Virtual Environment (One-time setup)
--------------------------------------------------------------------------------
# Windows (PowerShell):
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Windows (Command Prompt):
python -m venv .venv
.\.venv\Scripts\activate.bat

# Linux / macOS:
python3 -m venv .venv
source .venv/bin/activate


--------------------------------------------------------------------------------
STEP 2: Install Dependencies (One-time setup)
--------------------------------------------------------------------------------
# Windows:
.\.venv\Scripts\pip.exe install -r requirements.txt

# Linux / macOS:
.venv/bin/pip install -r requirements.txt


--------------------------------------------------------------------------------
STEP 3: Configure Environment Variables (.env)
--------------------------------------------------------------------------------
Make sure you have a .env file created in the project root directory containing:
  - TELEGRAM_BOT_TOKEN
  - DATABASE_URL
  - GEMINI_API_KEY
  - ALLOWED_TELEGRAM_IDS
  - TIMEZONE (e.g. Asia/Kolkata)
  - CURRENCY (e.g. ₹)


--------------------------------------------------------------------------------
STEP 4: Initialize Database Tables & Schema (Run once or after schema change)
--------------------------------------------------------------------------------
# Windows:
.\.venv\Scripts\python.exe -m jarvis.db

# Linux / macOS:
.venv/bin/python -m jarvis.db


--------------------------------------------------------------------------------
STEP 5: Running Jarvis
--------------------------------------------------------------------------------

OPTION A: Run the Telegram-Styled Web Chat UI (Browser Interface)
----------------------------------------------------------------
# Windows:
.\.venv\Scripts\python.exe -m jarvis.web

# Linux / macOS:
.venv/bin/python -m jarvis.web

--> Then open your browser at: http://127.0.0.1:8000


OPTION B: Run the Telegram Bot (Long Polling)
---------------------------------------------
# Windows (Foreground):
.\.venv\Scripts\python.exe -m jarvis.main

# Linux / macOS (Foreground):
.venv/bin/python -m jarvis.main

# Linux / macOS (Background):
nohup .venv/bin/python -m jarvis.main > jarvis.log 2>&1 &
echo $! > jarvis.pid


--------------------------------------------------------------------------------
STEP 6: Running Automated Tests
--------------------------------------------------------------------------------
# Windows:
.\.venv\Scripts\pytest.exe -v

# Linux / macOS:
.venv/bin/pytest -v
================================================================================