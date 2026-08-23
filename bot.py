import asyncio
from telegram.ext import Application
from floor import check_floors
from mint import check_mints
from drops import check_drops, wire_healthy_rpcs
from solana_drops import check_solana_drops
from btc_ordinals import check_btc_ordinals
from commands import build_app

# Use private config if available (local dev), fall back to public config
try:
    from private.config_live import FLOOR_CHECK_INTERVAL, MINT_CHECK_INTERVAL, DROPS_CHECK_INTERVAL, TELEGRAM_TOKEN
    print("[Config] ✅ Private config loaded")
except ImportError:
    from config import FLOOR_CHECK_INTERVAL, MINT_CHECK_INTERVAL, DROPS_CHECK_INTERVAL, TELEGRAM_TOKEN
    print("[Config] 📄 Public config loaded")

SOLANA_DROPS_CHECK_INTERVAL = 2 # every 2 minutes
BTC_CHECK_INTERVAL = 5          # every 5 minutes

print("🤖 NFT Alert Bot starting...")
print(f"   Floor checks:      every {FLOOR_CHECK_INTERVAL} minutes")
print(f"   Mint checks:       every {MINT_CHECK_INTERVAL} minute(s)")
print(f"   EVM Drop checks:   every {DROPS_CHECK_INTERVAL} minutes")
print(f"   Solana Drop checks:every {SOLANA_DROPS_CHECK_INTERVAL} minutes")
print(f"   Bitcoin Ordinals:  every {BTC_CHECK_INTERVAL} minutes")
print(f"   Commands:          /watch  /unwatch  /list  /help")
print("─" * 40)

async def loop_task(interval_minutes, task_func):
    """Run an async task in a continuous loop with a sleep interval."""
    # Run once immediately on startup
    try:
        await task_func()
    except Exception as e:
        print(f"[Loop Error] Initial run failed in {task_func.__name__}: {e}")

    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            await task_func()
        except Exception as e:
            print(f"[Loop Error] Failed in {task_func.__name__}: {e}")

async def main():
    # Order RPC endpoints by what actually responds before any scanning starts.
    # Runs off-thread because probing every chain is blocking network I/O.
    await asyncio.to_thread(wire_healthy_rpcs)

    # Build Telegram App
    app = build_app()

    # Schedule recurring on-chain checks as asyncio tasks
    asyncio.create_task(loop_task(FLOOR_CHECK_INTERVAL, check_floors))
    asyncio.create_task(loop_task(MINT_CHECK_INTERVAL, check_mints))
    asyncio.create_task(loop_task(DROPS_CHECK_INTERVAL, check_drops))
    asyncio.create_task(loop_task(SOLANA_DROPS_CHECK_INTERVAL, check_solana_drops))
    asyncio.create_task(loop_task(BTC_CHECK_INTERVAL, check_btc_ordinals))

    # Initialize and run telegram bot
    await app.initialize()
    await app.updater.start_polling(allowed_updates=["message"])
    await app.start()
    
    print("[Commands] ✅ Telegram command listener started")
    print("[Commands]    /start  /watch  /unwatch  /list  /live  /help")
    
    # Wait indefinitely
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")