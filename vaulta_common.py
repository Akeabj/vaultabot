# vaulta_common.py — shared config, storage, pricing, and helpers used by
# both the admin bot (bot.py) and the Mini App API server (miniapp_server.py).
#
# Both processes read/write the same JSON files (coins.json / orders.json /
# langs.json). asyncio.Lock alone only protects against races *within* one
# process, so writes also go through a cross-process file lock (via the
# `filelock` package) so the bot and the web server can't clobber each
# other's writes if they happen to save at the same instant.

import os
import json
import time
import hmac
import hashlib
import logging
import asyncio
from urllib.parse import parse_qsl
from typing import Optional, Dict, Tuple, List

import aiohttp
from filelock import FileLock
from dotenv import load_dotenv

logger = logging.getLogger("vaulta_common")

# Load variables from a .env file in the current working directory (if one
# exists) into the process environment, BEFORE reading any of them below.
# Both bot.py and miniapp_server.py import this module first, so this runs
# once for whichever process starts.
load_dotenv()

# ==============================
# CONFIG
# ==============================

BOT_TOKEN = os.environ.get("VAULTA_BOT_TOKEN")
ADMIN_ID_RAW = os.environ.get("VAULTA_ADMIN_ID")

if not BOT_TOKEN:
    raise RuntimeError(
        "VAULTA_BOT_TOKEN env var is not set. "
        "Revoke your old token via @BotFather (/revoke) and set the new one as an env var."
    )
if not ADMIN_ID_RAW:
    raise RuntimeError("VAULTA_ADMIN_ID env var is not set.")

ADMIN_ID = int(ADMIN_ID_RAW)

STAR_WITHDRAWAL_VALUE = 0.013
COMMISSION = 0.03
PRICE_BUFFER = 1.01

MIN_WALLET_LEN = 20  # rough sanity check; real validation should be per-chain
DEFAULT_LANG = "en"

# Where coins.json/orders.json/langs.json live. Defaults to the current
# directory (unchanged local-dev behavior). On Railway, set this to the
# mounted volume's path (e.g. /data) so order history survives redeploys —
# without it, files would live in the throwaway build directory and reset
# every time you deploy.
DATA_DIR = os.environ.get("VAULTA_DATA_DIR", ".")

COINS_FILE = os.path.join(DATA_DIR, "coins.json")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
LANGS_FILE = os.path.join(DATA_DIR, "langs.json")

# ==============================
# STORAGE (cross-process safe)
# ==============================

_save_lock = asyncio.Lock()  # fast in-process guard

def load_json(file, default):
    if not os.path.exists(file):
        return default
    try:
        with open(file, "r") as f:
            return json.load(f)
    except Exception:
        logger.exception(f"Failed to load {file}, using default")
        return default

def reload_json(file, default):
    """Explicit re-read from disk. Call this before reading shared state
    (coins/orders/langs) from request handlers, since the *other* process
    (bot.py or miniapp_server.py) may have written since this process last
    loaded its in-memory copy."""
    return load_json(file, default)

async def save_json_async(file, data):
    """Serialize writes so concurrent saves can't clobber each other, both
    within this process (asyncio.Lock) and across the bot.py / miniapp
    processes (a real file lock, since they're separate OS processes)."""
    async with _save_lock:
        lock_path = f"{file}.lock"
        loop = asyncio.get_event_loop()

        def _write():
            with FileLock(lock_path, timeout=10):
                tmp_file = f"{file}.tmp"
                with open(tmp_file, "w") as f:
                    json.dump(data, f, indent=4)
                os.replace(tmp_file, file)  # atomic on POSIX and Windows

        await loop.run_in_executor(None, _write)

# ==============================
# HTTP SESSION (per-process)
# ==============================

_http_session: Optional[aiohttp.ClientSession] = None

def get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session

async def close_http_session():
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()

# ==============================
# PRICING
# ==============================

PRICE_CACHE_TTL = 30  # seconds
# coin_id -> (price, 24h_change_pct_or_None, fetched_at). The change field is
# additive — get_coin_prices()/get_coin_price() below still only read/return
# the price, so every existing caller (compute_purchase, /api/quote,
# /api/order) is unaffected.
_price_cache: Dict[str, Tuple[float, Optional[float], float]] = {}

async def _fetch_prices(coin_ids: List[str]) -> dict:
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": ",".join(coin_ids), "vs_currencies": "usd", "include_24hr_change": "true"}
    session = get_http_session()

    for attempt in range(2):  # one retry on 429
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 429:
                retry_after = float(response.headers.get("Retry-After", 5))
                logger.warning(f"CoinGecko rate-limited us (429). Retry-After={retry_after}s")
                if attempt == 0:
                    await asyncio.sleep(min(retry_after, 5))
                    continue
                response.raise_for_status()
            response.raise_for_status()
            return await response.json()
    return {}

async def get_coin_prices(coin_ids: List[str]) -> dict:
    now = asyncio.get_event_loop().time()
    result = {}
    to_fetch = []

    for cid in coin_ids:
        cached = _price_cache.get(cid)
        if cached and (now - cached[2]) < PRICE_CACHE_TTL:
            result[cid] = cached[0]
        else:
            to_fetch.append(cid)

    if to_fetch:
        try:
            data = await _fetch_prices(to_fetch)
            for cid in to_fetch:
                entry = data.get(cid, {})
                price = entry.get("usd")
                change = entry.get("usd_24h_change")
                if price is not None:
                    _price_cache[cid] = (price, change, now)
                    result[cid] = price
                else:
                    logger.warning(f"CoinGecko returned no USD price for '{cid}'")
                    result[cid] = None
        except Exception:
            logger.exception(f"Batch price fetch failed for {to_fetch}")
            for cid in to_fetch:
                result[cid] = None

    return result

async def get_coin_price(coin_id):
    prices = await get_coin_prices([coin_id])
    price = prices.get(coin_id)
    if price is None:
        raise RuntimeError(f"No price available for {coin_id}")
    return price

# ---- Market data (price + 24h change + logo image) for the home screen's
# Popular Coins cards. Separate cache from _price_cache above — this is a
# different CoinGecko endpoint and is only used by /api/popular, never by
# the quote/order flow, so it can't affect what a buyer is charged. ----

_market_cache: Dict[str, Tuple[dict, float]] = {}  # coin_id -> (market_data, fetched_at)

async def _fetch_market_data(coin_ids: List[str]) -> list:
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "ids": ",".join(coin_ids), "price_change_percentage": "24h"}
    session = get_http_session()

    for attempt in range(2):  # one retry on 429
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 429:
                retry_after = float(response.headers.get("Retry-After", 5))
                logger.warning(f"CoinGecko rate-limited us (429) on /coins/markets. Retry-After={retry_after}s")
                if attempt == 0:
                    await asyncio.sleep(min(retry_after, 5))
                    continue
                response.raise_for_status()
            response.raise_for_status()
            return await response.json()
    return []

async def get_coin_market_data(coin_ids: List[str]) -> dict:
    """Returns {coin_id: {"price", "change_24h", "image"}} — the image is
    CoinGecko's own hosted logo for that coin (their API is meant to be used
    this way), so nothing here is reproducing anyone's logo artwork."""
    now = asyncio.get_event_loop().time()
    result = {}
    to_fetch = []

    for cid in coin_ids:
        cached = _market_cache.get(cid)
        if cached and (now - cached[1]) < PRICE_CACHE_TTL:
            result[cid] = cached[0]
        else:
            to_fetch.append(cid)

    if to_fetch:
        try:
            data = await _fetch_market_data(to_fetch)
            by_id = {item.get("id"): item for item in data}
            for cid in to_fetch:
                item = by_id.get(cid)
                if item:
                    entry = {
                        "price": item.get("current_price"),
                        "change_24h": item.get("price_change_percentage_24h"),
                        "image": item.get("image"),
                    }
                else:
                    logger.warning(f"CoinGecko returned no market data for '{cid}'")
                    entry = {"price": None, "change_24h": None, "image": None}
                _market_cache[cid] = (entry, now)
                result[cid] = entry
        except Exception:
            logger.exception(f"Market data fetch failed for {to_fetch}")
            for cid in to_fetch:
                result[cid] = {"price": None, "change_24h": None, "image": None}

    return result

def compute_purchase(symbol: str, amount: float, price: float) -> dict:
    """Same math as v1: commission + volatility buffer, converted to Stars."""
    usd_value = amount * price
    commission_amount = usd_value * COMMISSION
    buffer_amount = (usd_value + commission_amount) * (PRICE_BUFFER - 1)
    usd_total = usd_value + commission_amount + buffer_amount
    stars = usd_to_stars(usd_total)
    return {
        "coin": symbol,
        "amount": amount,
        "price": price,
        "usd": usd_value,
        "commission": commission_amount,
        "buffer": buffer_amount,
        "usd_total": usd_total,
        "stars": stars,
    }

def usd_to_stars(usd_total_with_buffer):
    stars = round(usd_total_with_buffer / STAR_WITHDRAWAL_VALUE)
    return max(1, int(stars))

def get_wallet_label(symbol):
    names = {"BTC": "Bitcoin", "SOL": "Solana", "XRP": "XRP", "ETH": "Ethereum", "DOGE": "Dogecoin"}
    return names.get(symbol, symbol)

def is_wallet_valid(wallet: str) -> bool:
    return bool(wallet) and len(wallet) >= MIN_WALLET_LEN and " " not in wallet

# ==============================
# TELEGRAM MINI APP AUTH
# ==============================

def validate_init_data(init_data: str, max_age_seconds: int = 86400) -> Optional[dict]:
    """Validates the `initData` string a Telegram Mini App sends on launch,
    per Telegram's documented HMAC scheme:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Returns the parsed `user` dict on success, or None if the signature is
    missing, invalid, or too old. NEVER trust a user id coming straight from
    the frontend without this check — it's what stops someone from editing
    the page's JS to claim to be a different Telegram user.
    """
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None

        auth_date = int(pairs.get("auth_date", "0"))
        if max_age_seconds and (time.time() - auth_date) > max_age_seconds:
            return None

        user_raw = pairs.get("user")
        user = json.loads(user_raw) if user_raw else {}
        return user
    except Exception:
        logger.exception("initData validation failed")
        return None