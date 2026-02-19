import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database import init_db
from handlers import user, shift_register, admin, confirmations
from handlers import shift_report
from handlers import unblock
from scheduler import setup_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    await init_db()
    logger.info("✅ База данных инициализирована")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Подключаем роутеры (порядок важен!)
    dp.include_router(admin.router)               # Блок 4
    dp.include_router(confirmations.router)        # Блок 5
    dp.include_router(shift_report.router)         # Блок 6
    dp.include_router(user.router)                 # Блок 2
    dp.include_router(shift_register.router)       # Блок 3
    dp.include_router(unblock.router)              # Блок 7 — последним (перехватчик)

    # Запускаем планировщик
    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("🤖 Бот запущен")

    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
