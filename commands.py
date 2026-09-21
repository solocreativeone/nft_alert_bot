import asyncio
import functools
import inspect
import os
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from watchlist import add_to_watchlist, remove_from_watchlist, get_watchlist, get_collection_url
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
try:
    from private.config_live import WATCH_FLOOR_CHANGE_PERCENT
except ImportError:
    from config import WATCH_FLOOR_CHANGE_PERCENT

# Valid Ethereum address pattern
ETH_ADDRESS_PATTERN = re.compile(r'^0x[a-fA-F0-9]{40}$')

# Auto-cleanup delay in seconds (default: 15 seconds)
COMMAND_CLEANUP_SECONDS = float(os.environ.get("COMMAND_CLEANUP_SECONDS", 15.0))


async def safe_delete_message(message):
    """Attempt to delete a message, handling errors gracefully without crashing."""
    if message is None:
        return
    try:
        delete_fn = getattr(message, "delete", None)
        if callable(delete_fn):
            res = delete_fn()
            if inspect.isawaitable(res):
                await res
    except Exception:
        # Gracefully ignore deletion failures (missing permissions, already deleted, etc.)
        pass


async def _delete_messages_after_delay(messages, delay=COMMAND_CLEANUP_SECONDS):
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        for msg in messages:
            await safe_delete_message(msg)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


def schedule_message_cleanup(messages, delay=COMMAND_CLEANUP_SECONDS):
    """Schedule background deletion of messages after delay seconds."""
    if not isinstance(messages, (list, tuple, set)):
        messages = [messages]
    valid_msgs = [m for m in messages if m is not None]
    if not valid_msgs:
        return None
    return asyncio.create_task(_delete_messages_after_delay(valid_msgs, delay))


def _watch_confirmation(col):
    """Render the signal-based confirmation shared by /watch entry points."""
    floor_display = format_floor_display(
        floor=col.get("current_floor"),
        eth_usd_price=get_eth_usd_price(),
        is_free_mint=bool(col.get("is_free_mint") or col.get("free_mint")),
        is_alert=False,
    )
    lines = [
        f"✅ Now watching: {col.get('name', 'Unknown')} "
        f"[{col.get('chain', 'ethereum').capitalize()}]",
        f"Contract: {shorten_address(col.get('contract', ''))}",
        f"📊 Floor signal: ±{_watch_floor_change_percent():g}%",
    ]
    if floor_display:
        lines.append(floor_display)
    collection_url = get_collection_url(col)
    if collection_url:
        lines.append(f"🔗 {collection_url}")
    return "\n".join(lines)


def _watch_floor_change_percent():
    """Use the same configured threshold displayed by /watch and floor checks."""
    try:
        return float(os.environ.get("WATCH_FLOOR_CHANGE_PERCENT", WATCH_FLOOR_CHANGE_PERCENT))
    except (TypeError, ValueError):
        return float(WATCH_FLOOR_CHANGE_PERCENT)


class _CleanupMessageProxy:
    """Forward a command message while recording messages sent by reply_text."""

    def __init__(self, message, responses):
        self._message = message
        self._responses = responses

    def __getattr__(self, name):
        return getattr(self._message, name)

    async def reply_text(self, *args, **kwargs):
        result = self._message.reply_text(*args, **kwargs)
        response = await result if inspect.isawaitable(result) else result
        if response is not None:
            self._responses.append(response)
        return response


class _CleanupUpdateProxy:
    """Expose the original update except for its tracked command message."""

    def __init__(self, update, message):
        self._update = update
        self.message = message

    def __getattr__(self, name):
        return getattr(self._update, name)


def auto_cleanup(handler):
    """Decorator to automatically delete user command message and bot responses after 15 seconds."""
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update or not hasattr(update, "effective_chat") or not update.effective_chat:
            return await handler(update, context, *args, **kwargs)

        allowed_ids = {cid.strip() for cid in str(CHAT_ID).split(",") if cid.strip()}
        if str(update.effective_chat.id) not in allowed_ids:
            return

        user_msg = getattr(update, "message", None)
        bot_responses = []
        command_update = update
        if user_msg is not None and hasattr(user_msg, "reply_text"):
            command_update = _CleanupUpdateProxy(
                update,
                _CleanupMessageProxy(user_msg, bot_responses),
            )

        try:
            return await handler(command_update, context, *args, **kwargs)
        finally:
            delay = float(os.environ.get("COMMAND_CLEANUP_SECONDS", COMMAND_CLEANUP_SECONDS))
            messages_to_delete = []
            if user_msg is not None:
                messages_to_delete.append(user_msg)
            messages_to_delete.extend(bot_responses)
            schedule_message_cleanup(messages_to_delete, delay=delay)

    return wrapper


# Command Handlers

@auto_cleanup
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_ids = {cid.strip() for cid in str(CHAT_ID).split(",") if cid.strip()}
    if str(update.effective_chat.id) not in allowed_ids:
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
        "ethereum, polygon, base, arbitrum, optimism, solana, arc"
    )


@auto_cleanup
async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_ids = {cid.strip() for cid in str(CHAT_ID).split(",") if cid.strip()}
    if str(update.effective_chat.id) not in allowed_ids:
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


@auto_cleanup
async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_ids = {cid.strip() for cid in str(CHAT_ID).split(",") if cid.strip()}
    if str(update.effective_chat.id) not in allowed_ids:
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
        "arc",
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

    await update.message.reply_text(_watch_confirmation(result))


@auto_cleanup
async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_ids = {cid.strip() for cid in str(CHAT_ID).split(",") if cid.strip()}
    if str(update.effective_chat.id) not in allowed_ids:
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
        return "📂 Current Watchlist:\n\nNo collections currently being watched.", None

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
        collection_url = get_collection_url(item)

        name_display = (
            f"<a href='{collection_url}'>{escape_html(item['name'])}</a>"
            if collection_url
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

        btn_text = f"🗑 Unwatch {item.get('name', 'collection')[:45]}"
        button_rows.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"unwatch:{contract.lower()}")
        ])

    body = "\n\n".join(items_output)
    full_text = f"📂 <b>Current Watchlist:</b>\n\n{body}"
    reply_markup = InlineKeyboardMarkup(button_rows) if button_rows else None
    return full_text, reply_markup


@auto_cleanup
async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_ids = {cid.strip() for cid in str(CHAT_ID).split(",") if cid.strip()}
    if str(update.effective_chat.id) not in allowed_ids:
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
    allowed_ids = {cid.strip() for cid in str(CHAT_ID).split(",") if cid.strip()}
    if str(update.effective_chat.id) not in allowed_ids:
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

    reply_text = _watch_confirmation(result)
    if query.message:
        await query.message.reply_text(reply_text)


async def unwatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unwatch callback button click from watchlist."""
    query = update.callback_query
    if not query:
        return
    allowed_ids = {cid.strip() for cid in str(CHAT_ID).split(",") if cid.strip()}
    if str(update.effective_chat.id) not in allowed_ids:
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



@auto_cleanup
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Report scan checkpoint position and Gemini key-pool quota usage.

    Lets you confirm from Telegram that the bot resumed from its saved position
    and see which API keys still have quota, without shell access to the host.
    """
    allowed_ids = {cid.strip() for cid in str(CHAT_ID).split(",") if cid.strip()}
    if str(update.effective_chat.id) not in allowed_ids:
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


@auto_cleanup
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed_ids = {cid.strip() for cid in str(CHAT_ID).split(",") if cid.strip()}
    if str(update.effective_chat.id) not in allowed_ids:
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

async def _log_error(update, context):
    print(f"[ERROR HANDLER] Exception while processing update: {context.error!r}")
    import traceback
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)


def build_app():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_error_handler(_log_error)

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
