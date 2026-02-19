"""
Блок 7 — Запрос на разблокировку от заблокированного сотрудника.
Роутер подключается ПОСЛЕДНИМ в main.py — перехватывает любое сообщение
заблокированного пользователя, которое не поймали предыдущие роутеры.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from config import ADMIN_ID
from database import get_profile, get_user, create_unblock_request
from utils.states import UnblockStates

router = Router()


# ─── Перехват заблокированного пользователя ──────────────────────────────────

@router.message(F.text, StateFilter("*"))
async def intercept_blocked_user(message: Message, state: FSMContext):
    """
    Срабатывает на любое текстовое сообщение, которое не поймали
    предыдущие роутеры. Проверяем — заблокирован ли пользователь.
    """
    telegram_id = message.from_user.id

    # Пропускаем команды и админа
    if message.text and message.text.startswith("/"):
        return
    if telegram_id == ADMIN_ID:
        return

    user = await get_user(telegram_id)
    profile = await get_profile(telegram_id)

    # Незарегистрированных не трогаем — пусть обработает user.py
    if not user or not profile:
        return

    # Активный пользователь — не трогаем
    if user.get("is_active", 1) == 1 and profile.get("is_active", 1) == 1:
        return

    # Заблокирован — проверяем текущий FSM
    current_state = await state.get_state()

    if current_state == UnblockStates.waiting_message.state:
        # Уже в процессе подачи заявки — обрабатываем текст
        await _handle_unblock_message(message, state, profile)
        return

    # Показываем меню заблокированного
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✍️ Отправить заявку на разблокировку",
            callback_data="unblock:request",
        )]
    ])
    await message.answer(
        "🚫 <b>Ваш аккаунт заблокирован.</b>\n\n"
        "Вы 4 раза подряд отказались от смены или не ответили на напоминание.\n\n"
        "Если хотите продолжить работу — отправьте заявку администратору.",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ─── Кнопка «Отправить заявку» ────────────────────────────────────────────────

@router.callback_query(F.data == "unblock:request", StateFilter("*"))
async def start_unblock_request(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id
    profile = await get_profile(telegram_id)

    # Вдруг уже разблокировали пока кнопка висела
    if profile and profile.get("is_active", 1) == 1:
        await callback.message.edit_text(
            "✅ Ваш аккаунт уже активен! Напишите /start"
        )
        await callback.answer()
        return

    await state.set_state(UnblockStates.waiting_message)
    await callback.message.edit_text(
        "✍️ <b>Заявка на разблокировку</b>\n\n"
        "Напишите причину — почему не смогли выйти на смены "
        "и почему хотите продолжить работу.\n\n"
        "<i>Администратор рассмотрит заявку и свяжется с вами.</i>",
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Обработка текста заявки ──────────────────────────────────────────────────

async def _handle_unblock_message(message: Message, state: FSMContext, profile: dict):
    telegram_id = message.from_user.id
    text = message.text.strip()

    if len(text) < 10:
        await message.answer(
            "Пожалуйста, напишите подробнее — минимум несколько слов."
        )
        return

    city = profile.get("city", "не указан")
    created = await create_unblock_request(telegram_id, city, text)
    await state.clear()

    if not created:
        await message.answer(
            "⏳ Ваша заявка уже на рассмотрении.\n"
            "Пожалуйста, дождитесь ответа администратора."
        )
        return

    await message.answer(
        "✅ <b>Заявка отправлена!</b>\n\n"
        "Администратор рассмотрит её и свяжется с вами.\n"
        "Обычно это занимает до 24 часов.",
        parse_mode="HTML",
    )

    # Уведомление админу
    full_name = profile.get("full_name", "Неизвестно")
    phone     = profile.get("phone", "—")
    refused   = profile.get("refused_shifts", 0)
    ignored   = profile.get("ignored_shifts", 0)

    await message.bot.send_message(
        ADMIN_ID,
        f"🔓 <b>Новая заявка на разблокировку</b>\n\n"
        f"👤 {full_name}\n"
        f"📱 {phone}\n"
        f"🏙 Город: {city}\n"
        f"❌ Отказов: {refused} | ⏳ Игнорировал: {ignored}\n\n"
        f"💬 Сообщение:\n<i>{text}</i>\n\n"
        f"Перейди в /admin → Запросы на разблокировку",
        parse_mode="HTML",
    )