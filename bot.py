# bot.py — Vaulta admin bot (v2 / Mini App mode)
#
# The buy flow now lives in the Mini App (see miniapp_server.py + webapp/).
# This process stays running for:
#   - /start — greets the user with a button that opens the Mini App
#   - Admin commands (/addcoin, /removecoin, /listcoins, /orders, /complete, /adminhelp)
#   - PreCheckoutQuery + SuccessfulPayment — Telegram only ever delivers these
#     to the bot itself (never to a web server), so this half of the payment
#     flow can't move into the Mini App and has to stay here.
#
# coins.json / orders.json / langs.json are SHARED with miniapp_server.py via
# vaulta_common.py, using a cross-process file lock so both processes can
# write safely without clobbering each other.

import os
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    PreCheckoutQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

import vaulta_common as vc

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

WEBAPP_URL = os.environ.get("VAULTA_WEBAPP_URL")  # e.g. https://your-domain.com/
if not WEBAPP_URL:
    raise RuntimeError(
        "VAULTA_WEBAPP_URL env var is not set. It must be an HTTPS URL pointing "
        "at the running Mini App (miniapp_server.py), e.g. https://your-domain.com/ "
        "— Telegram Mini Apps require HTTPS even for local testing (use ngrok or similar)."
    )

ADMIN_HELP_TEXT = (
    "🛠️ *Admin Commands*\n\n"
    "/addcoin SYMBOL coingecko_id — Add a coin (e.g. /addcoin ETH ethereum)\n"
    "/removecoin SYMBOL — Remove a coin\n"
    "/listcoins — List all coins currently offered\n"
    "/orders [status] — List orders, default 'pending'. Status: pending, paid, completed, cancelled, all\n"
    "/complete ORDER_ID — Mark a paid order as fulfilled (sends buyer a confirmation)\n"
    "/adminhelp — Show this list\n\n"
    "_Notifications sent to you automatically:_\n"
    "⏳ New pending order — as soon as the Mini App creates an invoice (before payment)\n"
    "💰 New payment — when a buyer actually pays"
)

def is_admin(update: Update) -> bool:
    return update.effective_user.id == vc.ADMIN_ID

# ==============================
# START — opens the Mini App
# ==============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🪙 Open Vaulta", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await update.message.reply_text(
        "✨ *VAULTA* ✨\n"
        "_Buy crypto instantly with Telegram Stars_\n\n"
        "Tap below to open the app 👇",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Use /start to open Vaulta.")
    if is_admin(update):
        await update.message.reply_text(ADMIN_HELP_TEXT, parse_mode="Markdown")

# ==============================
# PAYMENTS — must stay on the bot; Telegram never routes these to a web server
# ==============================

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    order_id = query.invoice_payload
    # Re-read from disk — the order may have been created moments ago by the
    # OTHER process (miniapp_server.py), not this one.
    current_orders = vc.reload_json(vc.ORDERS_FILE, {})
    order = current_orders.get(order_id)

    if not order:
        await query.answer(ok=False, error_message="This order no longer exists. Please reopen the app and try again.")
        return
    if order.get("status") != "pending":
        await query.answer(ok=False, error_message="This order has already been processed.")
        return

    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    order_id = payment.invoice_payload

    current_orders = vc.reload_json(vc.ORDERS_FILE, {})
    if order_id in current_orders:
        current_orders[order_id]['status'] = 'paid'
        current_orders[order_id]['paid_at'] = datetime.now().isoformat()
        await vc.save_json_async(vc.ORDERS_FILE, current_orders)

        order = current_orders[order_id]
        await context.bot.send_message(
            chat_id=vc.ADMIN_ID,
            text=(
                f"💰 NEW PAYMENT\n\n"
                f"Order: {order_id}\n"
                f"User: @{order['username']}\n"
                f"Coin: {order['amount']} {order['coin']}\n"
                f"Wallet: {order['wallet']}\n"
                f"Stars: {payment.total_amount:,}\n\n"
                f"Mark as sent with: /complete {order_id}"
            )
        )
    else:
        logger.warning(f"Received payment for unknown order_id={order_id}")

    await update.message.reply_text(f"✅ Payment Successful!\nOrder: {order_id}")

# ==============================
# ADMIN COMMANDS
# ==============================

async def adminhelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Not authorised.")
        return
    await update.message.reply_text(ADMIN_HELP_TEXT, parse_mode="Markdown")

async def addcoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Not authorised.")
        return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /addcoin SYMBOL coingecko_id")
        return
    symbol = context.args[0].upper()
    coin_id = context.args[1].lower()
    current_coins = vc.reload_json(vc.COINS_FILE, {"BTC": "bitcoin", "SOL": "solana", "XRP": "ripple"})
    current_coins[symbol] = coin_id
    await vc.save_json_async(vc.COINS_FILE, current_coins)
    await update.message.reply_text(f"✅ Added {symbol}")

async def removecoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Not authorised.")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /removecoin SYMBOL")
        return
    symbol = context.args[0].upper()
    current_coins = vc.reload_json(vc.COINS_FILE, {})
    if symbol not in current_coins:
        await update.message.reply_text(f"❌ {symbol} not found.")
        return
    del current_coins[symbol]
    await vc.save_json_async(vc.COINS_FILE, current_coins)
    await update.message.reply_text(f"✅ Removed {symbol}")

async def listcoins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Not authorised.")
        return
    current_coins = vc.reload_json(vc.COINS_FILE, {})
    msg = "📋 Coins\n\n"
    for symbol, coin_id in current_coins.items():
        msg += f"🪙 {symbol} → {coin_id}\n"
    await update.message.reply_text(msg)

async def complete_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Not authorised.")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /complete ORDER_ID")
        return
    order_id = context.args[0]
    current_orders = vc.reload_json(vc.ORDERS_FILE, {})
    order = current_orders.get(order_id)
    if not order:
        await update.message.reply_text(f"❌ {order_id} not found.")
        return
    if order.get("status") != "paid":
        await update.message.reply_text(f"❌ Order is '{order.get('status')}', not 'paid' — can't complete.")
        return

    order["status"] = "completed"
    order["completed_at"] = datetime.now().isoformat()
    await vc.save_json_async(vc.ORDERS_FILE, current_orders)

    await update.message.reply_text(f"✅ {order_id} marked completed.")

    current_langs = vc.reload_json(vc.LANGS_FILE, {})
    buyer_lang = current_langs.get(str(order["user_id"]), vc.DEFAULT_LANG)
    completed_text = {
        "en": f"✅ Your order {order_id} has been fulfilled! {order['amount']} {order['coin']} sent to your wallet.",
        "ru": f"✅ Ваш заказ {order_id} выполнен! {order['amount']} {order['coin']} отправлены на ваш кошелёк.",
    }.get(buyer_lang, f"✅ Your order {order_id} has been fulfilled! {order['amount']} {order['coin']} sent to your wallet.")

    try:
        await context.bot.send_message(chat_id=order["user_id"], text=completed_text)
    except Exception:
        logger.exception(f"Could not notify user for order {order_id}")

async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("❌ Not authorised.")
        return

    current_orders = vc.reload_json(vc.ORDERS_FILE, {})
    status_filter = context.args[0].lower() if context.args else "pending"

    if status_filter == "all":
        matching = list(current_orders.items())
    else:
        matching = [(oid, o) for oid, o in current_orders.items() if o.get("status") == status_filter]

    matching.sort(key=lambda item: item[1].get("created", ""), reverse=True)

    if not matching:
        await update.message.reply_text(f"📋 No orders with status '{status_filter}'.")
        return

    counts = {}
    for _, o in current_orders.items():
        s = o.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    counts_line = " | ".join(f"{k}: {v}" for k, v in counts.items())

    header = f"📋 Orders — status: '{status_filter}' ({len(matching)})\n({counts_line})\n\n"
    msg = header
    shown = 0
    MAX_SHOWN = 25

    for order_id, order in matching:
        if shown >= MAX_SHOWN:
            msg += f"\n…and {len(matching) - MAX_SHOWN} more. Narrow with /orders {status_filter} or check orders.json directly."
            break
        emoji = {'pending': '⏳', 'paid': '🟡', 'completed': '✅', 'cancelled': '🚫'}.get(order.get('status'), '❓')
        line = (
            f"{emoji} {order_id}\n"
            f"   @{order.get('username', 'Unknown')} — {order.get('amount')} {order.get('coin')} "
            f"({order.get('stars', 0):,}⭐)\n"
            f"   wallet: {order.get('wallet', 'n/a')}\n"
            f"   created: {order.get('created', 'n/a')}\n\n"
        )
        if len(msg) + len(line) > 4000:
            msg += f"\n…truncated. {len(matching) - shown} more not shown."
            break
        msg += line
        shown += 1

    await update.message.reply_text(msg)

# ==============================
# ERROR / SHUTDOWN
# ==============================

async def error_handler(update, context):
    logger.error("Exception while handling update:", exc_info=context.error)

async def _on_shutdown(app):
    await vc.close_http_session()

# ==============================
# MAIN
# ==============================

def main():
    request = HTTPXRequest(
        connect_timeout=10.0,
        read_timeout=10.0,
        write_timeout=10.0,
        pool_timeout=10.0,
    )
    app = Application.builder().token(vc.BOT_TOKEN).request(request).build()
    app.post_shutdown = _on_shutdown

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("addcoin", addcoin))
    app.add_handler(CommandHandler("removecoin", removecoin))
    app.add_handler(CommandHandler("listcoins", listcoins))
    app.add_handler(CommandHandler("complete", complete_order))
    app.add_handler(CommandHandler("orders", admin_orders))
    app.add_handler(CommandHandler("adminhelp", adminhelp))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_error_handler(error_handler)

    print("⭐ Vaulta admin bot running (v2 — Mini App mode)")
    print(f"🌐 Mini App URL: {WEBAPP_URL}")

    app.run_polling()

if __name__ == "__main__":
    main()
