"""Клавиатуры покупателя.

Главное меню — обычные reply-кнопки: они всегда под рукой и не теряются в
переписке. Тексты кнопок дублируются в хендлерах как фильтры, поэтому меняются
только здесь и там одновременно.
"""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

import config

BTN_CATALOG = "🛍 Каталог"
BTN_CART = "🧺 Корзина"
BTN_ORDERS = "📦 Мои заказы"
BTN_HELP = "❓ Доставка и оплата"
# Витрина отдельной кнопкой, а не вместо каталога: у кнопки с web_app нет
# текстового события — нажатие открывает окно, и бот о нём не узнаёт. Замени мы
# ею «Каталог», разговор с ИИ-продавцом лишился бы половины пути.
BTN_SHOP = "🏬 Магазин"


def main_menu() -> ReplyKeyboardMarkup:
    """Постоянное меню под полем ввода.

    Кнопка витрины появляется только когда мини-приложение выложено
    (config.WEBAPP_URL): Telegram отказывается показывать клавиатуру целиком,
    если у web_app-кнопки адрес не https, — то есть без проверки пропало бы и
    обычное меню.
    """
    rows = [
        [KeyboardButton(text=BTN_CATALOG), KeyboardButton(text=BTN_CART)],
        [KeyboardButton(text=BTN_ORDERS), KeyboardButton(text=BTN_HELP)],
    ]
    if config.webapp_enabled():
        rows.insert(0, [
            KeyboardButton(text=BTN_SHOP, web_app=WebAppInfo(url=config.WEBAPP_URL))
        ])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
