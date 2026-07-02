import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
OPENSEA_API_KEY = os.environ.get("OPENSEA_API_KEY")
ALCHEMY_API_KEY = os.environ.get("ALCHEMY_API_KEY")

# Collections to watch — add as many as you like
COLLECTIONS = [
    # Uncomment and fill in your collections
    # {
    #     "name": "Collection Name",
    #     "slug": "collection-slug",        # OpenSea slug e.g. "boredapeyachtclub"
    #     "contract": "0x...",              # Contract address
    #     "floor_alert_low": 0.0,           # Alert if floor drops BELOW this (ETH)
    #     "floor_alert_high": 0.0,          # Alert if floor rises ABOVE this (ETH)
    # },
]

# How often to poll (in minutes) — override via Railway environment variables
FLOOR_CHECK_INTERVAL = int(os.environ.get("FLOOR_CHECK_INTERVAL", 5))
MINT_CHECK_INTERVAL = int(os.environ.get("MINT_CHECK_INTERVAL", 1))
DROPS_CHECK_INTERVAL = int(os.environ.get("DROPS_CHECK_INTERVAL", 1))
MINT_COOLDOWN_MINUTES = int(os.environ.get("MINT_COOLDOWN_MINUTES", 10))
MIN_MINTS_THRESHOLD = int(os.environ.get("MIN_MINTS_THRESHOLD", 5))
FLOOR_COOLDOWN_MINUTES = int(os.environ.get("FLOOR_COOLDOWN_MINUTES", 30))