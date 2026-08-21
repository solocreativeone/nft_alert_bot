import asyncio
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from watchlist import add_to_watchlist, remove_from_watchlist, get_watchlist

try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID

# Valid Ethereum address pattern
ETH_ADDRESS_PATTERN = re.compile(r'^0x[a-fA-F0-9]{40}$')

# Command Handlers 

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    await update.message.reply_text(
        "🤖 NFTpulse is live!\n\n"
        "I track floor prices, mints, new drops, and upcoming launches across multiple chains — "
        "and send alerts straight here.\n\n"
        "Quick commands:\n"
        "/watch 0xContract [chain] — add a collection (default: ethereum)\n"
        "  e.g. /watch 0xABC... polygon\n"
        "/unwatch 0xContract — remove a collection\n"
        "/list — show watchlist\n"
        "/live [chain] — check upcoming mints for a chain (default: ethereum)\n"
        "  e.g. /live polygon\n"
        "/help — show all commands\n\n"
        "Supported chains: ethereum, polygon, base, arbitrum, optimism, solana"
    )

async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    from live_drops import get_live_drops_summary, NFTCALENDAR_CHAINS

    chain = context.args[0].strip().lower() if context.args else "ethereum"
    if chain not in NFTCALENDAR_CHAINS:
        supported = ", ".join(NFTCALENDAR_CHAINS.keys())
        await update.message.reply_text(f"❌ Unsupported chain. Supported: {supported}")
        return

    await update.message.reply_text(f"🔍 Fetching upcoming {chain.capitalize()} drops from NFTCalendar...")

    try:
        summary = await asyncio.to_thread(get_live_drops_summary, chain)
        await update.message.reply_text(summary)
    except Exception as e:
        await update.message.reply_text(f"❌ Error checking live mints: {e}")

async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /watch 0xContractAddress [chain]\n"
            "Example: /watch 0x6de7848a77e0910b29723dba879fcba3d8c07b67 polygon\n"
            "Default chain is ethereum."
        )
        return

    contract = context.args[0].strip()
    chain = context.args[1].strip().lower() if len(context.args) > 1 else "ethereum"

    supported_chains = ["ethereum", "polygon", "base", "arbitrum", "optimism", "zora", "robinhood"]
    if chain not in supported_chains:
         await update.message.reply_text(f"❌ Unsupported chain. Supported chains: {', '.join(supported_chains)}")
         return

    # Ethereum addresses apply to EVM chains — must be 0x + 40 hex chars
    if not ETH_ADDRESS_PATTERN.match(contract):
        await update.message.reply_text(
            "❌ Invalid contract address.\n"
            "Must be 0x followed by exactly 40 hex characters.\n"
            "Example: 0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D"
        )
        return

    await update.message.reply_text(f"🔍 Looking up {contract[:10]}... on {chain} via OpenSea...")

    success, result = await asyncio.to_thread(add_to_watchlist, contract, chain)

    if not success:
        await update.message.reply_text(f"❌ {result}")
        return

    col = result
    await update.message.reply_text(
        f"✅ Now watching: {col['name']} [{col['chain'].capitalize()}]\n"
        f"Contract: {col['contract'][:10]}...\n"
        f"Current floor: {col['current_floor']} ETH\n"
        f"🚨 Alert low: {col['floor_alert_low']} ETH\n"
        f"🚀 Alert high: {col['floor_alert_high']} ETH\n"
        f"🔗 https://opensea.io/collection/{col['slug']}"
    )

async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    if not context.args:
        await update.message.reply_text("Usage: /unwatch 0xContractAddress")
        return

    contract = context.args[0].strip()

    if not ETH_ADDRESS_PATTERN.match(contract):
        await update.message.reply_text("❌ Invalid contract address.")
        return

    success, msg = await asyncio.to_thread(remove_from_watchlist, contract)

    if success:
        await update.message.reply_text(f"✅ Removed {contract[:10]}... from watchlist.")
    else:
        await update.message.reply_text(f"❌ {msg}")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    watchlist = get_watchlist()
    if not watchlist:
        await update.message.reply_text("📂 Watchlist is empty.")
        return

    lines = ["📂 <b>Current Watchlist:</b>\n"]
    for idx, item in enumerate(watchlist, 1):
        chain_name = item.get("chain", "ethereum").capitalize()
        lines.append(
            f"{idx}. <a href='https://opensea.io/collection/{item['slug']}'>{item['name']}</a> [{chain_name}]\n"
            f"   Floor: {item.get('current_floor', 0)} ETH"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    await update.message.reply_text(
        "🤖 NFTpulse Bot Commands\n\n"
        "/start — welcome message\n"
        "/watch 0xContract — add a collection to watchlist\n"
        "/unwatch 0xContract — remove a collection\n"
        "/list — show all watched collections\n"
        "/live — check live & upcoming mints now\n"
        "/help — show this message"
    )

# App Builder 

def build_app():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("watch", watch_command))
    app.add_handler(CommandHandler("unwatch", unwatch_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("live", live_command))
    app.add_handler(CommandHandler("help", help_command))
    return app

async def start_polling():
    """Start polling without signal handlers — safe for background threads."""
    app = build_app()
    await app.initialize()
    await app.updater.start_polling(allowed_updates=["message"])
    await app.start()
    print("[Commands] ✅ Telegram command listener started")
    print("[Commands]    /start  /watch  /unwatch  /list  /live  /help")
    await asyncio.Event().wait()

def run_command_listener():
    """Run command listener in its own event loop inside a background thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(start_polling())
    except Exception as e:
        print(f"[Commands Error] {e}")
    finally:
        loop.close()