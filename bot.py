import schedule
import time
from floor import check_floors
from mint import check_mints
from drops import check_drops

# Use private config if available (local dev), fall back to public config (Render/contributors)
try:
    from private.config_live import FLOOR_CHECK_INTERVAL, MINT_CHECK_INTERVAL, DROPS_CHECK_INTERVAL
    print("[Config] ✅ Private config loaded")
except ImportError:
    from config import FLOOR_CHECK_INTERVAL, MINT_CHECK_INTERVAL, DROPS_CHECK_INTERVAL
    print("[Config] 📄 Public config loaded")

print("🤖 NFT Alert Bot starting...")
print(f"   Floor checks: every {FLOOR_CHECK_INTERVAL} minutes")
print(f"   Mint checks:  every {MINT_CHECK_INTERVAL} minute(s)")
print(f"   Drop checks:  every {DROPS_CHECK_INTERVAL} minutes")
print("─" * 40)

# Run once immediately on startup
check_floors()
check_mints()
check_drops()

# Schedule recurring checks
schedule.every(FLOOR_CHECK_INTERVAL).minutes.do(check_floors)
schedule.every(MINT_CHECK_INTERVAL).minutes.do(check_mints)
schedule.every(DROPS_CHECK_INTERVAL).minutes.do(check_drops)

while True:
    schedule.run_pending()
    time.sleep(10)