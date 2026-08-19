"""Счёт в Telegram: сборка и отправка. Обработка ответов — в handlers/payments.py.

Отдельным файлом, потому что счёт выставляют с двух сторон: мини-приложение
после оформления (web/api/shop.py) и чат, если человек вернулся к неоплаченному
заказу. Собирать одно и то же в двух местах нельзя — разойдутся суммы.

Про суммы. Telegram принимает цену в МЕЛКИХ единицах валюты: 2400 грн — это
240000. Ошибка здесь стоит дорого в буквальном смысле (счёт в сто раз меньше
или больше), поэтому перевод живёт ровно в одном месте — to_minor().

Скидка в счёт отдельной строкой не выносится: promo уже погашен внутри
create_order, и order['total'] — это сумма к оплате. Показывать её ещё раз
значило бы вычесть скидку дважды.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import LabeledPrice

import config
from services.format import money, variant_label

logger = logging.getLogger(__name__)

# Что кладём в payload счёта, чтобы потом узнать заказ. Telegram вернёт эту
# строку в successful_payment слово в слово — по ней и находим, что оплатили.
PAYLOAD_PREFIX = "order"

# Ограничения Telegram на поля счёта. Обрезаем сами: превышение — это отказ
# API, то есть клиент увидит «не удалось выставить счёт» вместо оплаты.
_MAX_TITLE = 32
_MAX_DESCRIPTION = 255


def to_minor(amount: float) -> int:
    """Гривны → копейки. Единственное место, где сумма меняет масштаб."""
    return int(round(float(amount) * 100))


def order_payload(order_id: int) -> str:
    return f"{PAYLOAD_PREFIX}:{order_id}"


def order_id_from_payload(payload: str) -> int | None:
    """Достаёт номер заказа из payload. None — payload не наш или испорчен."""
    prefix, _, tail = (payload or "").partition(":")
    if prefix != PAYLOAD_PREFIX or not tail.isdigit():
        return None
    return int(tail)


def _description(order: dict) -> str:
    """Состав заказа одной строкой — то, что человек видит в окне оплаты."""
    parts = []
    for item in order.get("items", []):
        label = variant_label(item)
        title = item.get("title_snapshot") or item.get("title") or "товар"
        parts.append(f"{title} ({label}) × {item['qty']}")
    text = "; ".join(parts) or f"Заказ №{order['id']}"
    if len(text) > _MAX_DESCRIPTION:
        text = text[: _MAX_DESCRIPTION - 1].rstrip() + "…"
    return text


async def send_invoice(bot: Bot, order: dict) -> bool:
    """Выставляет счёт клиенту в его чат. False — счёт не ушёл.

    Одна строка в счёте, а не позиция на товар: позиции Telegram складывает
    сам, и сумма из них обязана сойтись с итогом до копейки. Скидка по
    промокоду уже вычтена в create_order, поэтому разложенные позиции дали бы
    сумму БОЛЬШЕ итоговой, и счёт бы не выставился.

    Ошибку не поднимаем: заказ уже создан и остатки списаны — падать здесь
    значило бы оставить клиента с заказом, о котором ему не сказали. Вместо
    этого возвращаем False, и вызывающий шлёт реквизиты карты.
    """
    if not config.payments_enabled():
        return False

    title = f"Заказ №{order['id']}"
    try:
        await bot.send_invoice(
            chat_id=order["client_id"],
            title=title[:_MAX_TITLE],
            description=_description(order),
            payload=order_payload(order["id"]),
            provider_token=config.PAYMENT_PROVIDER_TOKEN,
            currency=config.PAYMENT_CURRENCY_CODE,
            prices=[
                LabeledPrice(
                    label=f"Заказ №{order['id']} — {money(order['total'])}",
                    amount=to_minor(order["total"]),
                )
            ],
            # Телефон и адрес у нас уже есть — их собрали при оформлении.
            # Спрашивать второй раз в окне оплаты значит заставлять человека
            # набирать то же самое ещё раз, с телефона, под таймером.
            need_name=False,
            need_phone_number=False,
            need_shipping_address=False,
        )
        return True
    except TelegramAPIError as error:
        # Самое частое здесь — токен провайдера от другого бота или валюта,
        # которую провайдер не принимает. Пишем в лог полностью: без текста
        # ошибки это неотличимо от «клиент заблокировал бота».
        logger.error("Счёт по заказу №%s не выставлен: %s", order["id"], error)
        return False
