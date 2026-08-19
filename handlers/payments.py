"""Оплата картой внутри Telegram: подтверждение счёта и приход денег.

Работает только когда подключён платёжный провайдер (config.PAYMENT_PROVIDER_TOKEN).
Без него магазин живёт по-старому — переводом на карту, и ни один из этих
обработчиков просто не срабатывает: счетов никто не выставляет.

Чем этот путь отличается от «Я оплатил». Там клиент СООБЩАЕТ об оплате, и
владелец сверяет поступление руками — поэтому заказ уходит в paid_claimed и
ждёт решения. Здесь про оплату говорит платёжная система, проверять нечего:
заказ сразу становится confirmed, и владельцу остаётся собрать посылку.

Про сроки. На pre_checkout_query Telegram даёт 10 секунд: не ответили — платёж
отменяется у клиента без объяснений. Поэтому здесь только быстрая проверка по
базе, никаких обращений к внешним сервисам.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message, PreCheckoutQuery

import config
from db import queries
from keyboards.menus import main_menu
from services import payments
from services.format import ORDER_STATUS_UA, money

logger = logging.getLogger(__name__)

router = Router(name="payments")

_HTML = "HTML"

# Из каких состояний заказ можно оплатить. 'new' — заказ, заведённый вручную в
# CRM; остальное уже либо оплачено, либо отменено.
_PAYABLE = ("awaiting_payment", "new")


@router.pre_checkout_query()
async def confirm_checkout(query: PreCheckoutQuery) -> None:
    """Последняя проверка перед списанием денег.

    Отказ здесь — единственный момент, когда платёж можно остановить бесплатно.
    После него деньги уже у провайдера, и «извините, товар кончился» стоит
    возврата. Поэтому проверяем всё, что можно проверить: наш ли это счёт, тот
    ли клиент, в том ли заказ состоянии и сходится ли сумма.

    Сумма сверяется отдельно, потому что счёт живёт в чате сколько угодно:
    человек мог открыть старый счёт после того, как заказ отменили и оформили
    заново.
    """
    order_id = payments.order_id_from_payload(query.invoice_payload)
    if order_id is None:
        await query.answer(ok=False, error_message="Рахунок застарів. Оформіть замовлення заново.")
        return

    order = await queries.get_order(order_id)
    if not order or order["client_id"] != query.from_user.id:
        await query.answer(ok=False, error_message="Замовлення не знайдено.")
        return

    if order["status"] not in _PAYABLE:
        # Статус подставляем через двоеточие, а не в середину фразы: в словаре
        # лежат целые формулировки («оплата подтверждена, готовим к отправке»),
        # и «заказ уже оплата подтверждена» читалось бы как оговорка.
        human = ORDER_STATUS_UA.get(order["status"], order["status"])
        await query.answer(
            ok=False, error_message=f"Замовлення №{order_id}: {human}."
        )
        return

    if query.total_amount != payments.to_minor(order["total"]):
        logger.warning(
            "Сумма счёта разошлась с заказом №%s: пришло %s, ожидали %s",
            order_id, query.total_amount, payments.to_minor(order["total"]),
        )
        await query.answer(
            ok=False,
            error_message="Сума замовлення змінилася. Відкрийте замовлення й оплатіть заново.",
        )
        return

    await query.answer(ok=True)


@router.message(F.successful_payment)
async def payment_received(message: Message, bot: Bot) -> None:
    """Деньги пришли: подтверждаем заказ и зовём владельца собирать посылку.

    Статус ставим через advance_order_status с проверкой состояния: Telegram при
    сбое связи повторяет доставку апдейта, и без неё одно и то же поступление
    дважды переписало бы заказ и дважды дёрнуло владельца.
    """
    payment = message.successful_payment
    order_id = payments.order_id_from_payload(payment.invoice_payload)
    if order_id is None:
        # Деньги списаны, а за что — непонятно. Это не должно случаться, но
        # молчать здесь нельзя: платёж есть, и разбираться придётся вручную.
        logger.error(
            "Оплата с неизвестным payload %r от %s на %s",
            payment.invoice_payload, message.from_user.id, payment.total_amount,
        )
        await message.answer(
            "Оплата пройшла, але замовлення за нею не знайшлося. Напишіть нам — розберемось."
        )
        return

    moved = await queries.advance_order_status(
        order_id, "confirmed", allowed_from=_PAYABLE
    )
    order = await queries.get_order(order_id)
    if not order:
        logger.error("Оплачен заказ №%s, которого нет в базе", order_id)
        return

    if not moved:
        # Повторный апдейт по уже подтверждённому заказу. Клиенту отвечаем
        # спокойно: для него это тот же самый платёж, а не второй.
        logger.info("Повторное подтверждение оплаты заказа №%s", order_id)
        await message.answer(f"Замовлення №{order_id} вже оплачено і в роботі.")
        return

    logger.info(
        "Заказ №%s оплачен в Telegram: %s %s, платёж %s",
        order_id, payment.total_amount, payment.currency,
        payment.provider_payment_charge_id,
    )

    await message.answer(
        f"Оплата пройшла ✅\n\n"
        f"Замовлення №{order_id} на {money(order['total'])} підтверджено — збираємо "
        f"посилку. Номер накладної надішлемо сюди, щойно відправимо.",
        reply_markup=main_menu(),
    )
    await _notify_admin(bot, order, message.from_user.username)


async def _notify_admin(bot: Bot, order: dict, username: str | None) -> None:
    """Пуш владельцу об оплаченном заказе.

    Свой текст, а не тот, что уходит по кнопке «Я оплатил»: там владельца
    просят СВЕРИТЬ поступление, здесь сверять нечего — деньги уже пришли, и
    кнопки подтверждения были бы лишним шагом.

    Состав заказа собираем тем же кодом, что и остальные сообщения по заказам,
    поэтому импорт локальный: handlers/orders.py тянет за собой клавиатуры и
    состояния, а на уровне модуля это замкнуло бы импорты в кольцо.
    """
    from handlers.orders import admin_order_text  # noqa: PLC0415

    full = await queries.get_order_full(order["id"]) or order
    try:
        await bot.send_message(
            config.ADMIN_ID,
            admin_order_text(
                full,
                username,
                head=(
                    f"💰 <b>Заказ №{order['id']} оплачен картой</b>",
                    "Деньги пришли, подтверждать не нужно — собирайте посылку.",
                ),
            ),
            parse_mode=_HTML,
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        logger.exception(
            "Не удалось сообщить владельцу об оплате заказа %s", order["id"]
        )
