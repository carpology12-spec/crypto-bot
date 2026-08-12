from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, WebAppInfo
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import asyncio
import os
import time
import html
import json
import re
import io
import aiohttp
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont

# این سه مقدار از Environment Variables خوانده می‌شوند
# (همان‌هایی که در Railway -> Variables تنظیم کرده‌اید: BOT_TOKEN, CHANNEL_ID, ADMIN_ID)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))
CALCULATOR_URL = "https://carpology12-spec.github.io/crypto-calculator/"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me")
DEFAULT_RISK_BALANCE = os.environ.get("DEFAULT_RISK_BALANCE", "2%")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- تنظیمات پایش قیمت (برای اعلام خودکار تاچ‌شدن تارگت/استاپ/Entry دوم) ---
PRICE_CHECK_INTERVAL = 20  # هر ۲۰ ثانیه یک‌بار قیمت‌ها چک می‌شود (کاهش تاخیر نسبت به قبل)
ACTIVE_SIGNALS_FILE = "active_signals.json"
HISTORY_FILE = "signal_history.json"
SUMMARY_BATCH_SIZE = 5  # بعد از هر ۵ سیگنال کامل‌شده، خلاصه ارسال می‌شود

# --- تنظیمات ساخت تصویر گزارش عملکرد ---
ASSETS_DIR = "assets"
FONTS_DIR = "fonts"
BACKGROUND_IMAGE_PATH = os.path.join(ASSETS_DIR, "background.png")
FONT_TITLE = os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf")
FONT_MONO_BOLD = os.path.join(FONTS_DIR, "DejaVuSansMono-Bold.ttf")
FONT_MONO_REG = os.path.join(FONTS_DIR, "DejaVuSansMono-Bold.ttf")  # فقط از همین دو فونت استفاده می‌شود

OUTCOME_COLORS = {
    "TP": (74, 222, 128),   # سبز
    "TS": (74, 222, 128),   # سبز (سود قفل‌شده با تریلینگ استاپ)
    "SL": (248, 113, 113),  # قرمز
    "BE": (250, 204, 21),   # زرد (ریسک‌فری)
}


def load_history() -> list:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_history(history: list) -> None:
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def get_target_weights(target_count: int) -> list:
    """نسبت بسته‌شدن پوزیشن به‌ازای هر تارگت."""
    if target_count == 1:
        return [1.0]
    if target_count == 2:
        return [0.5, 0.5]
    if target_count == 3:
        return [0.5, 0.25, 0.25]
    # برای موارد نادر با بیش از ۳ تارگت، تقسیم مساوی
    return [1.0 / target_count] * target_count

# قیمت از بازار Futures/Perpetual بایننس گرفته می‌شود (نه Spot)
# چون سیگنال‌های شما با لوریج (LEV) هستند، یعنی معامله فیوچرزی‌اند، نه اسپات.
BINGX_SWAP_TICKER_URL = "https://open-api.bingx.com/openApi/swap/v2/quote/ticker"


def load_active_signals() -> list:
    if not os.path.exists(ACTIVE_SIGNALS_FILE):
        return []
    try:
        with open(ACTIVE_SIGNALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_active_signals(signals: list) -> None:
    with open(ACTIVE_SIGNALS_FILE, "w", encoding="utf-8") as f:
        json.dump(signals, f, ensure_ascii=False, indent=2)


# ── صف سیگنال‌های در انتظار (وقتی تعداد سیگنال‌های فعال به حد مجاز رسیده باشد) ──
PENDING_SIGNALS_FILE = "pending_signals.json"
MAX_ACTIVE_SIGNALS = int(os.environ.get("MAX_ACTIVE_SIGNALS", "2"))


def load_pending_signals() -> list:
    if not os.path.exists(PENDING_SIGNALS_FILE):
        return []
    try:
        with open(PENDING_SIGNALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_pending_signals(pending: list) -> None:
    with open(PENDING_SIGNALS_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)


MAX_QUEUE_AGE_SECONDS = int(os.environ.get("MAX_QUEUE_AGE_SECONDS", "180"))  # ۳ دقیقه
PRICE_DRIFT_THRESHOLD_PCT = float(os.environ.get("PRICE_DRIFT_THRESHOLD_PCT", "0.4"))  # ۰.۴٪


async def process_pending_queue():
    """اگر ظرفیت خالی شده باشد، سیگنال بعدی صف را (در صورت هنوز معتبر بودن) ارسال می‌کند."""
    active = [s for s in load_active_signals() if not s["completed"]]
    if len(active) >= MAX_ACTIVE_SIGNALS:
        return

    pending = load_pending_signals()
    if not pending:
        return

    next_signal = pending.pop(0)
    save_pending_signals(pending)

    queued_at = next_signal.pop("queued_at", None)
    age = (time.time() - queued_at) if queued_at else 0

    # ── محافظ ۱: اگر خیلی وقته تو صف مونده، دیگه سیگنال بیاتیه ──────────────
    if age > MAX_QUEUE_AGE_SECONDS:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🗑 سیگنال {next_signal['currency']} به‌خاطر ماندن بیش از "
                f"{MAX_QUEUE_AGE_SECONDS // 60} دقیقه در صف (بیات شدن) حذف شد."
            )
        )
        await process_pending_queue()  # برو سراغ سیگنال بعدی صف (اگر بود)
        return

    # ── محافظ ۲: اگر قیمت فعلی خیلی از نقطه ورود ثبت‌شده دور شده، حذفش کن ────
    try:
        bingx_symbol = currency_to_bingx_symbol(next_signal["currency"])
        current_price = await fetch_bingx_price(bingx_symbol)
        original_entry = float(str(next_signal["entries"][0]).replace(",", ""))
        drift_pct = abs(current_price - original_entry) / original_entry * 100

        if drift_pct > PRICE_DRIFT_THRESHOLD_PCT:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"🗑 سیگنال {next_signal['currency']} به‌خاطر تغییر قیمت "
                    f"({drift_pct:.2f}٪ نسبت به نقطه ورود اصلی) در صف حذف شد."
                )
            )
            await process_pending_queue()
            return
    except Exception as e:
        print(f"⚠️ خطا در بررسی انحراف قیمت صف برای {next_signal.get('currency')}: {e}")

    await build_and_send_signal(**next_signal)
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📤 سیگنال {next_signal['currency']} از صف خارج و ارسال شد (ظرفیت خالی شد)."
    )


def currency_to_bingx_symbol(currency_display: str) -> str:
    """تبدیل 'BTC/USDT' به فرمت بایننس‌ایکس: 'BTC-USDT'"""
    return currency_display.replace(" ", "").replace("/", "-").upper()


# آیکون هر تارگت به ترتیب (دقیقاً مطابق نمونه)
# اگر تعداد تارگت‌ها از ۵ بیشتر شود، آیکون آخر (🌕) برای بقیه تکرار می‌شود
TARGET_EMOJIS = ["🌑", "🌘", "🌗", "🌖", "🌕"]


def get_target_emoji(index: int) -> str:
    if index < len(TARGET_EMOJIS):
        return TARGET_EMOJIS[index]
    return TARGET_EMOJIS[-1]


class SignalStates(StatesGroup):
    currency = State()
    position_type = State()
    entries = State()          # منتظر متن یک Entry (اول یا دوم)
    entries_decision = State()  # منتظر تصمیم: افزودن Entry دوم یا اتمام
    targets = State()          # منتظر متن یک تارگت
    targets_decision = State()  # منتظر تصمیم: تارگت بعدی یا اتمام
    leverage = State()
    balance = State()
    sl = State()


# ۰. پاسخ به /start
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(
        "سلام ادمین 👋\n\n"
        "برای ارسال سیگنال جدید از دستور /new_signal استفاده کنید."
    )


# ── پشتیبان‌گیری و بازیابی (چون داده‌ها روی فایل JSON محلی ذخیره می‌شوند و با هر
#    دیپلوی روی سرویس‌هایی مثل Railway بدون Volume دائمی از بین می‌روند) ────────
class RestoreStates(StatesGroup):
    waiting_file = State()


@dp.message(F.text == "/backup")
async def cmd_backup(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    sent_any = False
    if os.path.exists(ACTIVE_SIGNALS_FILE):
        await message.answer_document(BufferedInputFile(
            open(ACTIVE_SIGNALS_FILE, "rb").read(), filename=ACTIVE_SIGNALS_FILE
        ))
        sent_any = True
    if os.path.exists(HISTORY_FILE):
        await message.answer_document(BufferedInputFile(
            open(HISTORY_FILE, "rb").read(), filename=HISTORY_FILE
        ))
        sent_any = True

    if sent_any:
        await message.answer("✅ فایل‌های پشتیبان بالا ارسال شد. آن‌ها را جایی امن (مثلاً Saved Messages) نگه دارید.")
    else:
        await message.answer("⚠️ هنوز هیچ سیگنال یا تاریخچه‌ای برای پشتیبان‌گیری وجود ندارد.")


@dp.message(F.text == "/restore")
async def cmd_restore(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(RestoreStates.waiting_file)
    await message.answer(
        "📥 فایل پشتیبان (active_signals.json یا signal_history.json) را به‌صورت Document بفرستید.\n"
        "می‌توانید هر دو فایل را پشت‌سرهم بفرستید."
    )


@dp.message(RestoreStates.waiting_file, F.document)
async def restore_receive_file(message: Message, state: FSMContext):
    filename = message.document.file_name
    if filename not in (ACTIVE_SIGNALS_FILE, HISTORY_FILE):
        await message.answer(
            f"⚠️ اسم فایل باید دقیقاً {ACTIVE_SIGNALS_FILE} یا {HISTORY_FILE} باشد."
        )
        return

    file = await bot.get_file(message.document.file_id)
    file_bytes = await bot.download_file(file.file_path)
    with open(filename, "wb") as f:
        f.write(file_bytes.read())

    await message.answer(f"✅ فایل {filename} با موفقیت بازیابی شد.")


@dp.message(F.text == "/done_restore")
async def cmd_done_restore(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await message.answer("✅ حالت بازیابی بسته شد.")


# ۱. شروع کار توسط ادمین
@dp.message(F.text == "/new_signal")
async def start_signal(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer("لطفاً نام جفت ارز را وارد کنید:\n(مثال: BTC/USDT)")
    await state.set_state(SignalStates.currency)


# ۲. دریافت نام ارز و پرسش درباره نوع پوزیشن
@dp.message(SignalStates.currency)
async def process_currency(message: Message, state: FSMContext):
    await state.update_data(currency=message.text.upper())

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 LONG", callback_data="type_LONG"),
         InlineKeyboardButton(text="🔴 SHORT", callback_data="type_SHORT")]
    ])

    await message.answer("نوع پوزیشن را انتخاب کنید:", reply_markup=kb)
    await state.set_state(SignalStates.position_type)


# ۳. دریافت نوع پوزیشن از طریق دکمه شیشه‌ای
@dp.callback_query(SignalStates.position_type, F.data.startswith("type_"))
async def process_type(callback: CallbackQuery, state: FSMContext):
    pos_type = callback.data.split("_")[1]
    await state.update_data(position_type=pos_type)

    await callback.message.answer("نقطه ورود (Entry) اول را وارد کنید:")
    await state.update_data(entries=[])
    await state.set_state(SignalStates.entries)
    await callback.answer()


# ۴. دریافت متن یک Entry (اول یا دوم)
@dp.message(SignalStates.entries)
async def process_entry_value(message: Message, state: FSMContext):
    data = await state.get_data()
    entries = data.get("entries", [])
    entries.append(message.text)
    await state.update_data(entries=entries)

    if len(entries) == 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزودن Entry دوم", callback_data="entry_add_second"),
             InlineKeyboardButton(text="✅ فقط همین Entry", callback_data="entry_done")]
        ])
        await message.answer("Entry اول ثبت شد. آیا Entry دوم هم دارید؟", reply_markup=kb)
        await state.set_state(SignalStates.entries_decision)
    else:
        await state.update_data(targets=[])
        await message.answer(f"{get_target_emoji(0)} تارگت شماره ۱ را وارد کنید:")
        await state.set_state(SignalStates.targets)


# ۵. پردازش تصمیم: افزودن Entry دوم یا اتمام
@dp.callback_query(SignalStates.entries_decision, F.data == "entry_add_second")
async def add_second_entry(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Entry دوم را وارد کنید:")
    await state.set_state(SignalStates.entries)
    await callback.answer()


@dp.callback_query(SignalStates.entries_decision, F.data == "entry_done")
async def finish_entries(callback: CallbackQuery, state: FSMContext):
    await state.update_data(targets=[])
    await callback.message.answer(f"{get_target_emoji(0)} تارگت شماره ۱ را وارد کنید:")
    await state.set_state(SignalStates.targets)
    await callback.answer()


# ۶. دریافت متن یک تارگت، سپس پرسیدن ادامه یا پایان
@dp.message(SignalStates.targets)
async def process_targets(message: Message, state: FSMContext):
    data = await state.get_data()
    targets = data.get("targets", [])
    targets.append(message.text)
    await state.update_data(targets=targets)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ تارگت بعدی", callback_data="target_more"),
         InlineKeyboardButton(text="✅ اتمام تارگت‌ها", callback_data="target_done")]
    ])
    await message.answer(
        f"تارگت شماره {len(targets)} ثبت شد.\nمی‌خواید تارگت بعدی رو اضافه کنید یا همینجا تمومش کنید؟",
        reply_markup=kb
    )
    await state.set_state(SignalStates.targets_decision)


# ۷. پردازش تصمیم: تارگت بعدی یا اتمام
@dp.callback_query(SignalStates.targets_decision, F.data == "target_more")
async def add_more_target(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    next_index = len(data.get("targets", []))
    await callback.message.answer(f"{get_target_emoji(next_index)} تارگت شماره {next_index + 1} را وارد کنید:")
    await state.set_state(SignalStates.targets)
    await callback.answer()


@dp.callback_query(SignalStates.targets_decision, F.data == "target_done")
async def finish_targets(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("💢 مقدار لوریج (LEV) را وارد کنید:\n(مثال: 10x)")
    await state.set_state(SignalStates.leverage)
    await callback.answer()


# ۸. دریافت لوریج
@dp.message(SignalStates.leverage)
async def process_leverage(message: Message, state: FSMContext):
    await state.update_data(leverage=message.text)
    await message.answer("🔘 درصد بالانس ورودی را وارد کنید:\n(مثال: 5%)")
    await state.set_state(SignalStates.balance)


# ۹. دریافت درصد بالانس
@dp.message(SignalStates.balance)
async def process_balance(message: Message, state: FSMContext):
    await state.update_data(balance=message.text)
    await message.answer("🔘 حد ضرر (Stop Loss) را وارد کنید:")
    await state.set_state(SignalStates.sl)


def default_leverage_for_pair(pair: str) -> str:
    """اگر Alert مقدار لوریج نفرستد، بر اساس نوع کوین یک پیش‌فرض منطقی برمی‌گرداند."""
    normalized = normalize_pair(pair)
    base = normalized.split("/")[0] if "/" in normalized else normalized

    major_coins = {"BTC", "ETH"}
    if base in major_coins:
        return "20"
    return "10"  # آلت‌کوین‌ها


def normalize_pair(pair: str) -> str:
    """اگر جفت‌ارز بدون اسلش بیاید (مثل BTCUSDT از TradingView)، به فرمت BTC/USDT تبدیل می‌کند."""
    pair = pair.strip().upper()
    if "/" in pair:
        return pair

    known_quotes = ["USDT", "USDC", "BUSD", "FDUSD", "USD", "BTC", "ETH"]
    for quote in known_quotes:
        if pair.endswith(quote) and len(pair) > len(quote):
            base = pair[: -len(quote)]
            return f"{base}/{quote}"

    return pair  # اگر تشخیص داده نشد، همان‌طور که آمده برمی‌گردد


async def build_and_send_signal(currency: str, position_type: str, entries: list,
                                  targets: list, leverage: str, balance: str, sl: str):
    """قالب‌بندی و ارسال سیگنال به کانال + ثبت آن برای پایش قیمت (مشترک بین ورود دستی و Webhook)"""
    currency = normalize_pair(currency)

    if len(entries) == 1:
        entry_block = f"Entry: {html.escape(entries[0])}"
    else:
        entry_block = (
            f"Entry 1: {html.escape(entries[0])}\n"
            f"Entry 2: {html.escape(entries[1])}"
        )

    targets_block = "\n".join(
        f"{get_target_emoji(i)} {html.escape(targets[i])}" for i in range(len(targets))
    )

    position_emoji = "🟢" if position_type == "LONG" else "🔴"

    signal_text = (
        f"<b>🎗️ NEW SIGNAL 🎗️</b>\n\n"
        f"Pair: #{html.escape(currency)}\n"
        f"Signal Type: {position_emoji} \"{position_type}\"\n\n"
        f"{entry_block}\n\n"
        f"💫Target\n"
        f"{targets_block}\n\n"
        f"💢LEV x: {html.escape(leverage)}\n\n"
        f"🔘balance: {html.escape(balance)}\n\n"
        f"🔘STOP LOSS: {html.escape(sl)}\n\n"
        f"❗️لطفاً طبق مشخصه‌های درج شده سیگنال اعلامی عمل کرده (بالانس، اهرم، استاپ) رعایت کنید.\n"
        f"پوزیشنی که تارگتش تاچ شده ورود نداره!\n"
        f"بعد از تاچ تارگت اول پوزیشن ریسک‌فری می‌شود! (استاپ نقطه ورود)"
    )

    entries_float = [float(str(e).replace(",", "")) for e in entries]
    targets_float = [float(str(t).replace(",", "")) for t in targets]
    sl_float = float(str(sl).replace(",", ""))

    leverage_match = re.search(r"\d+(\.\d+)?", leverage)
    leverage_number = float(leverage_match.group()) if leverage_match else 1.0

    calc_url = (
        f"{CALCULATOR_URL}?entry={entries_float[0]}"
        f"&sl={sl_float}&target={targets_float[0]}"
        f"&leverage={leverage_number}&type={position_type}"
    )
    calc_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 محاسبه‌گر حجم پوزیشن", url=calc_url)]
    ])

    await bot.send_message(chat_id=CHANNEL_ID, text=signal_text, parse_mode="HTML", reply_markup=calc_kb)

    bingx_symbol = currency_to_bingx_symbol(currency)

    signal_record = {
        "currency_display": currency,
        "bingx_symbol": bingx_symbol,
        "position_type": position_type,
        "leverage": leverage_number,
        "entry1": entries_float[0],
        "entry1_touched": False,
        "entry2": entries_float[1] if len(entries_float) > 1 else None,
        "entry2_touched": len(entries_float) <= 1,
        "targets": targets_float,
        "targets_touched": [False] * len(targets_float),
        "weights": get_target_weights(len(targets_float)),
        "closed_weight": 0.0,
        "accumulated_pct": 0.0,
        "current_stop": sl_float,
        "sl": sl_float,
        "sl_touched": False,
        "completed": False,
    }

    active_signals = load_active_signals()
    active_signals.append(signal_record)
    save_active_signals(active_signals)


# ۱۰. دریافت حد ضرر و ارسال نهایی به کانال
@dp.message(SignalStates.sl)
async def process_sl(message: Message, state: FSMContext):
    await state.update_data(sl=message.text)

    data = await state.get_data()

    await build_and_send_signal(
        currency=data["currency"],
        position_type=data["position_type"],
        entries=data["entries"],
        targets=data["targets"],
        leverage=data["leverage"],
        balance=data["balance"],
        sl=data["sl"],
    )
    await message.answer("✅ سیگنال با موفقیت قالب‌بندی و به کانال ارسال شد.")
    await state.clear()


async def fetch_bingx_price(symbol: str):
    """قیمت لحظه‌ی فیوچرز (Perpetual) یک جفت‌ارز را از بایننس‌ایکس می‌گیرد."""
    url = f"{BINGX_SWAP_TICKER_URL}?symbol={symbol}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json(content_type=None)

            if not isinstance(data, dict) or "data" not in data:
                raise RuntimeError(f"پاسخ غیرمنتظره از BingX برای {symbol} (status={resp.status}): {data}")

            price_info = data["data"]
            # ممکن است data یک دیکشنری تک یا لیستی از دیکشنری‌ها باشد
            if isinstance(price_info, list):
                if not price_info:
                    return None
                price_info = price_info[0]

            last_price = price_info.get("lastPrice") or price_info.get("price")
            if last_price is None:
                return None
            return float(last_price)


OUTCOME_LABELS = {
    "TP": "TP",
    "SL": "SL",
    "BE": "BE",
    "TS": "TS",
}


def build_report_image(batch: list) -> bytes:
    """تصویر گزارش عملکرد را روی پس‌زمینه‌ی برند کانال می‌سازد و بایت PNG را برمی‌گرداند."""
    bg = Image.open(BACKGROUND_IMAGE_PATH).convert("RGBA")
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    title_font = ImageFont.truetype(FONT_TITLE, 28)
    mono_font = ImageFont.truetype(FONT_MONO_BOLD, 20)
    mono_font_reg = ImageFont.truetype(FONT_MONO_REG, 19)
    footer_font = ImageFont.truetype(FONT_TITLE, 19)
    channel_font = ImageFont.truetype(FONT_TITLE, 32)

    # --- اسم کانال با سایه، بالای لوگو ---
    channel_name = "CryptoLogic"
    bbox = draw.textbbox((0, 0), channel_name, font=channel_font)
    text_w = bbox[2] - bbox[0]
    logo_center_x = 785
    text_x = logo_center_x - text_w / 2
    text_y = 108
    draw.text((text_x + 3, text_y + 3), channel_name, font=channel_font, fill=(0, 0, 0, 180))
    draw.text((text_x, text_y), channel_name, font=channel_font, fill=(0, 210, 255, 255))

    # --- پنل نیمه‌شفاف ---
    panel_box = (35, 95, 545, 525)
    draw.rounded_rectangle(panel_box, radius=20, fill=(4, 12, 24, 205), outline=(56, 189, 248, 160), width=2)

    pad_x = panel_box[0] + 30

    draw.text((pad_x, panel_box[1] + 25), "📊 PERFORMANCE REPORT", font=title_font, fill=(56, 189, 248, 255))
    draw.text((pad_x, panel_box[1] + 62), f"Last {len(batch)} Signals", font=mono_font_reg, fill=(160, 190, 210, 255))

    line_y = panel_box[1] + 100
    draw.line([(pad_x, line_y), (panel_box[2] - 30, line_y)], fill=(56, 189, 248, 120), width=2)

    header_y = line_y + 18
    col_pair_x = pad_x
    col_result_x = pad_x + 195
    col_pl_x = pad_x + 335

    draw.text((col_pair_x, header_y), "PAIR", font=mono_font, fill=(200, 220, 235, 255))
    draw.text((col_result_x, header_y), "RES", font=mono_font, fill=(200, 220, 235, 255))
    draw.text((col_pl_x, header_y), "P/L", font=mono_font, fill=(200, 220, 235, 255))

    sep2_y = header_y + 32
    draw.line([(pad_x, sep2_y), (panel_box[2] - 30, sep2_y)], fill=(56, 189, 248, 80), width=1)

    total_pct = 0.0
    wins = 0
    losses = 0

    y = sep2_y + 15
    for item in batch:
        pair = item["pair"][:11]
        outcome = item["outcome"]
        pct = item["result_pct"]
        color = OUTCOME_COLORS.get(outcome, (230, 240, 250))
        label = OUTCOME_LABELS.get(outcome, outcome)

        draw.text((col_pair_x, y), pair, font=mono_font_reg, fill=(230, 240, 250, 255))
        draw.text((col_result_x, y), label, font=mono_font_reg, fill=color + (255,))
        draw.text((col_pl_x, y), f"{pct:+.1f}%", font=mono_font_reg, fill=color + (255,))
        y += 34

        total_pct += pct
        if pct > 0:
            wins += 1
        elif pct < 0:
            losses += 1

    y += 6
    draw.line([(pad_x, y), (panel_box[2] - 30, y)], fill=(56, 189, 248, 120), width=2)
    y += 20
    total_color = (74, 222, 128) if total_pct >= 0 else (248, 113, 113)
    draw.text((pad_x, y), "Total P/L:", font=footer_font, fill=(200, 220, 235, 255))
    draw.text((pad_x + 130, y), f"{total_pct:+.1f}%", font=footer_font, fill=total_color + (255,))
    y += 32
    breakeven = len(batch) - wins - losses
    draw.text((pad_x, y), f"Wins: {wins}   Losses: {losses}   BE: {breakeven}",
              font=mono_font_reg, fill=(180, 200, 215, 255))

    final = Image.alpha_composite(bg, overlay).convert("RGB")
    buffer = io.BytesIO()
    final.save(buffer, format="PNG", quality=95)
    return buffer.getvalue()


async def maybe_send_summary(history: list) -> None:
    """اگر تعداد سیگنال‌های تاریخچه به مضرب SUMMARY_BATCH_SIZE رسیده باشد، تصویر گزارش ارسال می‌شود."""
    if len(history) == 0 or len(history) % SUMMARY_BATCH_SIZE != 0:
        return
    batch = history[-SUMMARY_BATCH_SIZE:]
    image_bytes = build_report_image(batch)
    photo = BufferedInputFile(image_bytes, filename="performance_report.png")
    await bot.send_photo(chat_id=CHANNEL_ID, photo=photo)


async def check_prices():
    """هر PRICE_CHECK_INTERVAL ثانیه، قیمت‌ها را چک کرده و در صورت تاچ‌شدن، به کانال اعلام می‌کند."""
    while True:
        await asyncio.sleep(PRICE_CHECK_INTERVAL)
        try:
            signals = load_active_signals()
            active = [s for s in signals if not s["completed"]]
            print(f"تعداد سیگنال‌های ذخیره‌شده: {len(signals)} [چرخه پایش]")
            if not active:
                continue

            changed = False

            for sig in signals:
                if sig["completed"]:
                    continue

                try:
                    price = await fetch_bingx_price(sig["bingx_symbol"])
                except Exception as e:
                    print(f"خطا در گرفتن قیمت {sig['bingx_symbol']}: {e}")
                    continue

                if price is None:
                    continue

                is_short = sig["position_type"] == "SHORT"
                pair_label = sig["currency_display"]
                print(
                    f"تارگت‌ها: {pair_label} | قیمت: {price} (منبع: BingX-Futures) | "
                    f"Entry1: {sig['entry1']} | SL: {sig['sl']}"
                )

                # --- چک کردن استاپ متحرک (اولویت اول، چون یعنی باقیمانده پوزیشن بسته می‌شود) ---
                stop_hit = (price >= sig["current_stop"]) if is_short else (price <= sig["current_stop"])
                if stop_hit and not sig["sl_touched"] and sig["closed_weight"] < 1.0:
                    sig["sl_touched"] = True
                    sig["completed"] = True
                    changed = True

                    remaining_weight = 1.0 - sig["closed_weight"]
                    is_original_sl = sig["closed_weight"] == 0.0
                    is_breakeven = abs(sig["current_stop"] - sig["entry1"]) < 1e-9

                    if is_original_sl:
                        raw_pct = abs(sig["current_stop"] - sig["entry1"]) / sig["entry1"] * 100
                        contribution = -raw_pct * sig.get("leverage", 1) * remaining_weight
                        outcome, header = "SL", "🔴 <b>STOP LOSS فعال شد</b>"
                    elif is_breakeven:
                        contribution = 0.0
                        outcome, header = "BE", "🟡 <b>ریسک‌فری فعال شد (Breakeven)</b>"
                    else:
                        raw_pct = abs(sig["current_stop"] - sig["entry1"]) / sig["entry1"] * 100
                        contribution = raw_pct * sig.get("leverage", 1) * remaining_weight
                        outcome, header = "TS", "🟢 <b>تریلینگ استاپ فعال شد (سود قفل‌شده)</b>"

                    sig["accumulated_pct"] += contribution
                    final_pct = sig["accumulated_pct"]

                    await bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=(
                            f"{header}\n\n"
                            f"Pair: #{html.escape(pair_label)}\n"
                            f"قیمت فعلی: {price}\n"
                            f"سطح استاپ: {sig['current_stop']}\n"
                            f"نتیجه‌ی نهایی این سیگنال: {final_pct:+.1f}٪"
                        ),
                        parse_mode="HTML",
                    )

                    history = load_history()
                    history.append({
                        "pair": pair_label,
                        "outcome": outcome,
                        "result_pct": final_pct,
                    })
                    save_history(history)
                    await maybe_send_summary(history)
                    continue

                # --- چک کردن Entry اول (همیشه، چه یک Entry باشد چه دو تا) ---
                if not sig.get("entry1_touched", False):
                    hit = (price >= sig["entry1"]) if is_short else (price <= sig["entry1"])
                    if hit:
                        sig["entry1_touched"] = True
                        changed = True
                        await bot.send_message(
                            chat_id=CHANNEL_ID,
                            text=(
                                f"🟢 <b>Entry فعال شد</b>\n\n"
                                f"Pair: #{html.escape(pair_label)}\n"
                                f"قیمت فعلی: {price}\n"
                                f"Entry: {sig['entry1']}"
                            ),
                            parse_mode="HTML",
                        )

                # --- چک کردن Entry دوم (فقط بعد از تاچ Entry اول، و اگر تعریف شده و هنوز فعال نشده) ---
                if sig.get("entry1_touched", False) and sig["entry2"] is not None and not sig["entry2_touched"]:
                    entry2_up = sig["entry2"] > sig["entry1"]
                    hit = (price >= sig["entry2"]) if entry2_up else (price <= sig["entry2"])
                    if hit:
                        sig["entry2_touched"] = True
                        changed = True
                        await bot.send_message(
                            chat_id=CHANNEL_ID,
                            text=(
                                f"🟡 <b>Entry دوم فعال شد</b>\n\n"
                                f"Pair: #{html.escape(pair_label)}\n"
                                f"قیمت فعلی: {price}\n"
                                f"Entry 2: {sig['entry2']}"
                            ),
                            parse_mode="HTML",
                        )

                # --- چک کردن تارگت‌ها به ترتیب (فقط بعد از تاچ‌شدن Entry) ---
                if not sig.get("entry1_touched", False):
                    continue

                for i, target in enumerate(sig["targets"]):
                    if sig["targets_touched"][i]:
                        continue
                    hit = (price <= target) if is_short else (price >= target)
                    if hit:
                        sig["targets_touched"][i] = True
                        changed = True

                        weight_i = sig["weights"][i]
                        raw_pct = abs(target - sig["entry1"]) / sig["entry1"] * 100
                        leveraged_pct = raw_pct * sig.get("leverage", 1)
                        contribution = weight_i * leveraged_pct
                        sig["accumulated_pct"] += contribution
                        sig["closed_weight"] += weight_i

                        is_last_target = (i == len(sig["targets"]) - 1)

                        if is_last_target:
                            # تمام پوزیشن بسته شد، دیگر نیازی به جابه‌جایی استاپ نیست
                            sig["completed"] = True
                            extra_note = "\n🏁 تمام پوزیشن بسته شد (خروج کامل)"
                        else:
                            # استاپ را به تارگت قبلی (یا نقطه‌ورود اگر تارگت اول بود) منتقل کن
                            new_stop = sig["entry1"] if i == 0 else sig["targets"][i - 1]
                            sig["current_stop"] = new_stop
                            extra_note = (
                                f"\n🔒 {weight_i * 100:.0f}٪ پوزیشن بسته شد، استاپ به {new_stop} منتقل شد"
                            )

                        await bot.send_message(
                            chat_id=CHANNEL_ID,
                            text=(
                                f"✅ <b>تارگت {i + 1} تاچ شد</b>\n\n"
                                f"Pair: #{html.escape(pair_label)}\n"
                                f"قیمت فعلی: {price}\n"
                                f"تارگت {i + 1}: {target}\n"
                                f"📈 سود این بخش: +{leveraged_pct:.1f}٪ (با احتساب اهرم {sig.get('leverage', 1):g}x)"
                                f"{extra_note}"
                            ),
                            parse_mode="HTML",
                        )

                        if is_last_target:
                            history = load_history()
                            history.append({
                                "pair": pair_label,
                                "outcome": "TP",
                                "result_pct": sig["accumulated_pct"],
                            })
                            save_history(history)
                            await maybe_send_summary(history)

            if changed:
                save_active_signals(signals)

            await process_pending_queue()

        except Exception as e:
            print(f"خطا در چک کردن قیمت‌ها: {e}")


async def tradingview_webhook(request: web.Request):
    """دریافت سیگنال از TradingView (یا هر منبع دیگری که JSON مشابه بفرستد)"""
    if request.query.get("secret") != WEBHOOK_SECRET:
        return web.json_response({"error": "unauthorized"}, status=403)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    try:
        currency = str(payload["pair"])
        position_type = str(payload["type"]).upper()  # LONG یا SHORT
        entries = [str(payload["entry"])]
        if payload.get("entry2"):
            entries.append(str(payload["entry2"]))
        targets = [str(t) for t in payload["targets"]]
        leverage = str(payload.get("leverage", default_leverage_for_pair(currency))) + "x"
        balance = str(payload.get("balance", DEFAULT_RISK_BALANCE))
        sl = str(payload["sl"])
    except (KeyError, TypeError) as e:
        return web.json_response({"error": f"missing field: {e}"}, status=400)

    signal_params = dict(
        currency=currency, position_type=position_type, entries=entries,
        targets=targets, leverage=leverage, balance=balance, sl=sl,
    )

    active = [s for s in load_active_signals() if not s["completed"]]
    if len(active) >= MAX_ACTIVE_SIGNALS:
        pending = load_pending_signals()
        pending.append(signal_params)
        save_pending_signals(pending)
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"⏸ سیگنال {currency} به‌خاطر پر بودن ظرفیت "
                f"({MAX_ACTIVE_SIGNALS} سیگنال فعال) در صف قرار گرفت."
            )
        )
        return web.json_response({"status": "queued"})

    await build_and_send_signal(**signal_params)
    return web.json_response({"status": "ok"})


async def start_webhook_server():
    app = web.Application()
    app.router.add_post("/webhook/tradingview", tradingview_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 وب‌سرور Webhook روی پورت {port} فعال شد.")


# ── دستورهای تشخیصی برای دیدن و آزادکردن اسلات‌های سیگنال فعال ──────────────
@dp.message(F.text == "/list_active")
async def cmd_list_active(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    signals = load_active_signals()
    active = [s for s in signals if not s["completed"]]

    if not active:
        await message.answer("✅ هیچ سیگنال فعالی وجود ندارد. هر دو اسلات خالی است.")
        return

    lines = []
    for s in active:
        lines.append(
            f"• {s['currency_display']} ({s['position_type']}) — Entry: {s['entry1']} — "
            f"وضعیت Entry: {'تاچ‌شده' if s['entry1_touched'] else 'هنوز تاچ نشده'}"
        )
    await message.answer(
        f"📋 سیگنال‌های فعال ({len(active)} از {MAX_ACTIVE_SIGNALS}):\n\n" + "\n".join(lines)
        + "\n\nبرای بستن دستی هرکدام: /force_complete PAIR (مثلاً /force_complete BTC/USDT)"
    )


@dp.message(F.text.startswith("/force_complete"))
async def cmd_force_complete(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ فرمت درست: /force_complete BTC/USDT")
        return

    target_pair = parts[1].strip().upper()
    signals = load_active_signals()
    found = False
    for s in signals:
        if s["currency_display"].upper() == target_pair and not s["completed"]:
            s["completed"] = True
            found = True

    if found:
        save_active_signals(signals)
        await message.answer(f"✅ سیگنال {target_pair} به‌صورت دستی بسته شد. یک اسلات خالی شد.")
        await process_pending_queue()
    else:
        await message.answer(f"⚠️ سیگنال فعالی با نام {target_pair} پیدا نشد.")


async def main():
    asyncio.create_task(check_prices())
    await start_webhook_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
