from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID
from database import (
    get_profile,
    upsert_profile,
    get_shift,
    get_active_shift_by_id,   # ← исправлено: импортируется из database.py
    get_member_count,
    add_shift_member,
    get_user_shift_membership,
)
from utils.states import RegistrationStates

router = Router()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def slot_keyboard(shift_id: int, main_free: int, reserve_free: int):
    builder = InlineKeyboardBuilder()
    if main_free > 0:
        builder.button(
            text=f"🔵 Основной состав (свободно: {main_free})",
            callback_data=f"slot:main:{shift_id}",
        )
    if reserve_free > 0:
        builder.button(
            text=f"🟡 Резерв (свободно: {reserve_free})",
            callback_data=f"slot:reserve:{shift_id}",
        )
    builder.adjust(1)
    return builder.as_markup()


async def notify_admin(bot, text: str):
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    except Exception:
        pass


# ─── Step 1: нажали «Записаться» ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("register_shift:"))
async def start_register(callback: CallbackQuery, state: FSMContext):
    shift_id = int(callback.data.split(":")[1])
    shift = await get_active_shift_by_id(shift_id)

    if not shift:
        await callback.answer("❌ Смена уже недоступна", show_alert=True)
        return

    already = await get_user_shift_membership(shift_id, callback.from_user.id)
    if already:
        await callback.answer("⚠️ Ты уже записан на эту смену", show_alert=True)
        return

    main_taken = await get_member_count(shift_id, "main")
    reserve_taken = await get_member_count(shift_id, "reserve")
    main_free = max(0, shift["main_slots"] - main_taken)
    reserve_free = max(0, shift["reserve_slots"] - reserve_taken)

    if main_free == 0 and reserve_free == 0:
        await callback.answer("😔 Все места заняты", show_alert=True)
        return

    await state.update_data(shift_id=shift_id)
    await callback.message.edit_text(
        "Выбери тип записи:",
        reply_markup=slot_keyboard(shift_id, main_free, reserve_free),
    )


# ─── Step 2: выбор основа/резерв ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("slot:"))
async def choose_slot(callback: CallbackQuery, state: FSMContext):
    _, slot_type, shift_id_str = callback.data.split(":")
    shift_id = int(shift_id_str)
    shift = await get_active_shift_by_id(shift_id)

    if not shift:
        await callback.answer("❌ Смена уже недоступна", show_alert=True)
        return

    await state.update_data(shift_id=shift_id, slot_type=slot_type)

    profile = await get_profile(callback.from_user.id)

    # Если анкета уже заполнена — сразу записываем
    if profile and profile.get("full_name") and profile.get("phone"):
        await finalize_registration(callback, state, profile, shift)
        return

    # Иначе — заполняем анкету
    await state.set_state(RegistrationStates.filling_name)
    await callback.message.edit_text(
        "📝 <b>Заполни анкету</b>\n\nВведи своё полное имя (ФИО):",
        parse_mode="HTML",
    )


# ─── Анкета ───────────────────────────────────────────────────────────────────

@router.message(RegistrationStates.filling_name)
async def fill_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 3:
        await message.answer("Имя слишком короткое. Введи ФИО полностью:")
        return
    await state.update_data(full_name=name)
    await state.set_state(RegistrationStates.filling_age)
    await message.answer("Введи свой возраст:")


@router.message(RegistrationStates.filling_age)
async def fill_age(message: Message, state: FSMContext):
    if not message.text.isdigit() or not (16 <= int(message.text) <= 80):
        await message.answer("Введи корректный возраст (16–80):")
        return
    await state.update_data(age=int(message.text))
    await state.set_state(RegistrationStates.filling_phone)
    await message.answer("Введи номер телефона (например: +79001234567):")


@router.message(RegistrationStates.filling_phone)
async def fill_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if len(phone) < 10:
        await message.answer("Номер телефона слишком короткий. Попробуй ещё раз:")
        return

    data = await state.get_data()
    await upsert_profile(
        message.from_user.id,
        full_name=data["full_name"],
        age=data["age"],
        phone=phone,
    )

    profile = await get_profile(message.from_user.id)
    shift = await get_active_shift_by_id(data["shift_id"])

    if not shift:
        await state.clear()
        await message.answer("❌ Смена уже недоступна.")
        return

    await finalize_registration(message, state, profile, shift)


# ─── Финальная запись ─────────────────────────────────────────────────────────

async def finalize_registration(event, state: FSMContext, profile: dict, shift: dict):
    data = await state.get_data()
    shift_id = shift["id"]
    slot_type = data.get("slot_type", "main")
    telegram_id = event.from_user.id

    # Считаем позицию
    count = await get_member_count(shift_id, slot_type)
    slots_total = shift["main_slots"] if slot_type == "main" else shift["reserve_slots"]

    if count >= slots_total:
        await state.clear()
        msg = "😔 Место только что заняли. Попробуй другой тип записи."
        if hasattr(event, "message"):
            await event.message.edit_text(msg)
        else:
            await event.answer(msg)
        return

    position = count + 1
    await add_shift_member(shift_id, telegram_id, slot_type, position)
    await state.clear()

    slot_label = "основной состав" if slot_type == "main" else "резерв"
    confirm_text = (
        f"✅ <b>Ты записан!</b>\n\n"
        f"📅 Дата: {shift['date']}\n"
        f"📍 Адрес: {shift['address']}\n"
        f"💰 Оплата: {shift['payment']}\n"
        f"👤 Тип: {slot_label}, позиция №{position}\n\n"
        f"Вечером придёт напоминание с просьбой подтвердить участие."
    )

    if hasattr(event, "message"):
        await event.message.edit_text(confirm_text, parse_mode="HTML")
        bot = event.message.bot
    else:
        await event.answer(confirm_text, parse_mode="HTML")
        bot = event.bot

    # Уведомление админу
    new_count = await get_member_count(shift_id, slot_type)
    admin_text = (
        f"🔔 <b>Новая запись на смену</b>\n"
        f"👤 {profile.get('full_name', 'Без имени')} (@{event.from_user.username or 'нет'})\n"
        f"📅 {shift['date']} | {shift['city']}\n"
        f"Тип: {slot_label} ({new_count}/{slots_total})"
    )
    if new_count == slots_total:
        admin_text += f"\n\n🎉 <b>{'Основной состав' if slot_type == 'main' else 'Резерв'} заполнен!</b>"

    await notify_admin(bot, admin_text)
