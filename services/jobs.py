"""Фоновые задачи бота.

Пока одна: отмена заказов, которые так и не оплатили. Она нужна не ради
порядка в таблице, а ради склада — `queries.create_order` списывает остатки
сразу, и брошенный заказ держит товар, который никто не купит. Возврат делает
`queries.cancel_order`: смена статуса и остатки в одной транзакции.

Заказы со статусом paid_claimed задача не трогает — там клиент сказал, что
оплатил, и решение за владельцем (см. `queries.get_expired_unpaid_orders`).
"""
from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from db import queries
from services.format import money

logger = logging.getLogger(__name__)

# Раз в час: точность до минуты здесь не нужна (таймаут — сутки), а частые
# проходы по таблице заказов бессмысленны.
_CHECK_INTERVAL_MINUTES = 60


async def cancel_expired_orders(bot: Bot) -> int:
    """Отменяет просроченные неоплаченные заказы. Возвращает число отменённых."""
    orders = await queries.get_expired_unpaid_orders(config.ORDER_PAYMENT_TIMEOUT_HOURS)
    cancelled = 0
    gone: list[dict] = []

    for order in orders:
        if not await queries.cancel_order(order["id"], note="Не оплачен вовремя"):
            continue  # кто-то успел отменить или подтвердить заказ раньше
        cancelled += 1
        gone.append(order)

        # Сообщение мягкое: человек мог просто передумать, и ругаться на него
        # незачем — он вернётся. Отдельно говорим, что товар снова доступен.
        try:
            await bot.send_message(
                order["client_id"],
                f"Заказ №{order['id']} на {money(order['total'])} мы отменили — "
                f"оплата так и не пришла, а товар держать дольше не можем.\n\n"
                f"Если всё ещё нужен — напишите, соберём заказ заново 🙂",
            )
        except Exception:
            # Бот заблокирован или чат удалён. Заказ уже отменён и остатки
            # вернулись — это важнее, чем доставленное уведомление.
            logger.warning("Не удалось сообщить клиенту %s об отмене заказа %s",
                           order["client_id"], order["id"])

    if gone:
        await _notify_admin_expired(bot, gone)
    if cancelled:
        logger.info("Отменено неоплаченных заказов: %s", cancelled)
    return cancelled


async def _notify_admin_expired(bot: Bot, orders: list[dict]) -> None:
    """Сводка владельцу об автоотменах.

    Нужна, потому что о каждом заказе владелец уже получил пуш при оформлении:
    без этого сообщения он продолжал бы ждать оплату по заказу, которого нет.
    Одним сообщением, а не по штуке на заказ — ночью их может накопиться.
    """
    lines = ["🕒 <b>Автоотмена неоплаченных заказов</b>", ""]
    for order in orders:
        lines.append(f"№{order['id']} — {money(order['total'])}, товар вернулся на склад")
    try:
        await bot.send_message(config.ADMIN_ID, "\n".join(lines), parse_mode="HTML")
    except Exception:
        logger.warning("Не удалось сообщить владельцу об автоотмене заказов")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Запускает планировщик фоновых задач.

    Время киевское — как и все метки в базе (config.now_str), иначе задача
    считала бы «сутки» не от того момента, что записан в created_at.
    """
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
    scheduler.add_job(
        cancel_expired_orders,
        "interval",
        minutes=_CHECK_INTERVAL_MINUTES,
        args=(bot,),
        id="cancel_expired_orders",
        # Бот мог лежать несколько часов: пропущенные запуски сливаем в один,
        # а не выполняем очередь подряд.
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    logger.info(
        "Планировщик запущен: отмена неоплаченных заказов старше %s ч, проверка раз в %s мин",
        config.ORDER_PAYMENT_TIMEOUT_HOURS,
        _CHECK_INTERVAL_MINUTES,
    )
    return scheduler
