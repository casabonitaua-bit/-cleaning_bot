import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import (
    get_all_active_shifts, get_shift_members,
    set_reminder_sent_at, set_morning_reminder_sent_at,
    get_members_to_ignore_check, get_members_to_morning_ignore_check,
)
from handlers.confirmations import auto_remove_ignored, auto_remove_morning_ignored
from city_timezones import get_city_tz

logger = logging.getLogger(__name__)


def _local_hhmm(city: str) -> str:
    return datetime.now(get_city_tz(city)).strftime("%H:%M")


def _local_hour(city: str) -> int:
    return datetime.now(get_city_tz(city)).hour


def _local_date(city: str) -> str:
    return datetime.now(get_city_tz(city)).strftime("%d.%m.%Y")


async def job_send_evening_reminders(bot: Bot):
    """Вечернее напоминание — основе с кнопками, резерву просто инфо."""
    try:
        shifts = await get_all_active_shifts()
        for shift in shifts:
            if not shift.get("reminder_time"):
                continue
            if shift["reminder_time"] != _local_hhmm(shift["city"]):
                continue

            members = await get_shift_members(shift["id"])
            morning_time = shift.get("morning_reminder_time", "8:00")

            for member in members:
                if member["status"] not in ("registered", "confirmed"):
                    continue
                if member.get("reminder_sent_at"):
                    continue  # уже отправляли

                try:
                    if member["member_type"] == "main":
                        kb = InlineKeyboardBuilder()
                        kb.button(text="✅ Подтверждаю", callback_data=f"confirm_shift:{shift['id']}")
                        kb.button(text="❌ Не смогу", callback_data=f"refuse_shift:{shift['id']}")
                        kb.adjust(2)
                        text = (
                            f"⏰ <b>Напоминание о смене!</b>\n\n"
                            f"📅 {shift['date']}\n"
                            f"📍 {shift['address']}\n"
                            f"💰 {shift['payment']}\n\n"
                            f"Подтверди участие.\n"
                            f"⚠️ Если не ответишь в течение <b>30 минут</b> — будешь снят автоматически!"
                        )
                        await bot.send_message(
                            member["telegram_id"], text,
                            parse_mode="HTML", reply_markup=kb.as_markup(),
                        )
                        # Ставим время только основе — для отсчёта 30 мин игнора
                        await set_reminder_sent_at(shift["id"], member["telegram_id"])
                        logger.info(f"Вечернее напоминание (основа) → {member['telegram_id']}")

                    else:
                        # Резерв — только информация, reminder_sent_at НЕ ставим!
                        text = (
                            f"🔔 <b>Информация о смене</b>\n\n"
                            f"📅 {shift['date']}\n"
                            f"📍 {shift['address']}\n"
                            f"💰 {shift['payment']}\n\n"
                            f"Ты в очереди резерва. Основной состав сейчас подтверждает участие.\n\n"
                            f"Если кто-то откажется — тебе придёт сообщение о переводе в основу.\n"
                            f"Утром в <b>{morning_time}</b> придёт финальная информация.\n"
                            f"📱 Будь на связи!"
                        )
                        await bot.send_message(
                            member["telegram_id"], text, parse_mode="HTML",
                        )
                        logger.info(f"Вечернее инфо (резерв) → {member['telegram_id']}")
                        # set_reminder_sent_at для резерва НЕ вызываем!

                except Exception as e:
                    logger.warning(f"Вечернее напоминание {member['telegram_id']}: {e}")

    except Exception as e:
        logger.error(f"job_send_evening_reminders: {e}")


async def job_check_evening_ignores(bot: Bot):
    """Снимаем ТОЛЬКО основу кто не ответил 30+ мин на вечернее напоминание."""
    try:
        shifts = await get_all_active_shifts()
        for shift in shifts:
            ignored = await get_members_to_ignore_check(shift["id"])
            for member in ignored:
                logger.info(f"Игнор (вечер): {member['telegram_id']} смена {shift['id']}")
                await auto_remove_ignored(bot, shift["id"], member["telegram_id"])
    except Exception as e:
        logger.error(f"job_check_evening_ignores: {e}")


async def job_send_morning_reminders(bot: Bot):
    """Утреннее подтверждение готовности — основе."""
    try:
        shifts = await get_all_active_shifts()
        for shift in shifts:
            if not shift.get("morning_reminder_time"):
                continue
            if shift["morning_reminder_time"] != _local_hhmm(shift["city"]):
                continue

            today = _local_date(shift["city"])
            if today not in shift["date"]:
                continue

            members = await get_shift_members(shift["id"])

            for member in members:
                if member.get("morning_reminder_sent_at"):
                    continue

                try:
                    if member["member_type"] == "main" and member["status"] == "confirmed":
                        kb = InlineKeyboardBuilder()
                        kb.button(text="✅ Готов, выхожу!", callback_data=f"morning_confirm:{shift['id']}")
                        kb.button(text="❌ Не смогу выйти", callback_data=f"refuse_shift:{shift['id']}")
                        kb.adjust(2)
                        text = (
                            f"🌅 <b>Доброе утро! Сегодня твоя смена</b>\n\n"
                            f"📅 {shift['date']}\n"
                            f"📍 {shift['address']}\n"
                            f"💰 {shift['payment']}\n\n"
                            f"Подтверди что выходишь!\n"
                            f"⚠️ Если не ответишь в течение <b>10 минут</b> — будешь снят."
                        )
                        await bot.send_message(
                            member["telegram_id"], text,
                            parse_mode="HTML", reply_markup=kb.as_markup(),
                        )
                        await set_morning_reminder_sent_at(shift["id"], member["telegram_id"])
                        logger.info(f"Утреннее напоминание (основа) → {member['telegram_id']}")

                    elif member["member_type"] == "reserve" and member["status"] == "confirmed":
                        # Считаем сколько основы подтверждено
                        main_confirmed = sum(
                            1 for m in members
                            if m["member_type"] == "main"
                            and m["status"] not in ("refused", "removed")
                        )
                        if main_confirmed >= shift["main_slots"]:
                            # Основа заполнена — резерву просто инфо
                            text = (
                                f"🌅 <b>Доброе утро!</b>\n\n"
                                f"Сегодня смена в {shift['city']}.\n"
                                f"📅 {shift['date']} | 📍 {shift['address']}\n\n"
                                f"Основной состав заполнен, ты в резерве.\n"
                                f"Если кто-то не выйдет — тебе придёт сообщение. Будь на связи! 📱"
                            )
                            await bot.send_message(
                                member["telegram_id"], text, parse_mode="HTML",
                            )

                except Exception as e:
                    logger.warning(f"Утреннее напоминание {member['telegram_id']}: {e}")

    except Exception as e:
        logger.error(f"job_send_morning_reminders: {e}")


async def job_check_morning_ignores(bot: Bot):
    """Снимаем тех кто не ответил 10+ мин на утреннее напоминание."""
    try:
        shifts = await get_all_active_shifts()
        for shift in shifts:
            ignored = await get_members_to_morning_ignore_check(shift["id"])
            for member in ignored:
                logger.info(f"Игнор (утро): {member['telegram_id']} смена {shift['id']}")
                await auto_remove_morning_ignored(bot, shift["id"], member["telegram_id"])
    except Exception as e:
        logger.error(f"job_check_morning_ignores: {e}")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(job_send_evening_reminders, "cron", minute="*", kwargs={"bot": bot}, id="evening_reminders", replace_existing=True)
    scheduler.add_job(job_check_evening_ignores,  "cron", minute="*", kwargs={"bot": bot}, id="evening_ignores",  replace_existing=True)
    scheduler.add_job(job_send_morning_reminders, "cron", minute="*", kwargs={"bot": bot}, id="morning_reminders", replace_existing=True)
    scheduler.add_job(job_check_morning_ignores,  "cron", minute="*", kwargs={"bot": bot}, id="morning_ignores",  replace_existing=True)
    return scheduler
