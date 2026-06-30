import schedule
import time
import threading
from floor import check_floors
from mint import check_mints
from drops import check_drops
from calendar_tracker import check_calendar
from live_drops import check_live_drops
from commands import run_command_listener

# Use private config if available (local dev), fall back to public config
try:
    from private.config_live import FLOOR_CHECK_INTERVAL, MINT_CHECK_INTERVAL, DROPS_CHECK_INTERVAL
    print("[Config] ✅ Private config loaded")
except ImportError:
    from config import FLOOR_CHECK_INTERVAL, MINT_CHECK_INTERVAL, DROPS_CHECK_INTERVAL
    print("[Config] 📄 Public config loaded")

CALENDAR_CHECK_INTERVAL = 360   # every 6 hours
LIVE_DROPS_CHECK_INTERVAL = 10  # every 10 minutes — catches live mints fast

print("🤖 NFT Alert Bot starting...")
print(f"   Floor checks:      every {FLOOR_CHECK_INTERVAL} minutes")
print(f"   Mint checks:       every {MINT_CHECK_INTERVAL} minute(s)")
print(f"   Drop checks:       every {DROPS_CHECK_INTERVAL} minutes")
print(f"   Live drops:        every {LIVE_DROPS_CHECK_INTERVAL} minutes")
print(f"   Calendar checks:   every {CALENDAR_CHECK_INTERVAL} minutes (6 hours)")
print(f"   Commands:          /watch  /unwatch  /list  /help")
print("─" * 40)

# Run once immediately on startup
check_floors()
check_mints()
check_drops()
check_live_drops()
check_calendar()

# Schedule recurring checks
schedule.every(FLOOR_CHECK_INTERVAL).minutes.do(check_floors)
schedule.every(MINT_CHECK_INTERVAL).minutes.do(check_mints)
schedule.every(DROPS_CHECK_INTERVAL).minutes.do(check_drops)
schedule.every(LIVE_DROPS_CHECK_INTERVAL).minutes.do(check_live_drops)
schedule.every(CALENDAR_CHECK_INTERVAL).minutes.do(check_calendar)

# Run Telegram command listener in background thread
t = threading.Thread(target=run_command_listener, daemon=True)
t.start()

# Main schedule loop
while True:
    schedule.run_pending()
    time.sleep(10)