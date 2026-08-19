"""Фоновые задачи бота.

Их четыре.

1. Отмена заказов, которые так и не оплатили. Нужна не ради порядка в таблице,
   а ради склада — `queries.create_order` списывает остатки сразу, и брошенный
   заказ держит товар, который никто не купит. Возврат делает
   `queries.cancel_order`: смена статуса и остатки в одной транзакции.
   Заказы со статусом paid_claimed задача не трогает — там клиент сказал, что
   оплатил, и решение за владельцем (см. `queries.get_expired_unpaid_orders`).

2. Напоминание о брошенной корзине — РОВНО одно на корзину. Второе письмо в тот
   же чат превращает магазин в спамера: однократность держится на поле
   `carts.reminded_at`, а не на памяти процесса, поэтому переживает рестарт.

3. Напоминание тем, кто говорил с ботом и пропал, ничего не купив. Здесь пауза
   держится на `clients.outreach_at` — она общая для обеих рассылок, поэтому
   человек, получивший письмо про корзину, не получит следом второе про то, что
   его давно не было.

   Обе рассылки пишет модель, а не шаблон (см. services/outreach.py), и обе
   несут именной промокод, если скидки включены (PROMO_PERCENT).

4. Выгрузка заказов и остатков в Google Sheets — по расписанию, потому что
   таблицу владелец открывает когда захочет, а не после каждого заказа. Задача
   ставится, только если выгрузка настроена (`sheets.is_enabled()`): иначе
   планировщик каждые десять минут писал бы в лог, что она выключена.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from db import queries
from keyboards.menus import main_menu
from keyboards.orders import added_kb
from services import mail, outreach, sheets
from services.format import money

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
    """Отменяет просроченные неоплаченные заказы. Возвращает число отменённых.

    Владельцу не пишем: сюда попадают только заказы в статусе awaiting_payment,
    а про них он ничего и не знал — заявка в Telegram уходит после кнопки
    клиента «Я оплатил». Отменённые заказы видны в веб-CRM.
    """
    orders = await queries.get_expired_unpaid_orders(config.ORDER_PAYMENT_TIMEOUT_HOURS)
    cancelled = 0

    for order in orders:
        if not await queries.cancel_order(order["id"], note="Не оплачен вовремя"):
            continue  # кто-то успел отменить или подтвердить заказ раньше
        cancelled += 1

        # Сообщение мягкое: человек мог просто передумать, и ругаться на него
        # незачем — он вернётся. Отдельно говорим, что товар снова доступен.
        try:
            await bot.send_message(
                order["client_id"],
                f"Замовлення №{order['id']} на {money(order['total'])} ми "
                f"скасували — оплата так і не надійшла, а товар тримати довше не "
                f"можемо.\n\n"
                f"Якщо все ще потрібен — напишіть, зберемо замовлення заново 🙂",
            )
        except Exception:
            # Бот заблокирован или чат удалён. Заказ уже отменён и остатки
            # вернулись — это важнее, чем доставленное уведомление.
            logger.warning("Не удалось сообщить клиенту %s об отмене заказа %s",
                           order["client_id"], order["id"])

    if cancelled:
        logger.info("Отменено неоплаченных заказов: %s", cancelled)
    return cancelled


def _is_quiet_hours() -> bool:
    """True — сейчас ночь по времени магазина, писать клиентам не время."""
    hour = config.now_local().hour
    return hour >= _QUIET_FROM_HOUR or hour < _QUIET_TO_HOUR


async def remind_abandoned_carts(bot: Bot) -> int:
    """Одно напоминание по каждой брошенной корзине. Возвращает число отправленных.

    Кому напоминание НЕ уйдёт (условия в `queries.get_abandoned_carts`):
    тем, у кого уже есть незавершённый заказ, — человек оформил, и звать его в
    корзину значит выглядеть невнимательным; и тем, кому уже напоминали.

    Текст пишет модель под конкретную корзину (services/outreach.py), а к нему
    код добавляет состав, сумму и — если скидки включены — именной промокод.
    Разделение важное: числа в сообщении должны приходить из базы, а не из
    фантазии модели.
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
        await queries.mark_outreach(client_id)

        client = await queries.get_client(client_id)
        promo = await outreach.make_promo(client_id, "cart")
        text = await outreach.cart_reminder(client, cart, promo)

        try:
            # Без parse_mode: текст писала модель, и любой «<» в нём сорвал бы
            # отправку целиком (см. шапку services/outreach.py).
            await bot.send_message(client_id, text, reply_markup=added_kb())
            sent += 1
        except Exception:
            logger.warning("Не удалось напомнить клиенту %s о корзине", client_id)

        await outreach.send_email_copy(client, "Ваш кошик у магазині чекає", text)

    if sent:
        logger.info("Отправлено напоминаний о брошенной корзине: %s", sent)
    return sent


async def win_back_sleeping_clients(bot: Bot) -> int:
    """Напоминание тем, кто говорил с ботом и пропал, ничего не купив.

    Второй повод написать первым — и куда более скользкий, чем корзина: там
    человек сам положил товар и явно чего-то хотел, а здесь мы приходим к тому,
    кто нас ни о чём не просил. Поэтому ограничений сразу четыре: только раз в
    WINBACK_COOLDOWN_DAYS на человека, не чаще чем через WINBACK_AFTER_DAYS
    тишины, не больше WINBACK_BATCH за проход и никогда ночью.

    Тех, у кого лежит непустая корзина, задача не трогает — им идёт своё
    напоминание, и получить оба сразу значит выглядеть навязчивым.
    """
    if not config.WINBACK_ENABLED or _is_quiet_hours():
        return 0

    clients = await queries.get_sleeping_clients(
        days=config.WINBACK_AFTER_DAYS,
        cooldown_days=config.WINBACK_COOLDOWN_DAYS,
        limit=config.WINBACK_BATCH,
    )
    sent = 0

    for row in clients:
        client_id = row["client_id"]
        await queries.mark_outreach(client_id)

        client = await queries.get_client(client_id)
        promo = await outreach.make_promo(client_id, "sleeping")
        text = await outreach.winback(client, promo)

        try:
            await bot.send_message(client_id, text, reply_markup=main_menu())
            sent += 1
        except Exception:
            # Обычное дело: человек удалил чат или заблокировал бота. Метка уже
            # стоит, значит второй раз мы к нему не придём.
            logger.warning("Не удалось написать клиенту %s", client_id)

        await outreach.send_email_copy(
            client, f"{config.SHOP_NAME}: давно вас не було", text)

    if sent:
        logger.info("Отправлено напоминаний «давно не заходили»: %s", sent)
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
    if config.WINBACK_ENABLED:
        # Раз в сутки, а не каждый час: «давно не заходил» — состояние, которое
        # за час не меняется, а лишние проходы только жгут запросы к модели.
        # Час выбран дневной и в рабочее время — ночные пропускаются проверкой
        # тихих часов, и задача просто ничего не делала бы до следующих суток.
        scheduler.add_job(
            win_back_sleeping_clients,
            "cron",
            hour=12,
            minute=0,
            args=(bot,),
            id="win_back_sleeping_clients",
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
        "«давно не заходили» — %s, скидка в напоминаниях — %s, письма — %s, "
        "выгрузка в Google Sheets — %s",
        config.ORDER_PAYMENT_TIMEOUT_HOURS,
        _CHECK_INTERVAL_MINUTES,
        config.CART_REMINDER_HOURS,
        _CART_CHECK_INTERVAL_MINUTES,
        _QUIET_FROM_HOUR,
        _QUIET_TO_HOUR,
        (f"после {config.WINBACK_AFTER_DAYS} дн тишины, не чаще раза в "
         f"{config.WINBACK_COOLDOWN_DAYS} дн" if config.WINBACK_ENABLED else "выключено"),
        (f"{config.PROMO_PERCENT}% на {config.PROMO_TTL_DAYS} дн"
         if config.PROMO_PERCENT else "выключена"),
        "включены" if mail.is_enabled() else "выключены",
        f"раз в {_SHEETS_SYNC_MINUTES} мин" if sheets.is_enabled() else "выключена",
    )
    return scheduler
