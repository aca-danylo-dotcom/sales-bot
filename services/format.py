"""Форматирование значений для сообщений клиенту.

Живёт отдельно, потому что одни и те же цены и подписи вариантов показывают
и карточки товаров (handlers/client.py), и корзина с заказами
(handlers/orders.py). Дублировать формат нельзя: «1200 грн» в карточке и
«1 200.00 грн» в корзине выглядят как разные цены одного товара.
"""
from __future__ import annotations

import config


def money(value: float) -> str:
    """Цена так, как её читает человек: 1 200 грн, без хвоста ,00."""
    rounded = round(float(value), 2)
    text = f"{rounded:,.0f}" if rounded == int(rounded) else f"{rounded:,.2f}"
    return f"{text.replace(',', ' ')} {config.SHOP_CURRENCY}"


def variant_label(variant: dict) -> str:
    """«12 oz / чёрный», «42», «чёрный» — то из размера и цвета, что заполнено."""
    parts = [part for part in (variant.get("size"), variant.get("color")) if part]
    return " / ".join(parts) if parts else "один вариант"


# Статусы заказа человеческим языком. Одни и те же формулировки видит клиент в
# «Моих заказах» и получает ИИ через get_my_orders — иначе бот и кнопка
# называли бы одно состояние по-разному.
ORDER_STATUS_RU = {
    "new": "новый",
    "awaiting_payment": "ждёт оплаты",
    "paid_claimed": "клиент оплатил, ждём подтверждения",
    "confirmed": "оплата подтверждена, готовим к отправке",
    "shipped": "отправлен",
    "done": "выполнен",
    "cancelled": "отменён",
}
