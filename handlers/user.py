from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import CITIES, ADMIN_ID
from database import get_user, create_user, get_profile, upsert_profile, get_active_shift_by_city
from utils.states import RegistrationStates

router = Router()


def city_keyboard():
    builder = InlineKeyboardBuilder()
    for city in CITIES:
        builder.button(text=city, callback_data=f"city:{city}")
    builder.adjust(3)
    return builder.as_markup()


def welcome_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Продолжить", callback_data="welcome_continue")
    return builder.as_markup()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        await message.answer("👋 Добро пожаловать в админ-панель!\n\nИспользуй /admin для управления.")
        return

    await create_user(message.from_user.id, message.from_user.username)
    profile = await get_profile(message.from_user.id)

    if profile and profile.get("city"):
        await show_active_shift(message, profile["city"])
        return

    await state.set_state(RegistrationStates.choosing_city)
    await message.answer(
        "👋 Привет! Я бот для записи на смены.\n\nВыбери свой город:",
        reply_markup=city_keyboard(),
    )


@router.callback_query(RegistrationStates.choosing_city, F.data.startswith("city:"))
async def choose_city(callback: CallbackQuery, state: FSMContext):
    city = callback.data.split(":")[1]
    await upsert_profile(callback.from_user.id, city=city)
    await state.clear()

    await callback.message.edit_text(
        f"🏙 Отлично! Ты выбрал город: <b>{city}</b>\n\n"
        "Здесь ты сможешь:\n"
        "• Видеть актуальные смены\n"
        "• Записываться на работу\n"
        "• Получать напоминания\n\n"
        "Нажми «Продолжить», чтобы увидеть доступные смены.",
        parse_mode="HTML",
        reply_markup=welcome_keyboard(),
    )


@router.callback_query(F.data == "welcome_continue")
async def welcome_continue(callback: CallbackQuery):
    profile = await get_profile(callback.from_user.id)
    if not profile:
        return
    await callback.message.delete()
    await show_active_shift(callback.message, profile["city"], user_id=callback.from_user.id)


async def show_active_shift(message: Message, city: str, user_id: int | None = None):
    shift = await get_active_shift_by_city(city)
    if not shift:
        await message.answer(
            f"😔 В городе <b>{city}</b> пока нет активных смен.\n\n"
            "Как только появится новая смена — ты получишь уведомление!",
            parse_mode="HTML",
        )
        return

    text = (
        f"📋 <b>Доступная смена</b>\n\n"
        f"🏙 Город: {shift['city']}\n"
        f"📅 Дата: {shift['date']}\n"
        f"📍 Адрес: {shift['address']}\n"
        f"💰 Оплата: {shift['payment']}\n"
    )
    if shift.get("conditions"):
        text += f"📝 Условия: {shift['conditions']}\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Записаться", callback_data=f"register_shift:{shift['id']}")

    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())