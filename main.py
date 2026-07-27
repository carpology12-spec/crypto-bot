from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
import asyncio
import os
import html
import json
import aiohttp

# این سه مقدار از Environment Variables خوانده می‌شوند
# (همان‌هایی که در Railway → Variables تنظیم کرده‌اید: BOT_TOKEN, CHANNEL_ID, ADMIN_ID)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- تنظیمات پایش قیمت (برای اعلام خودکار تاچ‌شدن تارگت/استاپ/Entry دوم) ---
PRICE_CHECK_INTERVAL = 60  # هر ۶۰ ثانیه یک‌بار قیمت‌ها چک می‌شود
ACTIVE_SIGNALS_FILE = "active_signals.json"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
BINGX_TICKER_URL = "https://open-api.bingx.com/openApi/spot/v1/ticker/24hr"

# نگاشت نماد ارز به شناسه‌ی CoinGecko (در صورت نیاز به ارز جدید، همین‌جا اضافه کنید)
COINGECKO_ID_MAP = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin", "XRP": "ripple",
    "ADA": "cardano", "SOL": "solana", "DOGE": "dogecoin", "DOT": "polkadot",
    "MATIC": "matic-network", "LTC": "litecoin", "TRX": "tron", "AVAX": "avalanche-2",
    "LINK": "chainlink", "ATOM": "cosmos", "XLM": "stellar", "ETC": "ethereum-classic",
    "FIL": "filecoin", "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
    "NEAR": "near", "SUI": "sui", "TON": "the-open-network", "SHIB": "shiba-inu",
    "PEPE": "pepe", "INJ": "injective-protocol", "FTM": "fantom", "ALGO": "algorand",
    "VET": "vechain", "ICP": "internet-computer", "HBAR": "hedera-hashgraph",
    "EOS": "eos", "XMR": "monero", "BCH": "bitcoin-cash", "UNI": "uniswap",
    "AAVE": "aave", "MKR": "maker", "SAND": "the-sandbox", "MANA": "decentraland",
    "GALA": "gala", "APE": "apecoin", "TIA": "celestia", "WIF": "dogwifcoin",
    "RUNE": "thorchain", "DYDX": "dydx", "LDO": "lido-dao", "CRV": "curve-dao-token",
    "SNX": "synthetix-network-token", "GRT": "the-graph", "THETA": "theta-token",
    "AXS": "axie-infinity", "IMX": "immutable-x", "STX": "blockstack",
    "JUP": "jupiter-exchange-solana", "PYTH": "pyth-network", "ONDO": "ondo-finance",
    "ENA": "ethena", "TAO": "bittensor", "NOT": "notcoin", "WLD": "worldcoin-wld",
}


def get_coingecko_id(currency_display: str):
    base_symbol = currency_display.split("/")[0].strip().upper()
    return COINGECKO_ID_MAP.get(base_symbol)


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

# آیکون هر تارگت به ترتیب (دقیقاً مطابق نمونه)
# اگر تعداد تارگت‌ها از ۵ بیشتر شود، آیکون آخر (🌕) برای بقیه تکرار می‌شود
TARGET_EMOJIS = ["🌑", "🌘", "🌗", "🌖", "🌕"]


def get_target_emoji(index: int) -> str:
    if index < len(TARGET_EMOJIS):
        return TARGET_EMOJIS[index]
    return TARGET_EMOJIS[-1]


# تعریف وضعیت‌های مختلف برای گرفتن قدم‌به‌قدم اطلاعات
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


# ۱. شروع کار توسط ادمین
@dp.message(F.text == "/new_signal")
async def start_signal(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return  # اگر کاربر ادمین نبود، ربات واکنشی نشان نمی‌دهد

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
        # دو Entry ثبت شد، مستقیم برو سراغ تارگت‌ها
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


# ۵. دریافت متن یک تارگت، سپس پرسیدن ادامه یا پایان
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


# ۶. پردازش تصمیم: تارگت بعدی یا اتمام
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


# ۶. دریافت لوریج
@dp.message(SignalStates.leverage)
async def process_leverage(message: Message, state: FSMContext):
    await state.update_data(leverage=message.text)
    await message.answer("🔘 درصد بالانس ورودی را وارد کنید:\n(مثال: 5%)")
    await state.set_state(SignalStates.balance)


# ۷. دریافت درصد بالانس
@dp.message(SignalStates.balance)
async def process_balance(message: Message, state: FSMContext):
    await state.update_data(balance=message.text)
    await message.answer("🔘 حد ضرر (Stop Loss) را وارد کنید:")
    await state.set_state(SignalStates.sl)


# ۸. دریافت حد ضرر و ارسال نهایی به کانال
@dp.message(SignalStates.sl)
async def process_sl(message: Message, state: FSMContext):
    await state.update_data(sl=message.text)

    data = await state.get_data()
    entries = data["entries"]
    targets = data["targets"]

    # نمایش یک یا دو Entry
    if len(entries) == 1:
        entry_block = f"Entry: {html.escape(entries[0])}"
    else:
        entry_block = (
            f"Entry 1: {html.escape(entries[0])}\n"
            f"Entry 2: {html.escape(entries[1])}"
        )

    # ساخت بخش تارگت‌ها با آیکون‌های ماه (تعداد متغیر)
    targets_block = "\n".join(
        f"{get_target_emoji(i)} {html.escape(targets[i])}" for i in range(len(targets))
    )

    # ایموجی رنگی بر اساس نوع پوزیشن (چون تلگرام رنگ متن را پشتیبانی نمی‌کند)
    position_emoji = "🟢" if data["position_type"] == "LONG" else "🔴"

    signal_text = (
        f"<b>🎗️ NEW SIGNAL 🎗️</b>\n\n"
        f"Pair: #{html.escape(data['currency'])}\n"
        f"Signal Type: {position_emoji} \"{data['position_type']}\"\n\n"
        f"{entry_block}\n\n"
        f"💫Target\n"
        f"{targets_block}\n\n"
        f"💢LEV x: {html.escape(data['leverage'])}\n\n"
        f"🔘balance: {html.escape(data['balance'])}\n\n"
        f"🔘STOP LOSS: {html.escape(data['sl'])}\n\n"
        f"❗️لطفاً طبق مشخصه‌های درج شده سیگنال اعلامی عمل کرده (بالانس، اهرم، استاپ) رعایت کنید.\n"
        f"پوزیشنی که تارگتش تاچ شده ورود نداره!\n"
        f"بعد از تاچ تارگت اول پوزیشن ریسک‌فری می‌شود! (استاپ نقطه ورود)"
    )

    await bot.send_message(chat_id=CHANNEL_ID, text=signal_text, parse_mode="HTML")
    await message.answer("✅ سیگنال با موفقیت قالب‌بندی و به کانال ارسال شد.")

    # ثبت این سیگنال در لیست سیگنال‌های فعال برای پایش خودکار قیمت
    coingecko_id = get_coingecko_id(data["currency"])
    bingx_symbol = data["currency"].replace("/", "-").replace(" ", "").upper()
    entries_float = [float(e.replace(",", "")) for e in entries]
    targets_float = [float(t.replace(",", "")) for t in targets]
    sl_float = float(data["sl"].replace(",", ""))

    signal_record = {
        "currency_display": data["currency"],
        "coingecko_id": coingecko_id,
        "bingx_symbol": bingx_symbol,
        "position_type": data["position_type"],
        "entry1": entries_float[0],
        "entry2": entries_float[1] if len(entries_float) > 1 else None,
        "entry2_touched": len(entries_float) <= 1,  # اگر Entry دومی نبود، نیازی به اعلام ندارد
        "targets": targets_float,
        "targets_touched": [False] * len(targets_float),
        "sl": sl_float,
        "sl_touched": False,
        "completed": False,
    }

    active_signals = load_active_signals()
    active_signals.append(signal_record)
    save_active_signals(active_signals)

    if coingecko_id is None:
        await message.answer(
            "⚠️ توجه: این جفت‌ارز در لیست شناخته‌شده نیست، پس پایش خودکار قیمت "
            "برای این سیگنال انجام نمی‌شود (ولی خود سیگنال به کانال ارسال شد)."
        )

    await state.clear()


async def fetch_bingx_prices() -> dict:
    """قیمت لحظه‌ای همه‌ی جفت‌ارزهای BingX را یکجا می‌گیرد."""
    async with aiohttp.ClientSession() as session:
        async with session.get(BINGX_TICKER_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            raw = await resp.json(content_type=None)

            # BingX معمولاً پاسخ را داخل کلید "data" بسته‌بندی می‌کند
            items = raw.get("data", raw) if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                raise RuntimeError(f"پاسخ غیرمنتظره از BingX (status={resp.status}): {raw}")

            prices = {}
            for item in items:
                symbol = item.get("symbol")
                price_str = item.get("lastPrice") or item.get("price") or item.get("close")
                if symbol and price_str is not None:
                    try:
                        prices[symbol] = float(price_str)
                    except (TypeError, ValueError):
                        continue
            return prices


async def fetch_prices_by_ids(ids: list) -> dict:
    """قیمت لحظه‌ای (به دلار) چند ارز را یکجا از CoinGecko می‌گیرد."""
    if not ids:
        return {}
    ids_param = ",".join(sorted(set(ids)))
    url = f"{COINGECKO_URL}?ids={ids_param}&vs_currencies=usd"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json(content_type=None)

            if not isinstance(data, dict):
                raise RuntimeError(f"پاسخ غیرمنتظره از CoinGecko (status={resp.status}): {data}")

            return {cg_id: info.get("usd") for cg_id, info in data.items()}


async def check_prices():
    """هر PRICE_CHECK_INTERVAL ثانیه، قیمت‌ها را چک کرده و در صورت تاچ‌شدن، به کانال اعلام می‌کند."""
    while True:
        await asyncio.sleep(PRICE_CHECK_INTERVAL)
        try:
            signals = load_active_signals()
            if not signals:
                continue

            ids_needed = [
                sig["coingecko_id"] for sig in signals
                if not sig["completed"] and sig.get("coingecko_id")
            ]

            # ابتدا BingX را امتحان می‌کنیم (اولویت اول)
            bingx_prices = {}
            bingx_ok = False
            try:
                bingx_prices = await fetch_bingx_prices()
                bingx_ok = True
            except Exception as e:
                print(f"BingX در دسترس نبود، برگشت به CoinGecko: {e}")

            # برای هر سیگنالی که BingX قیمتش را نداشت (یا BingX کلاً کار نکرد)، از CoinGecko استفاده می‌شود
            coingecko_prices = {}
            missing_ids = [
                sig["coingecko_id"] for sig in signals
                if not sig["completed"] and sig.get("coingecko_id")
                and (not bingx_ok or sig.get("bingx_symbol") not in bingx_prices)
            ]
            if missing_ids:
                try:
                    coingecko_prices = await fetch_prices_by_ids(missing_ids)
                except Exception as e:
                    print(f"خطا در گرفتن قیمت از CoinGecko: {e}")

            if not bingx_prices and not coingecko_prices:
                continue

            changed = False

            for sig in signals:
                if sig["completed"] or not sig.get("coingecko_id"):
                    continue

                price = bingx_prices.get(sig.get("bingx_symbol"))
                if price is None:
                    price = coingecko_prices.get(sig["coingecko_id"])
                if price is None:
                    continue

                is_short = sig["position_type"] == "SHORT"
                pair_label = sig["currency_display"]

                # --- چک کردن Stop Loss (اولویت اول، چون یعنی پوزیشن بسته شده) ---
                sl_hit = (price >= sig["sl"]) if is_short else (price <= sig["sl"])
                sl_pending = sig.get("sl_pending", False)
                if sl_hit and not sig["sl_touched"]:
                    if not sl_pending:
                        # اولین باری که دیده شد؛ صبر می‌کنیم دور بعد هم تایید بشه (جلوگیری از خطای کش)
                        sig["sl_pending"] = True
                        changed = True
                        continue
                    sig["sl_touched"] = True
                    sig["completed"] = True
                    changed = True
                    await bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=(
                            f"🔴 <b>STOP LOSS فعال شد</b>\n\n"
                            f"Pair: #{html.escape(pair_label)}\n"
                            f"قیمت فعلی: {price}\n"
                            f"استاپ: {sig['sl']}"
                        ),
                        parse_mode="HTML",
                    )
                    continue  # پوزیشن بسته شد، دیگر تارگت‌ها را چک نکن
                elif sl_pending and not sl_hit:
                    sig["sl_pending"] = False  # دور بعد دیگر تایید نشد، پس کش اشتباه بوده
                    changed = True

                # --- چک کردن Entry دوم (اگر تعریف شده و هنوز فعال نشده) ---
                if sig["entry2"] is not None and not sig["entry2_touched"]:
                    entry2_up = sig["entry2"] > sig["entry1"]
                    hit = (price >= sig["entry2"]) if entry2_up else (price <= sig["entry2"])
                    entry2_pending = sig.get("entry2_pending", False)
                    if hit:
                        if not entry2_pending:
                            sig["entry2_pending"] = True
                            changed = True
                        else:
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
                    elif entry2_pending:
                        sig["entry2_pending"] = False
                        changed = True

                # --- چک کردن تارگت‌ها به ترتیب ---
                for i, target in enumerate(sig["targets"]):
                    if sig["targets_touched"][i]:
                        continue
                    hit = (price <= target) if is_short else (price >= target)
                    pending_key = f"target_{i}_pending"
                    if hit:
                        if not sig.get(pending_key, False):
                            sig[pending_key] = True
                            changed = True
                            continue
                        sig["targets_touched"][i] = True
                        changed = True
                        extra_note = (
                            "\n🔒 پوزیشن ریسک‌فری شد (استاپ به نقطه ورود منتقل کنید)"
                            if i == 0 else ""
                        )
                        await bot.send_message(
                            chat_id=CHANNEL_ID,
                            text=(
                                f"✅ <b>تارگت {i + 1} تاچ شد</b>\n\n"
                                f"Pair: #{html.escape(pair_label)}\n"
                                f"قیمت فعلی: {price}\n"
                                f"تارگت {i + 1}: {target}"
                                f"{extra_note}"
                            ),
                            parse_mode="HTML",
                        )
                    elif sig.get(pending_key, False):
                        sig[pending_key] = False
                        changed = True

                if all(sig["targets_touched"]):
                    sig["completed"] = True
                    changed = True

            if changed:
                save_active_signals(signals)

        except Exception as e:
            print(f"خطا در چک کردن قیمت‌ها: {e}")


async def main():
    asyncio.create_task(check_prices())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
