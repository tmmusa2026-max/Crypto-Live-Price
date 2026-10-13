import asyncio
import io
import json
import os
import requests
from datetime import datetime
import pytz
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, InputMediaPhoto
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from renderer import render_dashboard

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# --- টোকেন .env ফাইল বা এনভায়রনমেন্ট থেকে পড়া (কোডে সরাসরি লেখা নেই) ---
def load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

DATA_FILE = os.path.join(BASE_DIR, "user_watchlists.json")
STATE_FILE = os.path.join(BASE_DIR, "active_dashboards.json")
ICONS_DIR = os.path.join(BASE_DIR, "coin_icons")
os.makedirs(ICONS_DIR, exist_ok=True)

DEFAULT_COINS = ["AERO", "BTC", "SEI", "SUI", "XRP"]
MAX_COINS_LIMIT = 10
USD_TO_BDT = 121.80

COIN_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana",
    "DOGE": "Dogecoin", "SUI": "Sui", "SEI": "Sei",
    "AERO": "Aerodrome", "XRP": "XRP", "BNB": "BNB",
    "PEPE": "Pepe", "SHIB": "Shiba Inu", "ADA": "Cardano",
    "NEAR": "NEAR Protocol", "AVAX": "Avalanche", "LINK": "Chainlink",
    "OSMO": "Osmosis",
}

# --- স্টেট হ্যান্ডলার ---
def load_active_dashboards():
    if not os.path.exists(STATE_FILE): return {}
    try:
        with open(STATE_FILE, "r") as f: return json.load(f)
    except Exception: return {}

def save_active_dashboard(chat_id, message_id):
    data = load_active_dashboards()
    data[str(chat_id)] = message_id
    with open(STATE_FILE, "w") as f: json.dump(data, f)

def load_all_data():
    if not os.path.exists(DATA_FILE): return {}
    try:
        with open(DATA_FILE, "r") as f: return json.load(f)
    except Exception: return {}

def save_all_data(data):
    with open(DATA_FILE, "w") as f: json.dump(data, f)

def get_user_watchlist(user_id):
    data = load_all_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = DEFAULT_COINS.copy()
        save_all_data(data)
    return data[uid]

def add_coin_for_user(user_id, symbol):
    data = load_all_data()
    uid = str(user_id)
    coins = data.get(uid, DEFAULT_COINS.copy())
    symbol = symbol.upper()
    if len(coins) >= MAX_COINS_LIMIT: return False, "LIMIT_REACHED"
    if symbol not in coins:
        coins.append(symbol)
        data[uid] = coins
        save_all_data(data)
        return True, "ADDED"
    return False, "ALREADY_EXISTS"

def remove_coin_for_user(user_id, symbol):
    data = load_all_data()
    uid = str(user_id)
    coins = data.get(uid, DEFAULT_COINS.copy())
    symbol = symbol.upper()
    if symbol in coins:
        coins.remove(symbol)
        data[uid] = coins
        save_all_data(data)

# --- সেন্ট্রাল মার্কেট ফেচ ---
cached_market_data = {}

def update_market_data():
    global cached_market_data
    try:
        res = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=4)
        if res.status_code == 200:
            cached_market_data = {item['symbol']: item for item in res.json()}
    except Exception:
        pass

# --- Binance-এর মতো দশমিক ঘর (tickSize থেকে) ---
tick_cache = {}

def get_tick_decimals(symbol):
    """কয়েনের tickSize থেকে দশমিক ঘর। না পেলে None (রেন্ডারার নিজে আন্দাজ করবে)।"""
    if symbol in tick_cache:
        return tick_cache[symbol]
    try:
        r = requests.get("https://api.binance.com/api/v3/exchangeInfo",
                         params={"symbol": f"{symbol}USDT"}, timeout=4)
        if r.status_code == 200:
            for flt in r.json()["symbols"][0]["filters"]:
                if flt["filterType"] == "PRICE_FILTER":
                    tick = flt["tickSize"].rstrip("0")
                    dec = len(tick.split(".")[1]) if "." in tick else 0
                    tick_cache[symbol] = dec
                    return dec
    except Exception:
        pass
    return None

# --- লোগো: লোকাল ক্যাশ -> Binance -> CoinGecko -> বিকল্প উৎস ---
_binance_logo_map = {}

def _load_binance_logo_map():
    """Binance-এর নিজস্ব অ্যাসেট লিস্ট থেকে লোগো URL (রেফারেন্সের লোগোর মতো)।"""
    global _binance_logo_map
    if _binance_logo_map:
        return
    try:
        r = requests.get(
            "https://www.binance.com/bapi/asset/v2/public/asset/asset/get-all-asset",
            timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            for a in r.json().get("data", []):
                if a.get("assetCode") and a.get("logoUrl"):
                    url = a["logoUrl"]
                    if url.startswith("/"):
                        url = "https://bin.bnbstatic.com" + url
                    _binance_logo_map[a["assetCode"].upper()] = url
    except Exception:
        pass

def _try_download(url, path):
    try:
        r = requests.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and len(r.content) > 300:
            img = Image.open(io.BytesIO(r.content))
            img.load()
            img.convert("RGBA").save(path, "PNG")
            return True
    except Exception:
        pass
    return False

def get_coin_icon(symbol):
    """PIL ছবি বা None (None হলে রেন্ডারার সুন্দর বিকল্প গোল লোগো আঁকে)।"""
    icon_path = os.path.join(ICONS_DIR, f"{symbol.lower()}.png")
    if not os.path.exists(icon_path):
        _load_binance_logo_map()
        urls = []
        if symbol.upper() in _binance_logo_map:
            urls.append(_binance_logo_map[symbol.upper()])
        urls.append(f"https://bin.bnbstatic.com/static/assets/logos/{symbol.upper()}.png")
        ok = any(_try_download(u, icon_path) for u in urls)
        if not ok:
            try:
                s = requests.get("https://api.coingecko.com/api/v3/search",
                                 params={"query": symbol}, timeout=6).json()
                for c in s.get("coins", []):
                    if c.get("symbol", "").upper() == symbol.upper():
                        ok = _try_download(c.get("large") or c.get("thumb"), icon_path)
                        break
            except Exception:
                pass
        if not ok:
            _try_download(
                f"https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/128/color/{symbol.lower()}.png",
                icon_path)
    if os.path.exists(icon_path):
        try:
            return Image.open(icon_path).convert("RGBA")
        except Exception:
            return None
    return None

# --- ইমেজ জেনারেটর ---
def generate_dashboard_image(user_id):
    coins = get_user_watchlist(user_id)
    rows = []
    for coin in coins:
        item = cached_market_data.get(f"{coin}USDT")
        row = {"symbol": coin, "name": COIN_NAMES.get(coin, coin), "price": None}
        if item:
            price = float(item["lastPrice"])
            row.update(
                price=price,
                change=float(item["priceChangePercent"]),
                bdt=price * USD_TO_BDT,
                tick_decimals=get_tick_decimals(coin),
            )
        rows.append(row)
    return render_dashboard(rows, get_coin_icon)

# নির্দেশ মতো আপডেট করা ক্যাপশন
def get_caption_text():
    try:
        tz = pytz.timezone("Asia/Dhaka")
        current_time = datetime.now(tz).strftime("%I:%M:%S %p")
    except Exception:
        current_time = datetime.now().strftime("%I:%M:%S %p")
    return f"⏱ Refresh: 10s | 🕒 <code>{current_time}</code>"

def get_control_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ কয়েন যোগ", callback_data="add_coin"),
            InlineKeyboardButton("➖ কয়েন মুছুন", callback_data="remove_coin")
        ]
    ])

waiting_for_input = {}

async def delete_after_30s(bot, chat_id, message_id):
    await asyncio.sleep(30)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

# --- ব্যাকগ্রাউন্ড রিফ্রেশার লুপ ---
async def global_updater(app):
    while True:
        await asyncio.sleep(10)
        await asyncio.to_thread(update_market_data)

        dashboards = load_active_dashboards()
        for chat_id_str, msg_id in list(dashboards.items()):
            try:
                chat_id = int(chat_id_str)
                img_buf = await asyncio.to_thread(generate_dashboard_image, chat_id)
                media = InputMediaPhoto(
                    media=img_buf,
                    caption=get_caption_text(),
                    parse_mode="HTML"
                )
                await app.bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=msg_id,
                    media=media,
                    reply_markup=get_control_keyboard()
                )
                await asyncio.sleep(0.1)
            except BadRequest as e:
                if "Message to edit not found" in str(e):
                    dashboards.pop(chat_id_str, None)
                    with open(STATE_FILE, "w") as f:
                        json.dump(dashboards, f)
            except Exception:
                pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current_msg_id = update.message.message_id if update.message else None

    if current_msg_id:
        asyncio.create_task(delete_after_30s(context.bot, chat_id, current_msg_id))

    dashboards = load_active_dashboards()
    old_msg_id = dashboards.get(str(chat_id))
    if old_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception:
            pass

    if not cached_market_data:
        await asyncio.to_thread(update_market_data)

    img_buf = await asyncio.to_thread(generate_dashboard_image, chat_id)
    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=img_buf,
        caption=get_caption_text(),
        parse_mode="HTML",
        reply_markup=get_control_keyboard()
    )
    save_active_dashboard(chat_id, msg.message_id)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "add_coin":
        current_coins = get_user_watchlist(user_id)
        if len(current_coins) >= MAX_COINS_LIMIT:
            reply_msg = await query.message.reply_text(f"⚠️ সর্বোচ্চ {MAX_COINS_LIMIT}টি কয়েন পূর্ণ হয়েছে!")
            asyncio.create_task(delete_after_30s(context.bot, query.message.chat_id, reply_msg.message_id))
            return

        waiting_for_input[user_id] = True
        reply_msg = await query.message.reply_text("নতুন কয়েনের নাম লিখে মেসেজ দিন (যেমন: PEPE, DOGE):")
        asyncio.create_task(delete_after_30s(context.bot, query.message.chat_id, reply_msg.message_id))

    elif data == "remove_coin":
        coins = get_user_watchlist(user_id)
        buttons = [[InlineKeyboardButton(f"❌ {c}", callback_data=f"del_{c}")] for c in coins]
        reply_msg = await query.message.reply_text("যে কয়েনটি বাদ দিতে চান ট্যাপ করুন:", reply_markup=InlineKeyboardMarkup(buttons))
        asyncio.create_task(delete_after_30s(context.bot, query.message.chat_id, reply_msg.message_id))

    elif data.startswith("del_"):
        coin = data.replace("del_", "")
        remove_coin_for_user(user_id, coin)
        try:
            await query.message.delete()
        except Exception:
            pass
        dashboards = load_active_dashboards()
        active_msg_id = dashboards.get(str(user_id))
        if active_msg_id:
            img_buf = await asyncio.to_thread(generate_dashboard_image, user_id)
            await context.bot.edit_message_media(
                chat_id=user_id,
                message_id=active_msg_id,
                media=InputMediaPhoto(
                    media=img_buf,
                    caption=get_caption_text(),
                    parse_mode="HTML"
                ),
                reply_markup=get_control_keyboard()
            )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id

    asyncio.create_task(delete_after_30s(context.bot, chat_id, user_msg_id))

    if waiting_for_input.get(user_id):
        coin = (update.message.text or "").strip().upper()
        waiting_for_input.pop(user_id, None)

        success, status = add_coin_for_user(user_id, coin)
        if success:
            reply_text = f"✅ {coin} যোগ হয়েছে!"
        elif status == "LIMIT_REACHED":
            reply_text = f"❌ সর্বোচ্চ {MAX_COINS_LIMIT}টি কয়েন যোগ করতে পারবেন।"
        else:
            reply_text = f"ℹ️ {coin} ইতিমধ্যে যুক্ত আছে।"

        reply_msg = await update.message.reply_text(reply_text)
        asyncio.create_task(delete_after_30s(context.bot, chat_id, reply_msg.message_id))
    else:
        reply_msg = await update.message.reply_text("ℹ️ কয়েন পরিবর্তন করতে ছবির নিচের বাটন ব্যবহার করুন।")
        asyncio.create_task(delete_after_30s(context.bot, chat_id, reply_msg.message_id))

async def post_init(application):
    await application.bot.set_my_commands([
        BotCommand("start", "📊 লাইভ ড্যাশবোর্ড চালু করুন")
    ])
    asyncio.create_task(global_updater(application))

def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN পাওয়া যায়নি। .env ফাইলে BOT_TOKEN=... লিখুন (.env.example দেখুন)।")
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.ALL, message_handler))

    print("বট সক্রিয়ভাবে চলছে...")
    app.run_polling()

if __name__ == "__main__":
    main()
