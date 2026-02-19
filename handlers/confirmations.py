"""
Блок 5 — Подтверждения участия
Вечер: основа подтверждает/отказывается, резерв получает статус "в очереди"
Утро: основа подтверждает готовность, резерв получает шанс если есть место
Блок 7 — добавлена логика счётчика отказов/игноров и блокировки
"""

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from aiogram.filters import StateFilter
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID
from database import (
    get_shift, get_user_shift_membership, update_member_status,
    get_member_count, get_first_reserve, promote_to_main,
    increment_stat, get_profile, set_morning_reminder_sent_at,
    # Блок 7
    increment_consecutive_failures,
    reset_consecutive_failures,
    check_and_block_if_needed,
)

router = Router()

BLOCK_MESSAGE = (
    "🚫 <b>Ваш аккаунт временно заблокирован.</b>\n\n"
    "Вы 4 раза подряд отказались или не ответили на смену.\n"
    "Напишите боту — мы рассмотрим заявку на разблокировку."
)


async def notify_admin(bot: Bot, text: str):
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    except Exception:
        pass


def confirm_keyboard(shift_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтверждаю", callback_data=f"confirm_shift:{shift_id}")
    builder.button(text="❌ Не смогу", callback_data=f"refuse_shift:{shift_id}")
    builder.adjust(2)
    return builder.as_markup()


def morning_confirm_keyboard(shift_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Готов, выхожу!", callback_data=f"morning_confirm:{shift_id}")
    builder.button(text="❌ Не смогу выйти", callback_data=f"refuse_shift:{shift_id}")
    builder.adjust(2)
    return builder.as_markup()


# ─── Вечернее подтверждение ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("confirm_shift:"), StateFilter("*"))
async def confirm_shift(callback: CallbackQuery, bot: Bot):
    shift_id = int(callback.data.split(":")[1])
    shift = await get_shift(shift_id)
    if not shift:
        await callback.answer("❌ Смена не найдена", show_alert=True)
        return

    member = await get_user_shift_membership(shift_id, callback.from_user.id)
    if not member:
        await callback.answer("Ты не записан на эту смену", show_alert=True)
        return
    if member["status"] == "confirmed":
        await callback.answer("Ты уже подтвердил ✅", show_alert=True)
        return
    if member["status"] in ("refused", "removed"):
        await callback.answer("Ты снят с этой смены", show_alert=True)
        return

    await update_member_status(shift_id, callback.from_user.id, "confirmed")
    await increment_stat(callback.from_user.id, "confirmed_shifts")

    # Блок 7: сброс счётчика провалов при подтверждении
    await reset_consecutive_failures(callback.from_user.id)

    slot = member["member_type"]

    if slot == "main":
        text = (
            f"✅ <b>Участие подтверждено!</b>\n\n"
            f"📅 {shift['date']}\n"
            f"📍 {shift['address']}\n\n"
            f"Утром в <b>{shift.get('morning_reminder_time', '8:00')}</b> "
            f"придёт финальное подтверждение готовности. Ждём тебя! 💪"
        )
    else:
        text = (
            f"✅ <b>Ты в резерве!</b>\n\n"
            f"📅 {shift['date']}\n"
            f"📍 {shift['address']}\n\n"
            f"Ты в очереди резерва. Если освободится место в основном составе — "
            f"тебе придёт отдельное сообщение. Будь готов! 🔔"
        )

    await callback.message.edit_text(text, parse_mode="HTML")

    profile = await get_profile(callback.from_user.id)
    name = profile.get("full_name", "Без имени") if profile else "Без имени"
    slot_label = "основной состав" if slot == "main" else "резерв"
    await notify_admin(
        bot,
        f"✅ <b>Подтверждение</b>\n"
        f"👤 {name} | {slot_label}\n"
        f"📅 {shift['date']} | {shift['city']}",
    )


# ─── Утреннее подтверждение готовности ───────────────────────────────────────

@router.callback_query(F.data.startswith("morning_confirm:"), StateFilter("*"))
async def morning_confirm(callback: CallbackQuery, bot: Bot):
    shift_id = int(callback.data.split(":")[1])
    shift = await get_shift(shift_id)
    if not shift:
        await callback.answer("❌ Смена не найдена", show_alert=True)
        return

    member = await get_user_shift_membership(shift_id, callback.from_user.id)
    if not member or member["status"] in ("refused", "removed"):
        await callback.answer("Ты снят с этой смены", show_alert=True)
        return

    await update_member_status(shift_id, callback.from_user.id, "confirmed")

    # Блок 7: сброс счётчика провалов при утреннем подтверждении
    await reset_consecutive_failures(callback.from_user.id)

    await callback.message.edit_text(
        f"💪 <b>Отлично, ждём тебя!</b>\n\n"
        f"📅 {shift['date']}\n"
        f"📍 {shift['address']}\n"
        f"💰 {shift['payment']}\n\n"
        f"Удачного рабочего дня! 🌟",
        parse_mode="HTML",
    )

    profile = await get_profile(callback.from_user.id)
    name = profile.get("full_name", "Без имени") if profile else "Без имени"
    await notify_admin(
        bot,
        f"🌅 <b>Утреннее подтверждение</b>\n"
        f"👤 {name} подтвердил готовность\n"
        f"📅 {shift['date']} | {shift['city']}",
    )


# ─── Отказ (вечер или утро) ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("refuse_shift:"), StateFilter("*"))
async def refuse_shift(callback: CallbackQuery, bot: Bot):
    shift_id = int(callback.data.split(":")[1])
    shift = await get_shift(shift_id)
    if not shift:
        await callback.answer("❌ Смена не найдена", show_alert=True)
        return

    member = await get_user_shift_membership(shift_id, callback.from_user.id)
    if not member:
        await callback.answer("Ты не записан на эту смену", show_alert=True)
        return
    if member["status"] in ("refused", "removed"):
        await callback.answer("Ты уже снят с этой смены", show_alert=True)
        return

    telegram_id = callback.from_user.id

    await update_member_status(shift_id, telegram_id, "refused")
    await increment_stat(telegram_id, "refused_shifts")

    # Блок 7: счётчик + проверка блокировки
    await increment_consecutive_failures(telegram_id)
    blocked = await check_and_block_if_needed(telegram_id)

    if blocked:
        await callback.message.edit_text(BLOCK_MESSAGE, parse_mode="HTML")
        await notify_admin(
            bot,
            f"🚫 <b>Сотрудник заблокирован</b>\n"
            f"👤 ID {telegram_id} | 4 отказа/игнора подряд\n"
            f"📅 {shift['date']} | {shift['city']}",
        )
    else:
        await callback.message.edit_text(
            "❌ <b>Ты отказался от смены.</b>\n\n"
            "⚠️ Помни: частые отказы снижают рейтинг.\n"
            "После 4 отказов подряд аккаунт станет неактивным.",
            parse_mode="HTML",
        )

    slot_type = member["member_type"]
    profile = await get_profile(telegram_id)
    name = profile.get("full_name", "Без имени") if profile else "Без имени"

    if slot_type == "main":
        await _promote_first_reserve(bot, shift, shift_id)

    await notify_admin(
        bot,
        f"❌ <b>Отказ от смены</b>\n"
        f"👤 {name} | {'основной состав' if slot_type == 'main' else 'резерв'}\n"
        f"📅 {shift['date']} | {shift['city']}",
    )


# ─── Автоснятие за игнор (вечер, 30 мин) ─────────────────────────────────────

async def auto_remove_ignored(bot: Bot, shift_id: int, telegram_id: int):
    shift = await get_shift(shift_id)
    if not shift:
        return
    member = await get_user_shift_membership(shift_id, telegram_id)
    if not member or member["status"] != "registered":
        return

    await update_member_status(shift_id, telegram_id, "removed")
    await increment_stat(telegram_id, "ignored_shifts")

    # Блок 7: счётчик + проверка блокировки
    await increment_consecutive_failures(telegram_id)
    blocked = await check_and_block_if_needed(telegram_id)

    try:
        if blocked:
            await bot.send_message(telegram_id, BLOCK_MESSAGE, parse_mode="HTML")
        else:
            await bot.send_message(
                telegram_id,
                f"⚠️ <b>Ты снят со смены за игнор напоминания</b>\n\n"
                f"📅 {shift['date']} | {shift['address']}\n\n"
                f"Ты не ответил в течение 30 минут.\n"
                f"⚠️ Это влияет на твой рейтинг. После 4 игноров подряд — аккаунт блокируется.",
                parse_mode="HTML",
            )
    except Exception:
        pass

    profile = await get_profile(telegram_id)
    name = profile.get("full_name", f"ID {telegram_id}") if profile else f"ID {telegram_id}"

    if member["member_type"] == "main":
        await _promote_first_reserve(bot, shift, shift_id)

    admin_text = (
        f"🚫 <b>Сотрудник заблокирован после игнора</b>\n"
        if blocked else
        f"⏰ <b>Автоснятие за игнор (вечер)</b>\n"
    )
    await notify_admin(
        bot,
        admin_text +
        f"👤 {name}\n"
        f"📅 {shift['date']} | {shift['city']}",
    )


# ─── Автоснятие за утренний игнор (10 мин) ───────────────────────────────────

async def auto_remove_morning_ignored(bot: Bot, shift_id: int, telegram_id: int):
    shift = await get_shift(shift_id)
    if not shift:
        return
    member = await get_user_shift_membership(shift_id, telegram_id)
    if not member or member["status"] != "registered":
        return

    await update_member_status(shift_id, telegram_id, "removed")
    await increment_stat(telegram_id, "ignored_shifts")

    # Блок 7: счётчик + проверка блокировки
    await increment_consecutive_failures(telegram_id)
    blocked = await check_and_block_if_needed(telegram_id)

    try:
        if blocked:
            await bot.send_message(telegram_id, BLOCK_MESSAGE, parse_mode="HTML")
        else:
            await bot.send_message(
                telegram_id,
                f"⚠️ <b>Ты снят со смены</b>\n\n"
                f"Не подтвердил готовность утром в течение 10 минут.\n"
                f"📅 {shift['date']} | {shift['address']}\n\n"
                f"⚠️ Это влияет на твой рейтинг.",
                parse_mode="HTML",
            )
    except Exception:
        pass

    profile = await get_profile(telegram_id)
    name = profile.get("full_name", f"ID {telegram_id}") if profile else f"ID {telegram_id}"

    await _promote_first_reserve(bot, shift, shift_id, morning=True)

    admin_text = (
        f"🚫 <b>Сотрудник заблокирован после утреннего игнора</b>\n"
        if blocked else
        f"⏰ <b>Автоснятие за игнор (утро)</b>\n"
    )
    await notify_admin(
        bot,
        admin_text +
        f"👤 {name}\n"
        f"📅 {shift['date']} | {shift['city']}",
    )


# ─── Поднять первого из резерва ───────────────────────────────────────────────

async def _promote_first_reserve(bot: Bot, shift: dict, shift_id: int, morning: bool = False):
    import logging
    logger = logging.getLogger(__name__)

    reserve = await get_first_reserve(shift_id)
    logger.info(f"[PROMOTE] shift={shift_id}, reserve найден: {reserve}")
    if not reserve:
        logger.warning(f"[PROMOTE] Резерв не найден для смены {shift_id}")
        return

    logger.info(f"[PROMOTE] Переводим {reserve['telegram_id']} в основу")

    main_count = await get_member_count(shift_id, "main")
    new_position = main_count + 1
    await promote_to_main(shift_id, reserve["telegram_id"], new_position)

    morning_time = shift.get("morning_reminder_time", "8:00")

    try:
        kb = InlineKeyboardBuilder()
        kb.button(
            text="✅ Готов!",
            callback_data=f"morning_confirm:{shift_id}" if morning else f"confirm_shift:{shift_id}",
        )
        kb.button(text="❌ Не смогу", callback_data=f"refuse_shift:{shift_id}")
        kb.adjust(2)

        if morning:
            text = (
                f"🎉 <b>Для тебя нашлось место в основном составе!</b>\n\n"
                f"📅 {shift['date']}\n"
                f"📍 {shift['address']}\n"
                f"💰 {shift['payment']}\n\n"
                f"⚡ Подтверди готовность в течение <b>10 минут</b>!\n"
                f"⚠️ Если не ответишь — место перейдёт следующему."
            )
        else:
            text = (
                f"🎉 <b>Поздравляем! Тебя переводят в основной состав!</b>\n\n"
                f"📅 {shift['date']}\n"
                f"📍 {shift['address']}\n"
                f"💰 {shift['payment']}\n\n"
                f"Утром в <b>{morning_time}</b> придёт финальное подтверждение готовности.\n"
                f"⚠️ Если не ответишь — будешь снят автоматически. Будь на связи! 📱"
            )

        await bot.send_message(
            reserve["telegram_id"], text,
            parse_mode="HTML", reply_markup=kb.as_markup(),
        )
        if morning:
            await set_morning_reminder_sent_at(shift_id, reserve["telegram_id"])

    except Exception:
        pass

    profile = await get_profile(reserve["telegram_id"])
    name = (
        profile.get("full_name", f"ID {reserve['telegram_id']}")
        if profile else f"ID {reserve['telegram_id']}"
    )
    await notify_admin(
        bot,
        f"🔄 <b>Резерв переведён в основу</b>\n"
        f"👤 {name}\n"
        f"📅 {shift['date']} | {shift['city']}"
        + (" (утренняя замена)" if morning else ""),
    )
