"""Клавиатуры каталога по кнопке «🛍 Каталог».

Префикс `cat:` разведён с корзиной (`c:`), оформлением (`o:`), решениями
владельца (`oa:`) и админкой каталога (`ap:`) — см. keyboards/orders.py.

Категория в callback_data передаётся НОМЕРОМ, а не названием: у Telegram на
callback_data 64 байта, а кириллическая «Спортивные костюмы» в UTF-8 занимает
больше половины лимита. Номер — позиция в списке queries.get_categories(); если
каталог за это время поменялся, хендлер честно говорит об этом и показывает
категории заново.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.format import money

CB_CATS = "cat:home"     # вернуться к списку категорий
CB_PAGE = "cat:p"        # cat:p:<номер категории>:<страница> — список товаров
CB_ITEM = "cat:i"        # cat:i:<product_id> — показать карточку

# Номер «категории» для варианта «Всё подряд»: отдельным значением, чтобы не
# заводить второй callback и не разбирать в хендлере два формата.
ALL_CATEGORIES = -1

# Сколько товаров на странице. Больше — и список кнопок перестаёт помещаться на
# экран телефона целиком, а именно ради «увидел всё сразу» каталог и делался.
PAGE_SIZE = 8


def categories_kb(categories: list[str]) -> InlineKeyboardMarkup:
    """Первый экран каталога: по кнопке на категорию плюс «Всё подряд»."""
    kb = InlineKeyboardBuilder()
    for index, name in enumerate(categories):
        kb.button(text=name, callback_data=f"{CB_PAGE}:{index}:0")
    kb.button(text="📋 Всё подряд", callback_data=f"{CB_PAGE}:{ALL_CATEGORIES}:0")
    kb.adjust(2)
    return kb.as_markup()


def products_kb(
    products: list[dict], *, category_index: int, page: int, total: int
) -> InlineKeyboardMarkup:
    """Страница списка: товары кнопками, под ними — перелистывание.

    В подписи кнопки цена: без неё клиент открывает карточки подряд, чтобы
    сравнить, и каждое открытие — отдельное сообщение с фото.
    """
    kb = InlineKeyboardBuilder()
    rows: list[int] = []
    for product in products:
        title = product["title"]
        if len(title) > 30:
            title = title[:29] + "…"
        kb.button(
            text=f"{title} — {money(product['price'])}",
            callback_data=f"{CB_ITEM}:{product['id']}",
        )
        rows.append(1)

    nav = 0
    if page > 0:
        kb.button(text="‹ Назад", callback_data=f"{CB_PAGE}:{category_index}:{page - 1}")
        nav += 1
    if (page + 1) * PAGE_SIZE < total:
        kb.button(text="Ещё ›", callback_data=f"{CB_PAGE}:{category_index}:{page + 1}")
        nav += 1
    if nav:
        rows.append(nav)

    kb.button(text="↩️ К категориям", callback_data=CB_CATS)
    rows.append(1)

    kb.adjust(*rows)
    return kb.as_markup()
