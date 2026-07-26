from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
import asyncio
import os
import html

# این سه مقدار از Environment Variables خوانده می‌شوند
# (همان‌هایی که در Railway → Variables تنظیم کرده‌اید: BOT_TOKEN, CHANNEL_ID, ADMIN_ID)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

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

    signal_text = (
        f"<b>🎗️ NEW SIGNAL 🎗️</b>\n\n"
        f"Pair: #{html.escape(data['currency'])}\n"
        f"Signal Type: \"{data['position_type']}\"\n\n"
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

    await state.clear()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
