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

# ── Command Handlers ───────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    await update.message.reply_text(
        "🤖 NFTpulse is live!\n\n"
        "I track floor prices, mints, new drops, and live OpenSea mints — "
        "and send alerts straight here.\n\n"
        "Quick commands:\n"
        "/watch 0xContract — add a collection\n"
        "/unwatch 0xContract — remove a collection\n"
        "/list — show watchlist\n"
        "/live — check live & upcoming mints now\n"
        "/help — show all commands"
    )

async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    await update.message.reply_text("🔍 Checking OpenSea Live & Upcoming Mints...")

    try:
        from live_drops import get_live_drops_summary
        summary = get_live_drops_summary()
        await update.message.reply_text(summary)
    except Exception as e:
        await update.message.reply_text(f"❌ Error checking live mints: {e}")

async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /watch 0xContractAddress\n"
            "Example: /watch 0x6de7848a77e0910b29723dba879fcba3d8c07b67"
        )
        return

    contract = context.args[0].strip()

    # Strict Ethereum address validation — must be 0x + 40 hex chars
    if not ETH_ADDRESS_PATTERN.match(contract):
        await update.message.reply_text(
            "❌ Invalid contract address.\n"
            "Must be 0x followed by exactly 40 hex characters.\n"
            "Example: 0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D"
        )
        return

    await update.message.reply_text(f"🔍 Looking up {contract[:10]}... on OpenSea...")

    success, result = add_to_watchlist(contract)

    if not success:
        await update.message.reply_text(f"❌ {result}")
        return

    col = result
    await update.message.reply_text(
        f"✅ Now watching: {col['name']}\n"
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

    success, msg = remove_from_watchlist(contract)

    if success:
        await update.message.reply_text(f"✅ Removed {contract[:10]}... from watchlist.")
    else:
        await update.message.reply_text(f"❌ {msg}")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    watchlist = get_watchlist()

    if not watchlist:
        await update.message.reply_text(
            "👁 Watchlist is empty.\n"
            "Use /watch 0xContract to add a collection."
        )
        return

    lines = ["👁 Currently watching:\n"]
    for i, col in enumerate(watchlist, 1):
        lines.append(
            f"{i}. {col['name']}\n"
            f"   Floor alerts: <{col['floor_alert_low']} | >{col['floor_alert_high']} ETH\n"
            f"   {col['contract'][:10]}...\n"
        )

    await update.message.reply_text("\n".join(lines))

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

# ── App Builder ────────────────────────────────────────────────────

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