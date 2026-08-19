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

# Категорию показываем целиком: сколько товаров в ней есть, столько карточек и
# уходит. Число ниже — не страница, а потолок на случай, когда товаров в
# категории неожиданно много: тридцать сообщений подряд Telegram отдаёт нормально,
# а сотня заливает чат. Дальше — по кнопке «Показать ещё».
PAGE_SIZE = 30


def categories_kb(categories: list[str]) -> InlineKeyboardMarkup:
    """Первый экран каталога: по кнопке на категорию плюс «Всё подряд»."""
    kb = InlineKeyboardBuilder()
    for index, name in enumerate(categories):
        kb.button(text=name, callback_data=f"{CB_PAGE}:{index}:0")
    kb.button(text="📋 Усе поспіль", callback_data=f"{CB_PAGE}:{ALL_CATEGORIES}:0")
    kb.adjust(2)
    return kb.as_markup()


def more_kb(*, category_index: int, page: int) -> InlineKeyboardMarkup:
    """Одна кнопка — дослать остаток категории.

    Ни списка товаров, ни «назад»: карточки уже лежат в переписке выше, а к
    категориям возвращает кнопка «🛍 Каталог» под полем ввода — она на экране
    всегда.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="Показати ще", callback_data=f"{CB_PAGE}:{category_index}:{page}")
    return kb.as_markup()
