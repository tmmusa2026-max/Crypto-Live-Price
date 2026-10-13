"""Binance-স্টাইল ড্যাশবোর্ড ইমেজ রেন্ডারার (শুধু Pillow দরকার)।

মাপজোক Binance অ্যাপের রেফারেন্স স্ক্রিনশট থেকে নেওয়া (১x = ১১০৪px চওড়া)।
ভেতরে ২x-এ এঁকে ১x-এ ছোট করা হয়, তাই লেখা/গোল/কোণ সব মসৃণ থাকে।
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "fonts")

# ---- রং (রেফারেন্স থেকে স্যাম্পল করা) ----
BG = (39, 47, 58)
WHITE = (234, 236, 239)
GREY = (132, 142, 156)
RED = (246, 70, 93)
GREEN = (14, 203, 129)

# ---- জ্যামিতি (১x পিক্সেল) ----
SS = 2            # সুপারস্যাম্পলিং
W = 1104
PAD_X = 56
ROW_PITCH = 196
HEADER_TOP = 40           # হেডার টেক্সটের ক্যাপ-টপ
FIRST_BADGE_TOP = 149
BADGE_W, BADGE_H, BADGE_R = 272, 96, 10
LOGO_D = 91
TEXT_X = 176
PRICE_RIGHT = 717         # দাম/টাকার ডান প্রান্ত
BADGE_X = 775

_font_cache = {}


def _font(name, size):
    key = (name, size)
    if key not in _font_cache:
        path = os.path.join(FONT_DIR, name)
        try:
            _font_cache[key] = ImageFont.truetype(path, int(round(size * SS)))
        except Exception:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def f_regular(size):
    return _font("Inter-Regular.otf", size)


def f_medium(size):
    return _font("Inter-Medium.otf", size)


def f_semibold(size):
    return _font("Inter-SemiBold.otf", size)


def f_taka(size):
    return _font("Taka.ttf", size)


# ---- হেল্পার ----
def _text_w(draw, text, font):
    return draw.textlength(text, font=font) / SS


def _draw_text(draw, x, baseline, text, font, fill, anchor="ls"):
    """x/baseline ১x ইউনিটে। anchor: ls = বাম+বেসলাইন, rs = ডান+বেসলাইন, ms = মাঝ+বেসলাইন"""
    draw.text((x * SS, baseline * SS), text, font=font, fill=fill, anchor=anchor)


def _draw_taka_price(draw, right, baseline, number_text, size, fill):
    """'৳' + সংখ্যা, ডান প্রান্ত বরাবর সাজানো।"""
    num_font = f_regular(size)
    taka_font = f_taka(size * 1.42)
    gap = size * 0.03
    nw = _text_w(draw, number_text, num_font)
    tw = _text_w(draw, "৳", taka_font)
    _draw_text(draw, right, baseline, number_text, num_font, fill, "rs")
    draw.text(((right - nw - gap - tw) * SS, baseline * SS), "৳", font=taka_font, fill=fill, anchor="ls", stroke_width=1, stroke_fill=fill)


def _fallback_logo(symbol, d):
    """আইকন না পাওয়া গেলে নামের আদ্যক্ষর দিয়ে গোল লোগো।"""
    big = d * SS * 2
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    palette = [(240, 185, 11), (98, 126, 234), (0, 168, 150), (214, 76, 119),
               (74, 144, 226), (140, 90, 220), (240, 120, 50)]
    col = palette[sum(ord(c) for c in symbol) % len(palette)]
    dr.ellipse((0, 0, big - 1, big - 1), fill=col)
    font = ImageFont.truetype(os.path.join(FONT_DIR, "Inter-Bold.otf"), int(big * 0.38)) \
        if os.path.exists(os.path.join(FONT_DIR, "Inter-Bold.otf")) else ImageFont.load_default()
    dr.text((big / 2, big / 2), symbol[:3], font=font, fill=(255, 255, 255), anchor="mm")
    return img.resize((d * SS, d * SS), Image.Resampling.LANCZOS)


def _circular(icon, d):
    """যেকোনো আইকনকে মসৃণ কিনারার গোল ছবিতে রূপ দেয়।"""
    big = d * SS * 4
    icon = icon.convert("RGBA").resize((big, big), Image.Resampling.LANCZOS)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    out.paste(icon, (0, 0), mask)
    return out.resize((d * SS, d * SS), Image.Resampling.LANCZOS)


def format_price(price, tick_decimals=None):
    """Binance যেভাবে দেখায়: কয়েনের tickSize অনুযায়ী দশমিক ঘর।"""
    if tick_decimals is None:
        if price >= 1000:
            tick_decimals = 2
        elif price >= 100:
            tick_decimals = 2
        elif price >= 1:
            tick_decimals = 4
        elif price >= 0.1:
            tick_decimals = 5
        elif price >= 0.01:
            tick_decimals = 5
        elif price >= 0.0001:
            tick_decimals = 6
        else:
            tick_decimals = 8
    return f"{price:,.{tick_decimals}f}"


def render_dashboard(rows, get_icon):
    """
    rows: [{"symbol","name","price","change","bdt","tick_decimals"(ঐচ্ছিক)} ...]
          দাম না থাকলে price=None।
    get_icon(symbol) -> PIL.Image বা None
    রিটার্ন: PNG BytesIO
    """
    n = max(len(rows), 1)
    height = FIRST_BADGE_TOP + ROW_PITCH * (n - 1) + BADGE_H + 52
    img = Image.new("RGBA", (W * SS, height * SS), BG + (255,))
    draw = ImageDraw.Draw(img)

    # --- হেডার: Name / Last Price / 24h chg% ---
    hf = f_regular(40)
    hb = HEADER_TOP + 28
    _draw_text(draw, 57, hb, "Name", hf, GREY, "ls")
    _draw_text(draw, PRICE_RIGHT, hb, "Last Price", hf, GREY, "rs")
    _draw_text(draw, W - PAD_X - 1, hb, "24h chg%", hf, GREY, "rs")

    for i, r in enumerate(rows):
        B = FIRST_BADGE_TOP + ROW_PITCH * i      # ব্যাজের উপরের y
        cy = B + BADGE_H / 2

        # লোগো
        icon = get_icon(r["symbol"])
        logo = _circular(icon, LOGO_D) if icon is not None else _fallback_logo(r["symbol"], LOGO_D)
        img.paste(logo, (int(56 * SS), int((cy - LOGO_D / 2) * SS)), logo)

        # সিম্বল + /USDT
        sym_font = f_semibold(50)
        sym_base = B + 34
        _draw_text(draw, TEXT_X, sym_base, r["symbol"], sym_font, WHITE, "ls")
        sw = _text_w(draw, r["symbol"], sym_font)
        _draw_text(draw, TEXT_X + sw + 14, sym_base, "/USDT", f_regular(36), GREY, "ls")

        # পুরো নাম
        _draw_text(draw, TEXT_X, B + 100, r["name"], f_regular(40), GREY, "ls")

        if r.get("price") is not None:
            ptxt = format_price(r["price"], r.get("tick_decimals"))
            _draw_text(draw, PRICE_RIGHT, B + 33, ptxt, f_semibold(51), WHITE, "rs")
            _draw_taka_price(draw, PRICE_RIGHT, B + 98, f"{r['bdt']:,.2f}", 44, GREY)

            ch = r["change"]
            col = RED if ch < 0 else GREEN
            ctxt = f"{'+' if ch >= 0 else ''}{ch:.2f}%"
            draw.rounded_rectangle(
                [BADGE_X * SS, B * SS, (BADGE_X + BADGE_W) * SS, (B + BADGE_H) * SS],
                radius=BADGE_R * SS, fill=col)
            _draw_text(draw, BADGE_X + BADGE_W / 2, B + 63, ctxt, f_semibold(44), (255, 255, 255), "ms")
        else:
            _draw_text(draw, PRICE_RIGHT, B + 60, "N/A", f_regular(40), GREY, "rs")

    out = img.resize((W, height), Image.Resampling.LANCZOS).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
