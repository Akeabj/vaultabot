# miniapp_server.py — Vaulta Mini App backend (v2)
#
# Serves the Mini App frontend (webapp/) and a small JSON API that the
# frontend calls. Runs as ITS OWN process, alongside bot.py (which still
# handles admin commands + Stars payment callbacks). Both processes share
# coins.json / orders.json / langs.json via vaulta_common.py.
#
# Run with:
#   uvicorn miniapp_server:app --host 0.0.0.0 --port 8000
#
# This MUST be reachable over HTTPS for Telegram to load it as a Mini App —
# put a reverse proxy (nginx/Caddy) with a real cert in front for
# production, or use a tunnel (ngrok, cloudflared) while testing locally.

import logging
import random
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from telegram import Bot, LabeledPrice

import vaulta_common as vc

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("miniapp_server")

app = FastAPI(title="Vaulta Mini App API")
bot = Bot(token=vc.BOT_TOKEN)

app.mount("/static", StaticFiles(directory="webapp"), name="static")

@app.get("/")
async def index():
    return FileResponse("webapp/index.html")

# ==============================
# REQUEST MODELS
# ==============================

class QuoteRequest(BaseModel):
    symbol: str
    amount: float

class OrderRequest(BaseModel):
    initData: str
    symbol: str
    amount: float
    wallet: str

# ==============================
# HELPERS
# ==============================

def require_user(init_data: str) -> dict:
    """Verifies the Mini App's initData signature and returns the Telegram
    user dict. Raises 401 if it's missing/invalid — this is what stops
    someone from just editing the page's JS to claim to be another user."""
    user = vc.validate_init_data(init_data)
    if user is None or "id" not in user:
        raise HTTPException(status_code=401, detail="Invalid or expired Telegram session. Please reopen the app.")
    return user

def new_order_id(orders: dict) -> str:
    order_id = f"ORD-{datetime.now().strftime('%Y%m%d')}-{random.randint(1000, 9999)}"
    while order_id in orders:  # avoid the (rare) collision
        order_id = f"ORD-{datetime.now().strftime('%Y%m%d')}-{random.randint(1000, 9999)}"
    return order_id

# ==============================
# API
# ==============================

@app.get("/api/coins")
async def api_coins():
    coins = vc.reload_json(vc.COINS_FILE, {"BTC": "bitcoin", "SOL": "solana", "XRP": "ripple"})
    return {"coins": [{"symbol": s, "label": vc.get_wallet_label(s)} for s in coins.keys()]}

@app.get("/api/prices")
async def api_prices():
    coins = vc.reload_json(vc.COINS_FILE, {})
    prices = await vc.get_coin_prices(list(coins.values()))
    return {symbol: prices.get(coin_id) for symbol, coin_id in coins.items()}

@app.get("/api/popular")
async def api_popular():
    """Curated coin set (price + 24h change) for the home screen's Popular
    Coins cards. Prefers SOL/BTC/XRP, but falls back to whatever's actually
    configured — coins are admin-managed via /addcoin and /removecoin, so
    the preferred set may not always exist."""
    coins = vc.reload_json(vc.COINS_FILE, {"BTC": "bitcoin", "SOL": "solana", "XRP": "ripple"})

    preferred = ["SOL", "BTC", "XRP"]
    symbols = [s for s in preferred if s in coins]
    for s in coins:
        if len(symbols) >= 3:
            break
        if s not in symbols:
            symbols.append(s)
    symbols = symbols[:3]

    if not symbols:
        return {"coins": []}

    data = await vc.get_coin_market_data([coins[s] for s in symbols])
    return {
        "coins": [
            {
                "symbol": s,
                "price": data.get(coins[s], {}).get("price"),
                "change_24h": data.get(coins[s], {}).get("change_24h"),
                "image": data.get(coins[s], {}).get("image"),
            }
            for s in symbols
        ]
    }

@app.post("/api/quote")
async def api_quote(req: QuoteRequest):
    """Live price + full cost breakdown for a proposed purchase, before the
    user commits to an order. No auth needed — nothing is written yet."""
    coins = vc.reload_json(vc.COINS_FILE, {})
    symbol = req.symbol.upper()
    if symbol not in coins:
        raise HTTPException(status_code=400, detail="That coin isn't available anymore.")
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Enter a positive number, e.g. 0.5")

    try:
        price = await vc.get_coin_price(coins[symbol])
    except Exception:
        logger.exception("Price fetch failed in /api/quote")
        raise HTTPException(status_code=502, detail="Price fetch failed. Try again.")

    purchase = vc.compute_purchase(symbol, req.amount, price)
    purchase["wallet_label"] = vc.get_wallet_label(symbol)
    return purchase

@app.post("/api/order")
async def api_order(req: OrderRequest):
    """Creates the order (status=pending) and a Telegram Stars invoice link
    for it. The frontend opens that link with Telegram.WebApp.openInvoice()."""
    user = require_user(req.initData)

    coins = vc.reload_json(vc.COINS_FILE, {})
    symbol = req.symbol.upper()
    if symbol not in coins:
        raise HTTPException(status_code=400, detail="That coin isn't available anymore.")
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Enter a positive number, e.g. 0.5")
    if not vc.is_wallet_valid(req.wallet):
        raise HTTPException(status_code=400, detail="That doesn't look like a valid wallet address.")

    try:
        price = await vc.get_coin_price(coins[symbol])
    except Exception:
        logger.exception("Price fetch failed in /api/order")
        raise HTTPException(status_code=502, detail="Price fetch failed. Try again.")

    purchase = vc.compute_purchase(symbol, req.amount, price)

    orders = vc.reload_json(vc.ORDERS_FILE, {})
    order_id = new_order_id(orders)
    orders[order_id] = {
        "user_id": user["id"],
        "username": user.get("username", "Unknown"),
        "coin": purchase["coin"],
        "amount": purchase["amount"],
        "price": purchase["price"],
        "usd": purchase["usd"],
        "commission": purchase["commission"],
        "usd_total": purchase["usd_total"],
        "stars": purchase["stars"],
        "wallet": req.wallet,
        "status": "pending",
        "created": datetime.now().isoformat(),
    }
    await vc.save_json_async(vc.ORDERS_FILE, orders)

    try:
        invoice_url = await bot.create_invoice_link(
            title=f"Buy {purchase['amount']} {purchase['coin']}",
            description=(
                f"{purchase['amount']} {purchase['coin']}\n"
                f"Price: ${purchase['price']:.2f}\n"
                f"Total: ${purchase['usd_total']:,.2f}"
            ),
            payload=order_id,
            provider_token="",  # Telegram Stars use an empty provider token
            currency="XTR",
            prices=[LabeledPrice(f"{purchase['amount']} {purchase['coin']}", purchase["stars"])],
        )
    except Exception:
        logger.exception(f"Failed to create invoice link for {order_id}")
        orders[order_id]["status"] = "cancelled"
        await vc.save_json_async(vc.ORDERS_FILE, orders)
        raise HTTPException(status_code=502, detail="Could not create the payment invoice. Try again.")

    # Admin-facing notification stays in English regardless of buyer language.
    # Fires as soon as the invoice exists — before the user has actually paid.
    try:
        await bot.send_message(
            chat_id=vc.ADMIN_ID,
            text=(
                f"⏳ NEW PENDING ORDER (invoice created, awaiting payment)\n\n"
                f"Order: {order_id}\n"
                f"User: @{user.get('username', 'Unknown')} (id: {user['id']})\n"
                f"Coin: {purchase['amount']} {purchase['coin']}\n"
                f"Wallet: {req.wallet}\n"
                f"Stars due: {purchase['stars']:,}\n\n"
                f"Track with: /orders pending"
            ),
        )
    except Exception:
        logger.exception(f"Could not notify admin about pending order {order_id}")

    return {"order_id": order_id, "invoice_url": invoice_url}

@app.get("/api/orders")
async def api_orders(initData: str):
    user = require_user(initData)
    orders = vc.reload_json(vc.ORDERS_FILE, {})
    mine = [
        {"order_id": oid, **{k: o[k] for k in ("coin", "amount", "status", "created") if k in o}}
        for oid, o in orders.items()
        if o.get("user_id") == user["id"]
    ]
    mine.sort(key=lambda o: o.get("created", ""), reverse=True)
    return {"orders": mine[:20]}

@app.on_event("shutdown")
async def on_shutdown():
    await vc.close_http_session()