import os

# ─────────────────────────────────────────────
# COPY THIS FILE TO private/config_live.py
# Fill in your real values — never commit that file
# ─────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")    # From @BotFather
CHAT_ID = os.environ.get("CHAT_ID")                  # Your Telegram chat ID
OPENSEA_API_KEY = os.environ.get("OPENSEA_API_KEY")  # From opensea.io/developers
ALCHEMY_API_KEY = os.environ.get("ALCHEMY_API_KEY")  # From alchemy.com

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