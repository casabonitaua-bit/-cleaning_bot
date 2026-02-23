"""
Блок 4 — Админ-панель
• /admin — главное меню
• Создание объявления о смене (пошаговый FSM)
• Публикация смены (рассылка всем сотрудникам города)
• Статус текущей смены (основа / резерв)
• Панель-шпаргалка команд
• Просмотр запросов на разблокировку (просмотр + ручная разблокировка)
"""

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID, CITIES
from aiogram.types import BufferedInputFile                          
from utils.excel_export import excel_city_base, excel_shift_report
from database import (
    create_shift,
    get_shift,
    get_active_shift_by_city,
    get_active_shift_by_id,
    get_shift_members,
    get_shift_members_for_report,
    get_member_count,
    get_users_by_city,
    update_shift_status,
    upsert_profile,
    # Блок 7
    get_pending_unblock_requests,
    resolve_unblock_request,
    unblock_user,
    get_db,
)
from utils.states import AdminStates

router = Router()


# ─── Фильтр: только для админа ───────────────────────────────────────────────

def is_admin(telegram_id: int) -> bool:
    return telegram_id == ADMIN_ID


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def admin_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать смену", callback_data="admin:create_shift")
    builder.button(text="📋 Статус смены", callback_data="admin:shift_status")
    builder.button(text="🔓 Запросы на разблокировку", callback_data="admin:unblock_requests")
    builder.button(text="📑 Шпаргалка команд", callback_data="admin:cheatsheet")
    builder.button(text="📊 База города", callback_data="excel_choose_city")
    builder.button(text="📋 Отчёт по смене", callback_data="excel_choose_shift")
    builder.adjust(1)
    return builder.as_markup()


def city_keyboard_admin():
    builder = InlineKeyboardBuilder()
    for city in CITIES:
        builder.button(text=city, callback_data=f"admin_city:{city}")
    builder.adjust(3)
    builder.row()
    builder.button(text="❌ Отмена", callback_data="admin:cancel")
    return builder.as_markup()


def confirm_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Опубликовать", callback_data="admin:publish_shift")
    builder.button(text="✏️ Изменить", callback_data="admin:edit_shift")
    builder.button(text="❌ Отмена", callback_data="admin:cancel")
    builder.adjust(1)
    return builder.as_markup()


def shift_status_city_keyboard():
    builder = InlineKeyboardBuilder()
    for city in CITIES:
        builder.button(text=city, callback_data=f"status_city:{city}")
    builder.adjust(3)
    builder.row()
    builder.button(text="◀️ Назад", callback_data="admin:back_to_main")
    return builder.as_markup()


def shift_manage_keyboard(shift_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="📨 Отправить напоминание", callback_data=f"admin:send_reminder:{shift_id}")
    builder.button(text="🏁 Завершить смену", callback_data=f"admin:finish_shift:{shift_id}")
    builder.button(text="◀️ Назад", callback_data="admin:back_to_main")
    builder.adjust(1)
    return builder.as_markup()


# ─── Главное меню ─────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "🛠 <b>Админ-панель</b>\n\nВыбери действие:",
        parse_mode="HTML",
        reply_markup=admin_main_keyboard(),
    )


@router.callback_query(F.data == "admin:back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text(
        "🛠 <b>Админ-панель</b>\n\nВыбери действие:",
        parse_mode="HTML",
        reply_markup=admin_main_keyboard(),
    )


@router.callback_query(F.data == "admin:cancel")
async def admin_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.message.edit_text(
        "❌ Действие отменено.\n\n🛠 <b>Админ-панель</b>",
        parse_mode="HTML",
        reply_markup=admin_main_keyboard(),
    )


# ─── Шпаргалка команд ────────────────────────────────────────────────────────

CHEATSHEET = """
📋 <b>Шпаргалка команд</b>

/admin — открыть панель управления

<b>Создание смены:</b>
➕ Создать смену → выбери город → заполни данные → опубликуй

<b>После публикации:</b>
• Рассылка автоматически уходит всем в городе
• В «Статус смены» видишь кто записался
• Кнопка «Отправить напоминание» — вручную шлёт запрос подтверждения
• Кнопка «Завершить смену» — рассылает всем кто на смене

<b>Уведомления автоматические:</b>
• Новая запись → тебе сообщение
• Основной состав заполнен → уведомление
• Резерв заполнен → уведомление
• Вечернее напоминание → по расписанию (Блок 5)

<b>Разблокировка:</b>
• Сотрудник пишет боту с просьбой разблокировки
• Ты видишь запрос в панели и принимаешь решение
"""


@router.callback_query(F.data == "admin:cheatsheet")
async def show_cheatsheet(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data="admin:back_to_main")
    await callback.message.edit_text(
        CHEATSHEET, parse_mode="HTML", reply_markup=builder.as_markup()
    )


# ─── Создание смены (FSM) ─────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:create_shift")
async def create_shift_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AdminStates.choosing_city)
    await callback.message.edit_text(
        "🏙 <b>Создание смены</b>\n\nВыбери город:",
        parse_mode="HTML",
        reply_markup=city_keyboard_admin(),
    )


@router.callback_query(AdminStates.choosing_city, F.data.startswith("admin_city:"))
async def admin_choose_city(callback: CallbackQuery, state: FSMContext):
    city = callback.data.split(":")[1]
    await state.update_data(city=city)
    await state.set_state(AdminStates.entering_date)
    await callback.message.edit_text(
        f"🏙 Город: <b>{city}</b>\n\n📅 Введи дату смены (например: <code>25.07.2025</code> или <code>Суббота 26 июля</code>):",
        parse_mode="HTML",
    )


@router.message(AdminStates.entering_date)
async def admin_enter_date(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(date=message.text.strip())
    await state.set_state(AdminStates.entering_address)
    await message.answer("📍 Введи адрес объекта:")


@router.message(AdminStates.entering_address)
async def admin_enter_address(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(address=message.text.strip())
    await state.set_state(AdminStates.entering_payment)
    await message.answer(
        "💰 Введи размер оплаты (например: <code>2500 руб / смена</code>):",
        parse_mode="HTML",
    )


@router.message(AdminStates.entering_payment)
async def admin_enter_payment(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(payment=message.text.strip())
    await state.set_state(AdminStates.entering_conditions)
    await message.answer(
        "📝 Введи условия работы (форма одежды, что взять с собой и т.д.)\n"
        "Или напиши <code>-</code> если условий нет:",
        parse_mode="HTML",
    )


@router.message(AdminStates.entering_conditions)
async def admin_enter_conditions(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    conditions = message.text.strip()
    if conditions == "-":
        conditions = ""
    await state.update_data(conditions=conditions)
    await state.set_state(AdminStates.entering_main_slots)
    await message.answer("👥 Сколько мест в <b>основном составе</b>?", parse_mode="HTML")


@router.message(AdminStates.entering_main_slots)
async def admin_enter_main_slots(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("Введи корректное число мест (целое > 0):")
        return
    await state.update_data(main_slots=int(message.text))
    await state.set_state(AdminStates.entering_reserve_slots)
    await message.answer("🔄 Сколько мест в <b>резерве</b>?", parse_mode="HTML")


@router.message(AdminStates.entering_reserve_slots)
async def admin_enter_reserve_slots(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text.isdigit() or int(message.text) < 0:
        await message.answer("Введи корректное число мест (0 или больше):")
        return
    await state.update_data(reserve_slots=int(message.text))
    await state.set_state(AdminStates.entering_reminder_time)
    await message.answer(
        "⏰ В какое время вечером отправить напоминание о подтверждении?\n"
        "Формат: <code>19:00</code>",
        parse_mode="HTML",
    )


@router.message(AdminStates.entering_reminder_time)
async def admin_enter_reminder_time(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    time_str = message.text.strip()
    parts = time_str.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await message.answer(
            "Введи время в формате <code>HH:MM</code>, например <code>19:00</code>:",
            parse_mode="HTML",
        )
        return
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        await message.answer(
            "Некорректное время. Введи в формате <code>HH:MM</code>:",
            parse_mode="HTML",
        )
        return

    await state.update_data(reminder_time=time_str)
    await state.set_state(AdminStates.entering_morning_reminder_time)
    await message.answer(
        "🌅 Введи время <b>утреннего</b> напоминания о готовности (в день смены)\n"
        "Формат: <code>08:30</code>",
        parse_mode="HTML",
    )


@router.message(AdminStates.entering_morning_reminder_time)
async def admin_enter_morning_reminder_time(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    time_str = message.text.strip()
    parts = time_str.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await message.answer(
            "Введи время в формате <code>HH:MM</code>, например <code>08:30</code>:",
            parse_mode="HTML",
        )
        return
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        await message.answer(
            "Некорректное время. Введи в формате <code>HH:MM</code>:",
            parse_mode="HTML",
        )
        return

    await state.update_data(morning_reminder_time=time_str)
    await state.set_state(AdminStates.confirming)

    data = await state.get_data()
    preview = build_shift_preview(data)

    await message.answer(
        f"👀 <b>Предпросмотр объявления</b>\n\n{preview}\n\nПубликовать?",
        parse_mode="HTML",
        reply_markup=confirm_keyboard(),
    )


def build_shift_preview(data: dict) -> str:
    text = (
        f"🏙 Город: <b>{data['city']}</b>\n"
        f"📅 Дата: {data['date']}\n"
        f"📍 Адрес: {data['address']}\n"
        f"💰 Оплата: {data['payment']}\n"
    )
    if data.get("conditions"):
        text += f"📝 Условия: {data['conditions']}\n"
    text += (
        f"👥 Основной состав: {data['main_slots']} мест\n"
        f"🔄 Резерв: {data['reserve_slots']} мест\n"
        f"⏰ Вечернее напоминание: {data['reminder_time']}\n"
        f"🌅 Утреннее напоминание: {data.get('morning_reminder_time', 'не задано')}"
    )
    return text


# ─── Редактировать (заново) ───────────────────────────────────────────────────

@router.callback_query(AdminStates.confirming, F.data == "admin:edit_shift")
async def edit_shift(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.choosing_city)
    await callback.message.edit_text(
        "🔄 Начнём сначала. Выбери город:",
        reply_markup=city_keyboard_admin(),
    )


# ─── Публикация ───────────────────────────────────────────────────────────────

@router.callback_query(AdminStates.confirming, F.data == "admin:publish_shift")
async def publish_shift(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(callback.from_user.id):
        return

    data = await state.get_data()
    await state.clear()

    shift_id = await create_shift(
        city=data["city"],
        date=data["date"],
        address=data["address"],
        payment=data["payment"],
        conditions=data.get("conditions", ""),
        main_slots=data["main_slots"],
        reserve_slots=data["reserve_slots"],
        reminder_time=data["reminder_time"],
        morning_reminder_time=data.get("morning_reminder_time", "08:00"),
    )

    announcement = build_announcement(data, shift_id)
    users = await get_users_by_city(data["city"])
    sent = 0
    failed = 0

    for user in users:
        try:
            from aiogram.utils.keyboard import InlineKeyboardBuilder as IKB
            kb = IKB()
            kb.button(text="✅ Записаться", callback_data=f"register_shift:{shift_id}")
            await bot.send_message(
                user["telegram_id"],
                announcement,
                parse_mode="HTML",
                reply_markup=kb.as_markup(),
            )
            sent += 1
        except Exception:
            failed += 1

    await callback.message.edit_text(
        f"✅ <b>Смена опубликована!</b>\n\n"
        f"{build_shift_preview(data)}\n\n"
        f"📨 Рассылка: отправлено {sent}, не доставлено {failed}\n"
        f"🆔 ID смены: <code>{shift_id}</code>",
        parse_mode="HTML",
        reply_markup=admin_main_keyboard(),
    )


def build_announcement(data: dict, shift_id: int) -> str:
    text = (
        f"📢 <b>Новая смена!</b>\n\n"
        f"🏙 Город: <b>{data['city']}</b>\n"
        f"📅 Дата: {data['date']}\n"
        f"📍 Адрес: {data['address']}\n"
        f"💰 Оплата: {data['payment']}\n"
    )
    if data.get("conditions"):
        text += f"📝 Условия: {data['conditions']}\n"
    text += (
        f"\n👥 Мест в основном составе: {data['main_slots']}\n"
        f"🔄 Мест в резерве: {data['reserve_slots']}\n\n"
        f"⏰ Вечернее напоминание: {data['reminder_time']}\n"
        f"🌅 Утреннее напоминание: {data.get('morning_reminder_time', '08:00')}\n\n"
        f"👇 Нажми кнопку ниже, чтобы записаться!"
    )
    return text


# ─── Статус смены ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin:shift_status")
async def shift_status_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.edit_text(
        "📊 <b>Статус смены</b>\n\nВыбери город:",
        parse_mode="HTML",
        reply_markup=shift_status_city_keyboard(),
    )


@router.callback_query(F.data.startswith("status_city:"))
async def shift_status_city(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    city = callback.data.split(":")[1]
    shift = await get_active_shift_by_city(city)

    if not shift:
        builder = InlineKeyboardBuilder()
        builder.button(text="◀️ Назад", callback_data="admin:shift_status")
        await callback.message.edit_text(
            f"😔 В городе <b>{city}</b> нет активных смен.",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return

    members = await get_shift_members(shift["id"])
    main_members = [
        m for m in members
        if m["member_type"] == "main" and m["status"] not in ("refused", "removed")
    ]
    reserve_members = [
        m for m in members
        if m["member_type"] == "reserve" and m["status"] not in ("refused", "removed")
    ]

    text = (
        f"📊 <b>Статус смены — {city}</b>\n\n"
        f"📅 {shift['date']} | 📍 {shift['address']}\n"
        f"💰 {shift['payment']}\n\n"
        f"👥 <b>Основной состав</b> ({len(main_members)}/{shift['main_slots']}):\n"
    )

    if main_members:
        for i, m in enumerate(main_members, 1):
            status_icon = _status_icon(m["status"])
            name = m.get("full_name") or f"ID {m['telegram_id']}"
            text += f"  {i}. {status_icon} {name}"
            if m.get("phone"):
                text += f" | {m['phone']}"
            text += "\n"
    else:
        text += "  — пусто\n"

    text += f"\n🔄 <b>Резерв</b> ({len(reserve_members)}/{shift['reserve_slots']}):\n"

    if reserve_members:
        for i, m in enumerate(reserve_members, 1):
            status_icon = _status_icon(m["status"])
            name = m.get("full_name") or f"ID {m['telegram_id']}"
            text += f"  {i}. {status_icon} {name}"
            if m.get("phone"):
                text += f" | {m['phone']}"
            text += "\n"
    else:
        text += "  — пусто\n"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=shift_manage_keyboard(shift["id"]),
    )


def _status_icon(status: str) -> str:
    return {
        "registered": "⏳",
        "confirmed":  "✅",
        "refused":    "❌",
        "removed":    "🚫",
        "worked":     "🏆",
    }.get(status, "❓")


# ─── Отправить напоминание вручную ───────────────────────────────────────────

@router.callback_query(F.data.startswith("admin:send_reminder:"))
async def send_reminder_manual(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    shift_id = int(callback.data.split(":")[-1])
    shift = await get_shift(shift_id)
    if not shift:
        await callback.answer("Смена не найдена", show_alert=True)
        return

    members = await get_shift_members(shift_id)
    active = [m for m in members if m["status"] == "registered"]

    if not active:
        await callback.answer(
            "Нет участников для напоминания (все уже подтвердили или отказались)",
            show_alert=True,
        )
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder as IKB
    sent = 0
    for m in active:
        try:
            kb = IKB()
            kb.button(text="✅ Подтверждаю", callback_data=f"confirm_shift:{shift_id}")
            kb.button(text="❌ Не смогу",    callback_data=f"refuse_shift:{shift_id}")
            kb.adjust(2)
            await bot.send_message(
                m["telegram_id"],
                f"⏰ <b>Напоминание о смене</b>\n\n"
                f"📅 {shift['date']}\n"
                f"📍 {shift['address']}\n\n"
                f"Пожалуйста, подтверди своё участие:",
                parse_mode="HTML",
                reply_markup=kb.as_markup(),
            )
            sent += 1
        except Exception:
            pass

    await callback.answer(f"Напоминание отправлено {sent} участникам", show_alert=True)


# ─── Завершить смену — рассылка форм отчёта ───────────────────────────────────

@router.callback_query(F.data.startswith("admin:finish_shift:"), StateFilter("*"))
async def finish_shift(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    shift_id = int(callback.data.split(":")[2])
    shift = await get_active_shift_by_id(shift_id)

    if not shift:
        await callback.answer("Смена не найдена.", show_alert=True)
        return

    await update_shift_status(shift_id, "completed")

    members = await get_shift_members_for_report(shift_id)

    if not members:
        await callback.message.answer("⚠️ Нет участников для отчёта.")
        await callback.answer()
        return

    report_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Отработал",
                callback_data=f"report_worked_{shift_id}",
            ),
            InlineKeyboardButton(
                text="❌ Не смог выйти",
                callback_data=f"report_failed_{shift_id}",
            ),
        ]
    ])

    sent_count = 0
    for member in members:
        try:
            await callback.bot.send_message(
                member["telegram_id"],
                f"📋 <b>Смена завершена!</b>\n\n"
                f"📍 {shift['city']} | {shift['date']}\n\n"
                f"Пожалуйста, отметь результат своего участия:",
                reply_markup=report_kb,
                parse_mode="HTML",
            )
            sent_count += 1
        except Exception:
            pass

    summary_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📊 Итоговый отчёт",
            callback_data=f"admin_report_summary_{shift_id}",
        )]
    ])

    await callback.message.answer(
        f"✅ <b>Смена завершена!</b>\n\n"
        f"Форма отчёта отправлена <b>{sent_count}</b> участникам.\n"
        f"Когда все ответят — нажми кнопку ниже.",
        reply_markup=summary_kb,
        parse_mode="HTML",
    )
    await callback.answer()


# ─── Запросы на разблокировку — Блок 7 ───────────────────────────────────────

@router.callback_query(F.data == "admin:unblock_requests")
async def show_unblock_requests(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    requests = await get_pending_unblock_requests()
    builder = InlineKeyboardBuilder()

    if not requests:
        builder.button(text="◀️ Назад", callback_data="admin:back_to_main")
        await callback.message.edit_text(
            "🔓 <b>Запросы на разблокировку</b>\n\n✅ Новых запросов нет.",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        return

    text = f"🔓 <b>Запросы на разблокировку</b> ({len(requests)}):\n\n"

    for i, r in enumerate(requests, 1):
        name    = r.get("full_name") or f"ID {r['telegram_id']}"
        phone   = r.get("phone") or "—"
        city    = r.get("city") or "—"
        refused = r.get("refused_shifts", 0)
        ignored = r.get("ignored_shifts", 0)
        msg     = r.get("message") or "—"

        text += (
            f"{i}. 👤 <b>{name}</b> | 📱 {phone}\n"
            f"   🏙 {city} | ❌{refused} ⏳{ignored}\n"
            f"   💬 <i>{msg[:100]}{'...' if len(msg) > 100 else ''}</i>\n\n"
        )
        builder.button(
            text=f"✅ {name}",
            callback_data=f"unblock:approve:{r['id']}:{r['telegram_id']}",
        )
        builder.button(
            text="❌ Отказать",
            callback_data=f"unblock:deny:{r['id']}:{r['telegram_id']}",
        )

    builder.adjust(2)
    builder.row()
    builder.button(text="◀️ Назад", callback_data="admin:back_to_main")

    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("unblock:approve:"))
async def approve_unblock(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    parts       = callback.data.split(":")
    request_id  = int(parts[2])
    telegram_id = int(parts[3])

    await unblock_user(telegram_id)
    await resolve_unblock_request(request_id, "approved")

    try:
        await bot.send_message(
            telegram_id,
            "✅ <b>Ваша заявка одобрена!</b>\n\n"
            "Аккаунт разблокирован. Теперь вы снова будете получать "
            "объявления о сменах.\n\nНапишите /start чтобы продолжить.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await callback.answer("✅ Пользователь разблокирован", show_alert=True)
    await show_unblock_requests(callback)


@router.callback_query(F.data.startswith("unblock:deny:"))
async def deny_unblock(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    parts       = callback.data.split(":")
    request_id  = int(parts[2])
    telegram_id = int(parts[3])

    await resolve_unblock_request(request_id, "denied")

    try:
        await bot.send_message(
            telegram_id,
            "❌ <b>Заявка на разблокировку отклонена.</b>\n\n"
            "Если вы считаете это ошибкой — напишите нам повторно.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await callback.answer("❌ Заявка отклонена", show_alert=True)
    await show_unblock_requests(callback)


# ——— Excel выгрузка ———

@router.callback_query(F.data == "excel_choose_city")
async def excel_choose_city(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    builder = InlineKeyboardBuilder()
    for city in CITIES:
        builder.button(text=city, callback_data=f"excel_city:{city}")
    builder.adjust(3)
    await callback.message.answer("Выберите город:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("excel_city:"))
async def excel_send_city_base(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    city = callback.data.split(":")[1]
    await callback.answer("Формирую файл...")
    buf = await excel_city_base(city)
    file = BufferedInputFile(buf.read(), filename=f"base_{city}.xlsx")
    await callback.message.answer_document(file, caption=f"📊 База — {city}")


@router.callback_query(F.data == "excel_choose_shift")
async def excel_choose_shift(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    async with get_db() as db:
        shifts = await db.fetch(
            """
            SELECT id, city, date, address
            FROM shifts
            WHERE status != 'draft'
            ORDER BY created_at DESC
            LIMIT 20
            """
        )

    if not shifts:
        await callback.answer("Нет доступных смен", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for s in shifts:
        # asyncpg.Record -> обращение по ключам безопаснее, чем по индексам
        label = f"{s['city']} | {s['date']} | #{s['id']}"
        builder.button(text=label, callback_data=f"excel_shift:{s['id']}")
    builder.adjust(1)

    await callback.message.answer("Выберите смену:", reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("excel_shift:"))
async def excel_send_shift_report(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    shift_id = int(callback.data.split(":")[1])
    await callback.answer("Формирую файл...")
    buf = await excel_shift_report(shift_id)
    file = BufferedInputFile(buf.read(), filename=f"shift_{shift_id}.xlsx")
    await callback.message.answer_document(file, caption=f"📋 Отчёт по смене #{shift_id}")