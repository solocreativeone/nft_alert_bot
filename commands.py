import asyncio
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from watchlist import add_to_watchlist, remove_from_watchlist, get_watchlist
from price_utils import (
    shorten_address,
    get_eth_usd_price,
    format_floor_display,
)
from notifier import escape_html

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
        "Track floor prices, mints, new drops, and upcoming launches across multiple chains. "
        "Get real-time alerts straight here.\n\n"

        "📋 Quick commands\n"
        "/watch 0xContract [chain] : Add a collection\n"
        "  Default chain: ethereum\n"
        "  Example: /watch 0xABC... polygon\n\n"
        "/unwatch 0xContract : Remove a collection\n"
        "/list : Show your watchlist\n"
        "/live [chain] : Check upcoming mints\n"
        "  Default chain: ethereum\n"
        "  Example: /live polygon\n"
        "/help : Show all commands\n\n"

        "🌐 Supported chains\n"
        "ethereum, polygon, base, arbitrum, optimism, solana"
    )


async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    from live_drops import get_live_drops_summary, NFTCALENDAR_CHAINS

    chain = context.args[0].strip().lower() if context.args else "ethereum"

    if chain not in NFTCALENDAR_CHAINS:
        supported = ", ".join(NFTCALENDAR_CHAINS.keys())
        await update.message.reply_text(
            f"❌ Unsupported chain. Supported: {supported}"
        )
        return

    await update.message.reply_text(
        f"🔍 Fetching upcoming {chain.capitalize()} drops from NFTCalendar..."
    )

    try:
        summary = await asyncio.to_thread(
            get_live_drops_summary,
            chain
        )
        await update.message.reply_text(summary)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Error checking live mints: {e}"
        )


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
    chain = (
        context.args[1].strip().lower()
        if len(context.args) > 1
        else "ethereum"
    )

    supported_chains = [
        "ethereum",
        "polygon",
        "base",
        "arbitrum",
        "optimism",
        "robinhood",
    ]

    if chain not in supported_chains:
        await update.message.reply_text(
            f"❌ Unsupported chain. Supported chains: "
            f"{', '.join(supported_chains)}"
        )
        return

    # Ethereum addresses apply to EVM chains
    # Must be 0x + 40 hexadecimal characters
    if not ETH_ADDRESS_PATTERN.match(contract):
        await update.message.reply_text(
            "❌ Invalid contract address.\n"
            "Must be 0x followed by exactly 40 hex characters.\n"
            "Example: 0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D"
        )
        return

    await update.message.reply_text(
        f"🔍 Looking up {shorten_address(contract)} on {chain} via OpenSea..."
    )

    success, result = await asyncio.to_thread(
        add_to_watchlist,
        contract,
        chain
    )

    if not success:
        await update.message.reply_text(f"❌ {result}")
        return

    col = result
    short_addr = shorten_address(col["contract"])
    eth_usd_price = get_eth_usd_price()
    floor_val = col.get("current_floor")
    is_free = bool(col.get("is_free_mint") or col.get("free_mint"))
    floor_display = format_floor_display(
        floor=floor_val,
        eth_usd_price=eth_usd_price,
        is_free_mint=is_free,
        is_alert=False,
    )
    if floor_display == "🆓 Free Mint":
        floor_line = "🆓 Free Mint\n"
    elif floor_display:
        floor_line = f"Current {floor_display[0].lower() + floor_display[1:]}\n"
    else:
        floor_line = ""

    await update.message.reply_text(
        f"✅ Now watching: {col['name']} "
        f"[{col['chain'].capitalize()}]\n"
        f"Contract: {short_addr}\n"
        f"{floor_line}"
        f"🚨 Alert low: {col['floor_alert_low']} ETH\n"
        f"🚀 Alert high: {col['floor_alert_high']} ETH\n"
        f"🔗 https://opensea.io/collection/{col['slug']}"
    )


async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /unwatch 0xContractAddress"
        )
        return

    contract = context.args[0].strip()

    if not ETH_ADDRESS_PATTERN.match(contract):
        await update.message.reply_text(
            "❌ Invalid contract address."
        )
        return

    success, msg = await asyncio.to_thread(
        remove_from_watchlist,
        contract
    )

    short_addr = shorten_address(contract)
    if success:
        await update.message.reply_text(
            f"✅ Removed {short_addr} from watchlist."
        )
    else:
        await update.message.reply_text(f"❌ {msg}")


def render_watchlist_message(watchlist, eth_usd_price=None):
    """Render watchlist text and inline keyboard markup with /unwatch actions."""
    if not watchlist:
        return "📂 Watchlist is empty.", None

    if eth_usd_price is None:
        eth_usd_price = get_eth_usd_price()

    items_output = []
    button_rows = []

    for idx, item in enumerate(watchlist, 1):
        chain_name = item.get(
            "chain",
            "ethereum"
        ).capitalize()
        contract = item.get("contract", "")
        short_addr = shorten_address(contract)
        slug = item.get("slug", "")

        name_display = (
            f"<a href='https://opensea.io/collection/{slug}'>{escape_html(item['name'])}</a>"
            if slug
            else escape_html(item.get("name", "Unknown"))
        )

        item_lines = [
            f"{idx}. {name_display} [{chain_name}]",
            f"   Contract: {short_addr}",
        ]

        floor_val = item.get("current_floor")
        is_free_mint = bool(item.get("is_free_mint") or item.get("free_mint"))

        floor_str = format_floor_display(
            floor=floor_val,
            eth_usd_price=eth_usd_price,
            is_free_mint=is_free_mint,
            is_alert=False,
        )
        if floor_str:
            item_lines.append(f"   {floor_str}")

        items_output.append("\n".join(item_lines))

        btn_text = "/unwatch" if len(watchlist) == 1 else f"/unwatch {idx}. {item.get('name', '')[:20]}"
        button_rows.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"unwatch:{contract.lower()}")
        ])

    body = "\n\n".join(items_output)
    full_text = f"📂 <b>Current Watchlist:</b>\n\n{body}"
    reply_markup = InlineKeyboardMarkup(button_rows) if button_rows else None
    return full_text, reply_markup


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    watchlist = get_watchlist()
    text, reply_markup = render_watchlist_message(watchlist)

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )


async def watch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /watch callback button click from alert messages."""
    query = update.callback_query
    if not query:
        return
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    await query.answer()

    data = query.data or ""
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != "watch":
        return

    chain = parts[1].strip().lower()
    contract = parts[2].strip().lower()

    if not ETH_ADDRESS_PATTERN.match(contract):
        if query.message:
            await query.message.reply_text("❌ Invalid contract address.")
        return

    success, result = await asyncio.to_thread(
        add_to_watchlist,
        contract,
        chain
    )

    if not success:
        if query.message:
            await query.message.reply_text(f"❌ {result}")
        return

    col = result
    short_addr = shorten_address(col.get("contract", contract))
    eth_usd_price = get_eth_usd_price()
    floor_val = col.get("current_floor")
    is_free = bool(col.get("is_free_mint") or col.get("free_mint"))

    floor_display = format_floor_display(
        floor=floor_val,
        eth_usd_price=eth_usd_price,
        is_free_mint=is_free,
        is_alert=False,
    )
    if floor_display == "🆓 Free Mint":
        floor_line = "🆓 Free Mint\n"
    elif floor_display:
        floor_line = f"Current {floor_display[0].lower() + floor_display[1:]}\n"
    else:
        floor_line = ""

    reply_text = (
        f"✅ Now watching: {col['name']} "
        f"[{col['chain'].capitalize()}]\n"
        f"Contract: {short_addr}\n"
        f"{floor_line}"
        f"🚨 Alert low: {col['floor_alert_low']} ETH\n"
        f"🚀 Alert high: {col['floor_alert_high']} ETH\n"
        f"🔗 https://opensea.io/collection/{col['slug']}"
    )
    if query.message:
        await query.message.reply_text(reply_text)


async def unwatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unwatch callback button click from watchlist."""
    query = update.callback_query
    if not query:
        return
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    await query.answer()

    data = query.data or ""
    parts = data.split(":", 1)
    if len(parts) < 2 or parts[0] != "unwatch":
        return

    contract = parts[1].strip().lower()
    success, msg = await asyncio.to_thread(
        remove_from_watchlist,
        contract
    )

    short_addr = shorten_address(contract)
    if success:
        if query.message:
            await query.message.reply_text(
                f"✅ Removed {short_addr} from watchlist."
            )
    else:
        if query.message:
            await query.message.reply_text(f"❌ {msg}")



async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Report scan checkpoint position and Gemini key-pool quota usage.

    Lets you confirm from Telegram that the bot resumed from its saved position
    and see which API keys still have quota, without shell access to the host.
    """
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    import checkpoint
    from gemini_filter import pool_status

    lines = ["📊 <b>Bot Status</b>\n"]

    # Blockchain checkpoint positions
    blocks = checkpoint.all_blocks()

    if blocks:
        lines.append("<b>Last processed block:</b>")

        for chain in sorted(blocks):
            lines.append(
                f"  {chain}: <code>{blocks[chain]}</code>"
            )
    else:
        lines.append(
            "<b>Last processed block:</b> none yet (cold start)"
        )

    # Processed mint memory
    processed = sum(
        checkpoint.seen_count(section)
        for section in checkpoint.SEEN_LIMITS
    )

    lines.append(
        f"\n<b>Processed mints remembered:</b> {processed}"
    )

    # Gemini key pool
    status = pool_status()

    lines.append(
        f"\n<b>Gemini keys:</b> "
        f"{status['available_keys']}/{status['total_keys']} available"
    )

    for row in status["keys"]:
        mark = "🟢" if row["available"] else "🔴"
        active = " (active)" if row["active"] else ""
        cooling = " cooling down" if row["cooling_down"] else ""

        lines.append(
            f"  {mark} key #{row['index']}: "
            f"{row['used_today']}/{row['limit']} "
            f"used today{active}{cooling}"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(CHAT_ID).strip():
        return

    await update.message.reply_text(
        "🤖 NFTpulse Bot Commands\n\n"
        "/start - welcome message\n"
        "/watch 0xContract - add a collection to watchlist\n"
        "/unwatch 0xContract - remove a collection\n"
        "/list - show all watched collections\n"
        "/live - check live & upcoming mints now\n"
        "/help - show this message"
    )


# App Builder

def build_app():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(
        CommandHandler("start", start_command)
    )
    app.add_handler(
        CommandHandler("watch", watch_command)
    )
    app.add_handler(
        CommandHandler("unwatch", unwatch_command)
    )
    app.add_handler(
        CommandHandler("list", list_command)
    )
    app.add_handler(
        CommandHandler("live", live_command)
    )
    app.add_handler(
        CommandHandler("status", status_command)
    )
    app.add_handler(
        CommandHandler("help", help_command)
    )
    app.add_handler(
        CallbackQueryHandler(watch_callback, pattern=r"^watch:")
    )
    app.add_handler(
        CallbackQueryHandler(unwatch_callback, pattern=r"^unwatch:")
    )

    return app


async def start_polling():
    """Start polling without signal handlers, safe for background threads."""
    app = build_app()

    await app.initialize()
    await app.updater.start_polling(
        allowed_updates=["message", "callback_query"]
    )
    await app.start()

    print("[Commands] ✅ Telegram command listener started")
    print(
        "[Commands]    "
        "/start  /watch  /unwatch  /list  /live  /status  /help"
    )

    await asyncio.Event().wait()


def run_command_listener():
    """Run command listener in its own event loop inside a background thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(
            start_polling()
        )
    except Exception as e:
        print(f"[Commands Error] {e}")
    finally:
        loop.close()