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

CB_CATS = "cat:home"     # вернуться к списку категорий
CB_PAGE = "cat:p"        # cat:p:<номер категории>:<страница> — товары карточками
# cat:i:<product_id> — показать карточку. Кнопок с этим префиксом бот больше не
# рисует, но они остались в старых сообщениях у клиентов: хендлер живёт ради них.
CB_ITEM = "cat:i"

# Номер «категории» для варианта «Всё подряд»: отдельным значением, чтобы не
# заводить второй callback и не разбирать в хендлере два формата.
ALL_CATEGORIES = -1

# Сколько товаров на странице. Категория открывается карточками — с фото, ценой
# и кнопкой у каждой, — поэтому страница короче списка кнопок: пять сообщений
# подряд человек ещё пролистывает, восемь превращаются в ленту спама.
PAGE_SIZE = 5


def categories_kb(categories: list[str]) -> InlineKeyboardMarkup:
    """Первый экран каталога: по кнопке на категорию плюс «Всё подряд»."""
    kb = InlineKeyboardBuilder()
    for index, name in enumerate(categories):
        kb.button(text=name, callback_data=f"{CB_PAGE}:{index}:0")
    kb.button(text="📋 Всё подряд", callback_data=f"{CB_PAGE}:{ALL_CATEGORIES}:0")
    kb.adjust(2)
    return kb.as_markup()


def nav_kb(*, category_index: int, page: int, total: int) -> InlineKeyboardMarkup:
    """Перелистывание под карточками категории.

    Кнопок товаров здесь больше нет: сами товары пришли карточками выше, и
    список названий поверх них — второй раз одно и то же.
    """
    kb = InlineKeyboardBuilder()
    rows: list[int] = []

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
