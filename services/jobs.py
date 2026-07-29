"""Фоновые задачи бота.

Их три.

1. Отмена заказов, которые так и не оплатили. Нужна не ради порядка в таблице,
   а ради склада — `queries.create_order` списывает остатки сразу, и брошенный
   заказ держит товар, который никто не купит. Возврат делает
   `queries.cancel_order`: смена статуса и остатки в одной транзакции.
   Заказы со статусом paid_claimed задача не трогает — там клиент сказал, что
   оплатил, и решение за владельцем (см. `queries.get_expired_unpaid_orders`).

2. Напоминание о брошенной корзине — РОВНО одно на корзину. Второе письмо в тот
   же чат превращает магазин в спамера: однократность держится на поле
   `carts.reminded_at`, а не на памяти процесса, поэтому переживает рестарт.

3. Выгрузка заказов и остатков в Google Sheets — по расписанию, потому что
   таблицу владелец открывает когда захочет, а не после каждого заказа. Задача
   ставится, только если выгрузка настроена (`sheets.is_enabled()`): иначе
   планировщик каждые десять минут писал бы в лог, что она выключена.
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from db import queries
from keyboards.orders import added_kb
from services import sheets
from services.format import money, variant_label

logger = logging.getLogger(__name__)

# Раз в час: точность до минуты здесь не нужна (таймаут — сутки), а частые
# проходы по таблице заказов бессмысленны.
_CHECK_INTERVAL_MINUTES = 60

# Корзины проверяем чаще заказов: напоминание ждут часы, а не сутки, и лишний
# час задержки поверх заданного порога заметен.
_CART_CHECK_INTERVAL_MINUTES = 30

# Ночью не пишем: напоминание в три часа ночи раздражает сильнее, чем помогает.
# Проверка просто пропускается — корзина никуда не денется, напомним утром.
_QUIET_FROM_HOUR = 22
_QUIET_TO_HOUR = 9

# Раз в десять минут: таблицу смотрят глазами, и «данные десятиминутной
# давности» там никого не подводят, а чаще дёргать чужой сервис незачем.
_SHEETS_SYNC_MINUTES = 10


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


def _is_quiet_hours() -> bool:
    """True — сейчас ночь по времени магазина, писать клиентам не время."""
    hour = config.now_local().hour
    return hour >= _QUIET_FROM_HOUR or hour < _QUIET_TO_HOUR


def _cart_reminder_text(cart: dict) -> str:
    """Напоминание: что лежит в корзине, на какую сумму и что делать дальше.

    Состав перечисляем полностью, потому что за несколько часов человек уже не
    помнит, какой именно размер выбрал, — а идти проверять ради этого не станет.
    """
    lines = ["🧺 <b>Ваша корзина всё ещё ждёт</b>", ""]
    for item in cart["items"]:
        lines.append(
            f"{html.escape(item['title'])} ({html.escape(variant_label(item))}) — "
            f"{item['qty']} шт"
        )
    lines += [
        "",
        f"Сумма: <b>{money(cart['total'])}</b>",
        "",
        # Без вранья про «отложили за вами»: корзина ничего не резервирует,
        # остаток списывается только при оформлении заказа.
        "Товар в корзине не забронирован — остаток общий. Если всё ещё "
        "актуально, давайте оформим 🙂",
    ]
    return "\n".join(lines)


async def remind_abandoned_carts(bot: Bot) -> int:
    """Одно напоминание по каждой брошенной корзине. Возвращает число отправленных.

    Кому напоминание НЕ уйдёт (условия в `queries.get_abandoned_carts`):
    тем, у кого уже есть незавершённый заказ, — человек оформил, и звать его в
    корзину значит выглядеть невнимательным; и тем, кому уже напоминали.
    """
    if _is_quiet_hours():
        return 0

    carts = await queries.get_abandoned_carts(config.CART_REMINDER_HOURS)
    sent = 0

    for row in carts:
        client_id = row["client_id"]
        if row["channel"] != "telegram":
            continue  # Instagram Direct появится в своей фазе, там другой транспорт

        cart = await queries.get_cart(client_id)
        if not cart["items"]:
            continue  # успел очистить, пока задача шла по списку

        # Метку ставим ДО отправки. Если чат недоступен, напоминание всё равно
        # считается израсходованным: иначе задача ломилась бы в него каждые
        # полчаса до скончания веков.
        await queries.mark_cart_reminded(client_id)
        try:
            await bot.send_message(
                client_id,
                _cart_reminder_text(cart),
                parse_mode="HTML",
                reply_markup=added_kb(),
            )
            sent += 1
        except Exception:
            logger.warning("Не удалось напомнить клиенту %s о корзине", client_id)

    if sent:
        logger.info("Отправлено напоминаний о брошенной корзине: %s", sent)
    return sent


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
    scheduler.add_job(
        remind_abandoned_carts,
        "interval",
        minutes=_CART_CHECK_INTERVAL_MINUTES,
        args=(bot,),
        id="remind_abandoned_carts",
        coalesce=True,
        misfire_grace_time=3600,
    )
    if sheets.is_enabled():
        scheduler.add_job(
            sheets.sync_to_sheets,
            "interval",
            minutes=_SHEETS_SYNC_MINUTES,
            id="sync_google_sheets",
            coalesce=True,
            misfire_grace_time=3600,
        )
    scheduler.start()
    logger.info(
        "Планировщик запущен: отмена неоплаченных заказов старше %s ч (раз в %s мин), "
        "напоминание о корзине после %s ч простоя (раз в %s мин, кроме %s:00–%s:00), "
        "выгрузка в Google Sheets — %s",
        config.ORDER_PAYMENT_TIMEOUT_HOURS,
        _CHECK_INTERVAL_MINUTES,
        config.CART_REMINDER_HOURS,
        _CART_CHECK_INTERVAL_MINUTES,
        _QUIET_FROM_HOUR,
        _QUIET_TO_HOUR,
        f"раз в {_SHEETS_SYNC_MINUTES} мин" if sheets.is_enabled() else "выключена",
    )
    return scheduler
