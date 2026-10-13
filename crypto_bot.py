import asyncio
import io
import json
import os
import requests
from datetime import datetime
import pytz
from PIL import Image, ImageDraw, ImageFont
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

BOT_TOKEN = "8689018470:AAGNk4JcbxciDqbDEM4Y3bKffQEpzvGzAGI"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
    "NEAR": "NEAR Protocol", "AVAX": "Avalanche", "LINK": "Chainlink"
}

# --- টার্মাক্স ফ্রেন্ডলি ফন্ট লোডার ---
def get_font(size=22):
    paths = [
        os.path.join(BASE_DIR, "Roboto-Bold.ttf"),
        "/system/fonts/Roboto-Bold.ttf",
        "/system/fonts/DroidSans-Bold.ttf",
        "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

def get_regular_font(size=16):
    paths = [
        os.path.join(BASE_DIR, "Roboto-Regular.ttf"),
        "/system/fonts/Roboto-Regular.ttf",
        "/system/fonts/DroidSans.ttf",
        "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf"
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

# --- ডেটা ও স্টেট হ্যান্ডলার ---
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

# --- বাইনান্স লাইভ ডেটা ---
cached_market_data = {}

def update_market_data():
    global cached_market_data
    try:
        res = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=4)
        if res.status_code == 200:
            cached_market_data = {item['symbol']: item for item in res.json()}
    except Exception:
        pass

# --- ক্রিপ্টো লোগো ফেচার ---
def get_coin_icon(symbol, size=64):
    icon_path = os.path.join(ICONS_DIR, f"{symbol.lower()}.png")
    if not os.path.exists(icon_path):
        urls = [
            f"https://assets.coincap.io/assets/icons/{symbol.lower()}@2x.png",
            f"https://cryptoicons.org/api/icon/{symbol.lower()}/200"
        ]
        for u in urls:
            try:
                r = requests.get(u, timeout=3)
                if r.status_code == 200 and len(r.content) > 500:
                    with open(icon_path, "wb") as f:
                        f.write(r.content)
                    break
            except Exception:
                continue

    if os.path.exists(icon_path):
        try:
            img = Image.open(icon_path).convert("RGBA")
            img = img.resize((size, size), Image.Resampling.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, size, size), fill=255)
            circular = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            circular.paste(img, (0, 0), mask=mask)
            return circular
        except Exception:
            return None
    return None

# --- রেফারেন্স ফটো ম্যাচিং ইমেজ তৈরি ---
def generate_dashboard_image(user_id):
    coins = get_user_watchlist(user_id)

    # পারফেক্ট মোবাইল অনুপাত
    width = 650
    row_height = 92
    header_pad = 18
    height = header_pad + len(coins) * row_height + 15

    # আসল স্টিল ডার্ক ব্যাকগ্রাউন্ড (#181F29)
    img = Image.new("RGBA", (width, height), (24, 31, 41, 255))
    draw = ImageDraw.Draw(img)

    f_bold = get_font(25)
    f_reg = get_regular_font(16)
    f_price = get_font(26)
    f_badge = get_font(20)

    color_white = (255, 255, 255, 255)
    color_grey = (132, 142, 156, 255)

    y = header_pad
    for coin in coins:
        # ১. গোল লোগো
        icon = get_coin_icon(coin, size=60)
        if icon:
            img.paste(icon, (24, y + 14), mask=icon)
        else:
            draw.ellipse((24, y + 14, 84, y + 74), fill=(36, 45, 59))
            draw.text((36, y + 32), coin[:3], fill=color_white, font=f_bold)

        # ২. কয়েন সিম্বল ও নাম
        draw.text((100, y + 16), coin, fill=color_white, font=f_bold)
        bbox = draw.textbbox((100, y + 16), coin, font=f_bold)
        cw = bbox[2] - bbox[0]
        draw.text((100 + cw + 6, y + 23), "/USDT", fill=color_grey, font=f_reg)

        full_name = COIN_NAMES.get(coin, coin)
        draw.text((100, y + 52), full_name, fill=color_grey, font=f_reg)

        # ৩. প্রাইস এবং টাকার রেট (চারকোনা বক্স প্রতিরোধে স্পষ্ট Tk প্রিফিক্স)
        pair = f"{coin}USDT"
        if pair in cached_market_data:
            price = float(cached_market_data[pair]["lastPrice"])
            change = float(cached_market_data[pair]["priceChangePercent"])
            bdt_price = price * USD_TO_BDT

            p_str = f"{price:,.4f}" if price < 1 else f"{price:,.2f}"
            bdt_str = f"Tk {bdt_price:,.2f}"

            price_right = 460
            p_bbox = draw.textbbox((0, 0), p_str, font=f_price)
            pw = p_bbox[2] - p_bbox[0]
            draw.text((price_right - pw, y + 16), p_str, fill=color_white, font=f_price)

            b_bbox = draw.textbbox((0, 0), bdt_str, font=f_reg)
            bw = b_bbox[2] - b_bbox[0]
            draw.text((price_right - bw, y + 52), bdt_str, fill=color_grey, font=f_reg)

            # ৪. পার্সেন্টেজ ব্যাজ (#F6465D ও #0ECB81)
            badge_color = (246, 70, 93) if change < 0 else (14, 203, 129)
            sign = "+" if change >= 0 else ""
            c_str = f"{sign}{change:.2f}%"

            bx1, by1, bx2, by2 = 490, y + 20, 625, y + 72
            draw.rounded_rectangle([bx1, by1, bx2, by2], radius=8, fill=badge_color)

            cb = draw.textbbox((0, 0), c_str, font=f_badge)
            cw_b = cb[2] - cb[0]
            ch_b = cb[3] - cb[1]
            draw.text((bx1 + (135 - cw_b) // 2, by1 + (52 - ch_b) // 2 - 2), c_str, fill=color_white, font=f_badge)
        else:
            draw.text((380, y + 32), "N/A", fill=color_grey, font=f_reg)

        y += row_height

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf

# টেক্সট হিসেবে নিচের ক্যাপশন তৈরি
def get_caption_text():
    try:
        tz = pytz.timezone("Asia/Dhaka")
        current_time = datetime.now(tz).strftime("%I:%M:%S %p")
    except Exception:
        current_time = datetime.now().strftime("%I:%M:%S")
    return f"⏱ <b>অটো রিফ্রেশ:</b> ১০ সেকেন্ড\n🕒 <b>সর্বশেষ আপডেট:</b> {current_time} (ঢাকা সময়)"

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

# --- ১০ সেকেন্ড পর পর লাইভ আপডেট লুপ ---
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
        update_market_data()

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
        coin = update.message.text.strip().upper()
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
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.ALL, message_handler))

    print("বট সক্রিয়ভাবে চলছে...")
    app.run_polling()

if __name__ == "__main__":
    main()
    
