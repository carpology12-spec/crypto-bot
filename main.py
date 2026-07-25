import os
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
ADMIN_ID = int(os.environ["ADMIN_ID"])

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class SignalStates(StatesGroup):
    currency = State()
    position_type = State()
    entry = State()
    tp = State()
    sl = State()


@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(
        "👋 سلام ادمین!\n\n"
        "برای ارسال سیگنال جدید از دستور /new_signal استفاده کنید."
    )


@dp.message(F.text == "/new_signal")
async def start_signal(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("لطفاً نام جفت ارز را وارد کنید:\n(مثال: BTC/USDT)")
    await state.set_state(SignalStates.currency)


@dp.message(SignalStates.currency)
async def process_currency(message: Message, state: FSMContext):
    await state.update_data(currency=message.text.upper())
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 LONG", callback_data="type_LONG"),
            InlineKeyboardButton(text="🔴 SHORT", callback_data="type_SHORT"),
        ]
    ])
    await message.answer("نوع پوزیشن را انتخاب کنید:", reply_markup=kb)
    await state.set_state(SignalStates.position_type)


@dp.callback_query(SignalStates.position_type, F.data.startswith("type_"))
async def process_type(callback: CallbackQuery, state: FSMContext):
    pos_type = callback.data.split("_")[1]
    await state.update_data(position_type=pos_type)
    await callback.message.answer("نقطه ورود (Entry Price) را وارد کنید:")
    await state.set_state(SignalStates.entry)
    await callback.answer()


@dp.message(SignalStates.entry)
async def process_entry(message: Message, state: FSMContext):
    await state.update_data(entry=message.text)
    await message.answer("حد سود (Take Profit) را وارد کنید:")
    await state.set_state(SignalStates.tp)


@dp.message(SignalStates.tp)
async def process_tp(message: Message, state: FSMContext):
    await state.update_data(tp=message.text)
    await message.answer("حد ضرر (Stop Loss) را وارد کنید:")
    await state.set_state(SignalStates.sl)


@dp.message(SignalStates.sl)
async def process_sl(message: Message, state: FSMContext):
    await state.update_data(sl=message.text)
    data = await state.get_data()

    emoji = "🟢" if data["position_type"] == "LONG" else "🔴"

    signal_text = (
        f"🎯 **NEW SIGNAL** 🎯\n\n"
        f"🪙 **Pair:** #{data['currency']}\n"
        f"{emoji} **Type:** {data['position_type']}\n"
        f"📥 **Entry:** {data['entry']}\n"
        f"🚀 **Take Profit:** {data['tp']}\n"
        f"🛑 **Stop Loss:** {data['sl']}\n\n"
        f"⚠️ مدیریت سرمایه فراموش نشود!"
    )

    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=signal_text,
            parse_mode="Markdown",
        )
        await message.answer("✅ سیگنال با موفقیت به کانال ارسال شد.")
    except Exception as e:
        await message.answer(
            f"❌ خطا در ارسال به کانال. مطمئن شوید ربات در کانال ادمین است.\n"
            f"خطا: {str(e)}"
        )

    await state.clear()


async def main():
    print("Bot is starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
