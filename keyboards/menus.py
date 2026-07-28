"""Клавиатуры покупателя.

Главное меню — обычные reply-кнопки: они всегда под рукой и не теряются в
переписке. Тексты кнопок дублируются в хендлерах как фильтры, поэтому меняются
только здесь и там одновременно.
"""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_CATALOG = "🛍 Каталог"
BTN_CART = "🧺 Корзина"
BTN_ORDERS = "📦 Мои заказы"
BTN_HELP = "❓ Доставка и оплата"


def main_menu() -> ReplyKeyboardMarkup:
    """Постоянное меню под полем ввода."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CATALOG), KeyboardButton(text=BTN_CART)],
            [KeyboardButton(text=BTN_ORDERS), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )
