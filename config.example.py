import os

# ─────────────────────────────────────────────
# COPY THIS FILE TO private/config_live.py
# Fill in your real values — never commit that file
# ─────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")    # From @BotFather
CHAT_ID = os.environ.get("CHAT_ID")                  # Your Telegram chat ID
OPENSEA_API_KEY = os.environ.get("OPENSEA_API_KEY")  # From opensea.io/developers
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")    # From aistudio.google.com

# Extra Gemini keys for daily-quota rotation. Each free-tier key allows roughly
# 500 requests per UTC day; when one is exhausted the bot rotates to the next.
# Create additional keys under different Google accounts at aistudio.google.com.
GEMINI_API_KEYS = [
    # "AIza...second-key",
    # "AIza...third-key",
]

# Requests per key per UTC day before rotating away from it (free tier = 500).
GEMINI_DAILY_LIMIT = 500

# Collections to watch
COLLECTIONS = [
    {
        "name": "Bored Ape Yacht Club",
        "slug": "boredapeyachtclub",
        "contract": "0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D",
        "floor_alert_low": 10.0,    # Alert if floor drops BELOW this (ETH)
        "floor_alert_high": 20.0,   # Alert if floor rises ABOVE this (ETH)
    },
    {
        "name": "Pudgy Penguins",
        "slug": "pudgypenguins",
        "contract": "0xBd3531dA5CF5857e7CfAA92426877b022e612cf8",
        "floor_alert_low": 5.0,
        "floor_alert_high": 15.0,
    },
    # Add more collections here...
]

# How often to poll (in minutes)
FLOOR_CHECK_INTERVAL = 5
MINT_CHECK_INTERVAL = 1
DROPS_CHECK_INTERVAL = 1
MINT_COOLDOWN_MINUTES = 10
MIN_MINTS_THRESHOLD = 5
FLOOR_COOLDOWN_MINUTES = 30
MAX_CONTRACT_AGE_HOURS = 48   # Skip drops whose contract was deployed longer ago than this

# Gemini AI filter. Every name imported from this file must exist, because one
# missing name aborts the whole `from private.config_live import ...` statement
# and silently falls back to config.py, discarding the keys set above.
GEMINI_MIN_SCORE = 40         # Suppress drops scoring below this (0 = off)