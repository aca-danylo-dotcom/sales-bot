"""Точка входа бота-продавца.

Сейчас подключены ИИ-клиент, админка каталога и оформление заказов. Веб-CRM
добавляется на следующих этапах.

Порядок роутеров важен и сохраняется дальше: детерминированные потоки (админка,
заказы) регистрируются РАНЬШЕ клиентского, потому что тот ловит свободный текст
целиком и иначе перехватил бы их кнопки.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

import config
from db.database import init_db
from services import agent_stats
from services.jobs import setup_scheduler
from handlers.admin import router as admin_router
from handlers.client import router as client_router
from handlers.orders import router as orders_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

dp = Dispatcher()
dp.include_router(admin_router)
# Корзина и оформление — между админкой и ИИ: кнопки «Корзина» и «Мои заказы»
# приходят обычным текстом, и клиентский роутер отдал бы их модели.
dp.include_router(orders_router)
dp.include_router(client_router)


async def main() -> None:
    # parse_mode не задаём: ответы ИИ отправляем как обычный текст, чтобы символы
    # <, >, & в них не ломали разметку Telegram.
    bot = Bot(token=config.BOT_TOKEN)
    logger.info("Бот запускается...")
    await init_db()
    logger.info("База данных готова: %s", config.DB_PATH)

    # Отчёт в дашборд агентства молчаливый: если ключа нет, о нём вообще ничего не
    # слышно. Пишем статус в лог, чтобы не гадать, почему в статистике пусто.
    if agent_stats.enabled():
        logger.info("Отчёт в Agent Stats включён: %s", agent_stats.BASE_URL)
    else:
        logger.info("Отчёт в Agent Stats выключен (нет AGENT_STATS_URL/AGENT_STATS_KEY).")

    # Планировщик поднимаем до polling: он возвращает на склад товар из
    # заказов, которые так и не оплатили.
    scheduler = setup_scheduler(bot)

    # skip старых апдейтов, накопившихся пока бот был офлайн
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
