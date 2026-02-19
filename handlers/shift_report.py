from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from config import ADMIN_ID
from database import (
    save_shift_result,
    get_shift_result,
    get_shift_results_full,
    get_active_shift_by_id,
)
from utils.states import ShiftReportStates

router = Router()


# ─── Вспомогательная функция: отправить итог админу ───────────────────────────

async def send_admin_summary(bot, shift_id: int, shift_info: dict):
    worked, not_worked, no_response = await get_shift_results_full(shift_id)

    lines = [f"📊 <b>Текущий итог смены</b>\n📍 {shift_info['city']} | {shift_info['date']}\n"]

    if worked:
        lines.append(f"✅ <b>Отработали ({len(worked)}):</b>")
        for r in worked:
            tag = "🔵осн." if r['member_type'] == 'main' else "🟡рез."
            lines.append(f"  {tag} {r['full_name']} | {r['phone']}")

    if not_worked:
        lines.append(f"\n❌ <b>Не вышли ({len(not_worked)}):</b>")
        for r in not_worked:
            tag = "🔵осн." if r['member_type'] == 'main' else "🟡рез."
            reason = r['decline_reason'] or "причина не указана"
            lines.append(f"  {tag} {r['full_name']} | {r['phone']}\n    ↳ {reason}")

    if no_response:
        lines.append(f"\n⏳ <b>Не ответили ({len(no_response)}):</b>")
        for r in no_response:
            tag = "🔵осн." if r['member_type'] == 'main' else "🟡рез."
            lines.append(f"  {tag} {r['full_name']} | {r['phone']}")

    await bot.send_message(ADMIN_ID, "\n".join(lines), parse_mode="HTML")


# ─── Нажал «Отработал ✅» ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("report_worked_"), StateFilter("*"))
async def handle_report_worked(callback: CallbackQuery, state: FSMContext):
    shift_id = int(callback.data.split("_")[2])
    telegram_id = callback.from_user.id

    # Защита от повторного ответа
    existing = await get_shift_result(shift_id, telegram_id)
    if existing:
        await callback.answer("Ты уже отправил отчёт по этой смене.", show_alert=True)
        return

    shift = await get_active_shift_by_id(shift_id)
    if not shift:
        await callback.answer("Смена не найдена.", show_alert=True)
        return

    await save_shift_result(shift_id, telegram_id, worked=True)

    await callback.message.edit_text(
        "✅ <b>Отлично! Отмечено, что ты отработал смену.</b>\n\nСпасибо за работу! 💪",
        parse_mode="HTML"
    )
    await callback.answer()

    # Уведомляем админа
    await send_admin_summary(callback.bot, shift_id, shift)


# ─── Нажал «Не смог выйти ❌» ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("report_failed_"), StateFilter("*"))
async def handle_report_failed(callback: CallbackQuery, state: FSMContext):
    shift_id = int(callback.data.split("_")[2])
    telegram_id = callback.from_user.id

    # Защита от повторного ответа
    existing = await get_shift_result(shift_id, telegram_id)
    if existing:
        await callback.answer("Ты уже отправил отчёт по этой смене.", show_alert=True)
        return

    shift = await get_active_shift_by_id(shift_id)
    if not shift:
        await callback.answer("Смена не найдена.", show_alert=True)
        return

    # Сохраняем shift_id в state, переходим в FSM
    await state.set_state(ShiftReportStates.waiting_decline_reason)
    await state.update_data(report_shift_id=shift_id)

    await callback.message.edit_text(
        "❌ <b>Жаль, что не получилось.</b>\n\n"
        "Напиши, пожалуйста, причину — это важно для учёта.\n\n"
        "<i>Например: заболел, семейные обстоятельства, транспорт и т.д.</i>",
        parse_mode="HTML"
    )
    await callback.answer()


# ─── Получили причину текстом ──────────────────────────────────────────────────

@router.message(ShiftReportStates.waiting_decline_reason)
async def handle_decline_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    shift_id = data.get("report_shift_id")

    if not shift_id:
        await state.clear()
        await message.answer("Что-то пошло не так. Попробуй снова.")
        return

    reason = message.text.strip()

    # Валидация — не принимаем слишком короткие ответы
    if len(reason) < 3:
        await message.answer("Пожалуйста, напиши причину чуть подробнее.")
        return

    telegram_id = message.from_user.id
    shift = await get_active_shift_by_id(shift_id)

    await save_shift_result(shift_id, telegram_id, worked=False, decline_reason=reason)
    await state.clear()

    await message.answer(
        "📝 <b>Причина записана.</b>\n\nНадеемся, что в следующий раз всё получится! 🤝",
        parse_mode="HTML"
    )

    # Уведомляем админа
    if shift:
        await send_admin_summary(message.bot, shift_id, shift)


# ─── Команда админа: запросить итоговый отчёт ─────────────────────────────────

@router.callback_query(F.data.startswith("admin_report_summary_"), StateFilter("*"))
async def admin_request_summary(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    shift_id = int(callback.data.split("_")[3])
    shift = await get_active_shift_by_id(shift_id)

    if not shift:
        await callback.answer("Смена не найдена.", show_alert=True)
        return

    await send_admin_summary(callback.bot, shift_id, shift)
    await callback.answer("Итог отправлен 👆")
    