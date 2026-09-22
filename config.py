import os
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
OPENSEA_API_KEY = os.environ.get("OPENSEA_API_KEY")
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY")
HONEYPOT_API_KEY = os.environ.get("HONEYPOT_API_KEY")
GOPLUS_API_KEY = os.environ.get("GOPLUS_API_KEY")

# Arc EVM RPC endpoint (Chain ID 5042). Configurable via ARC_RPC_URL or ARC_RPC.
ARC_RPC_URL = os.environ.get("ARC_RPC_URL") or os.environ.get("ARC_RPC", "https://rpc.mainnet.arc.io")

# Additional Gemini keys for quota rotation. Each free-tier key is capped per day,
# so the bot rotates to the next usable key when one is exhausted. Supply a
# comma-separated list; GEMINI_API_KEY is always tried first.
GEMINI_API_KEYS = [
    k.strip() for k in os.environ.get("GEMINI_API_KEYS", "").split(",") if k.strip()
]

# Requests allowed per key per UTC day before the bot rotates away from it.
# Matches the free-tier gemini-flash-lite allowance; raise it for paid keys.
GEMINI_DAILY_LIMIT = int(os.environ.get("GEMINI_DAILY_LIMIT", 500))

# Gemini AI filter — skip contracts scoring below this (0 = off, 100 = all blocked)
GEMINI_MIN_SCORE = int(os.environ.get("GEMINI_MIN_SCORE", 40))

# If true, when all Gemini keys reach their daily quota, alerts for contracts that
# pass on-chain quality gates will still be sent (with a quota badge) rather than silenced.
GEMINI_ALLOW_ON_QUOTA = os.environ.get(
    "GEMINI_ALLOW_ON_QUOTA", "false").strip().lower() in ("true", "1", "yes", "on")

# Bitcoin Ordinals scanner. Disabled by default: the recent-inscriptions feed is
# dominated by BRC-20 token operations and other non-art text, which are not NFTs
# and burned the Gemini quota. Opt in with BTC_ORDINALS_ENABLED=true once the
# scanner does meaningful per-inscription filtering.
BTC_ORDINALS_ENABLED = os.environ.get(
    "BTC_ORDINALS_ENABLED", "false").strip().lower() in ("true", "1", "yes", "on")

# Optional non-trading guidance. Disabled by default so alert-only channels do
# not receive a message that could be mistaken for scanner activity.
COMMUNITY_PULSE_ENABLED = os.environ.get(
    "COMMUNITY_PULSE_ENABLED", "true").strip().lower() in ("true", "1", "yes", "on")

# Collections to watch — add as many as you like
COLLECTIONS = [
    # Uncomment and fill in your collections
    # {
    #     "name": "Collection Name",
    #     "slug": "collection-slug",        # OpenSea slug e.g. "boredapeyachtclub"
    #     "contract": "0x...",              # Contract address
    #     "floor_alert_low": 0.0,           # Alert if floor drops BELOW this (ETH)
    #     "floor_alert_high": 0.0,          # Alert if floor rises ABOVE this (ETH)
    #     "floor_drop_alert_enabled": True, # Set False to suppress only low-floor messages
    #     "silent_floor_drop_alert": False, # Low-floor alert without sound/vibration
    # },
]

# How often to poll (in minutes) — override via Railway environment variables
FLOOR_CHECK_INTERVAL = int(os.environ.get("FLOOR_CHECK_INTERVAL", 5))
MINT_CHECK_INTERVAL = int(os.environ.get("MINT_CHECK_INTERVAL", 1))
DROPS_CHECK_INTERVAL = int(os.environ.get("DROPS_CHECK_INTERVAL", 1))
MINT_COOLDOWN_MINUTES = int(os.environ.get("MINT_COOLDOWN_MINUTES", 10))
MIN_MINTS_THRESHOLD = int(os.environ.get("MIN_MINTS_THRESHOLD", 5))
FLOOR_COOLDOWN_MINUTES = int(os.environ.get("FLOOR_COOLDOWN_MINUTES", 30))
WATCH_FLOOR_CHANGE_PERCENT = float(os.environ.get("WATCH_FLOOR_CHANGE_PERCENT", 10.0))

# A "drop" is only fresh if its CONTRACT was deployed within this many hours.
# Established collections that are still minting (open editions, etc.) are
# skipped as "not a new drop". Falls back to mint-window age when the explorer
# can't provide a deployment timestamp. Tunable via env.
MAX_CONTRACT_AGE_HOURS = int(os.environ.get("MAX_CONTRACT_AGE_HOURS", 48))

# Maximum blocks to catch up when resuming after downtime. If a chain is behind
# by more than this (e.g. after being stopped for hours/days), the bot skips
# the stale backlog and jumps to (current_block - MAX_CATCHUP_BLOCKS) so you get
# real-time alerts immediately instead of spending hours/days reading ancient blocks.
# Set to 0 to jump directly to the live chain tip on every startup.
MAX_CATCHUP_BLOCKS = int(os.environ.get("MAX_CATCHUP_BLOCKS", 2000))

# Solana signature pagination limit when resuming. If the saved signature
# cannot be reached within this many pages of history, the backlog is treated
# as stale and the bot jumps to the newest available signature.
MAX_SOLANA_SIGNATURE_PAGES = int(os.environ.get("MAX_SOLANA_SIGNATURE_PAGES", 3))
