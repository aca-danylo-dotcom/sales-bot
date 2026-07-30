"""Каталог по кнопке «🛍 Каталог» — без участия ИИ.

Раньше эта кнопка была обычным текстом и уходила в модель, а та по инструкции
сначала уточняет запрос: чтобы увидеть товары, покупателю приходилось просить
дважды. Здесь путь детерминированный — категории, страницы, карточка, — и
работает он одинаково с первого нажатия.

Роутер подключается ДО клиентского (bot.py): тот ловит любой текст и иначе
перехватил бы кнопку. Карточку товара рисует общий services/cards.py — тот же,
которым отвечает ИИ-продавец, чтобы товар выглядел одинаково на обоих путях.
"""
from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from db import queries
from services.cards import send_product_card
from keyboards.catalog import (
    ALL_CATEGORIES,
    CB_CATS,
    CB_ITEM,
    CB_PAGE,
    PAGE_SIZE,
    categories_kb,
    nav_kb,
)
from keyboards.menus import BTN_CATALOG

logger = logging.getLogger(__name__)
router = Router(name="catalog")


# Просьба показать каталог, написанная словами. Кнопка внизу экрана есть, но
# половина покупателей просто пишет «а что у вас есть» — и такая реплика уходила
# модели, а та по инструкции отправляла клиента к кнопке. Одно и то же сообщение
# в ответ на живой вопрос выглядит как отписка, поэтому короткие просьбы про
# каталог открывают тот же экран категорий, что и кнопка.
#
# Ловим осторожно: фраза должна состоять ТОЛЬКО из слов про каталог и служебных
# слов. «Есть перчатки 42?» — уже конкретный запрос, его ищет ИИ инструментом.
#
# Слова ниже однозначны сами по себе: их достаточно, чтобы открыть категории.
_CATALOG_WORDS = frozenset({
    "каталог", "каталоге", "каталогом", "ассортимент", "асортимент",
    "товар", "товары", "товаров", "товари", "вещи", "речі",
})

# А эти говорят только про наличие и каталог сами по себе НЕ значат. «Есть в
# наличии?» — вопрос про товар, карточку которого клиент видит перед собой:
# меню категорий в ответ и есть то самое «отвечает не по делу». Поэтому они
# работают лишь вместе с вопросительным словом: «что у вас есть».
_STOCK_WORDS = frozenset({
    "есть", "є", "наличии", "наявності", "продаете", "продаєте",
})

_ASK_WORDS = frozenset({"что", "чего", "шо", "що", "какие", "какой"})

_FILLER_WORDS = frozenset({
    "как", "а", "и", "у", "в",
    "вас", "тебя", "ваш", "ваши", "весь", "все", "вся", "всі", "мне", "можно",
    "хочу", "хотел", "хотела", "покажи", "покажите", "показать", "покажеш",
    "посмотреть", "поглянути", "глянуть", "список", "пожалуйста", "плиз",
    "там", "ну", "давай", "интересно", "цікаво",
})


def _is_catalog_request(text: str | None) -> bool:
    """Просьба показать каталог целиком — или всё же конкретный запрос?

    Считаем каталогом только фразу, в которой есть слово про каталог и нет ни
    одного «своего» слова: любое лишнее слово («перчатки», «размер 42») значит,
    что человек ищет конкретное, — такое отдаём ИИ, он умеет искать.
    """
    clean = (text or "").lower().replace("ё", "е")
    words = [word.strip("«»\"'()") for word in clean.replace("?", " ").replace("!", " ")
             .replace(",", " ").replace(".", " ").split()]
    words = [word for word in words if word]
    if not words or len(words) > 6:
        return False
    known = _CATALOG_WORDS | _STOCK_WORDS | _ASK_WORDS | _FILLER_WORDS
    if any(word not in known for word in words):
        return False
    if any(word in _CATALOG_WORDS for word in words):
        return True
    # Осталось только про наличие: «что есть» — каталог, «есть в наличии?» — нет.
    return (any(word in _STOCK_WORDS for word in words)
            and any(word in _ASK_WORDS for word in words))


async def _categories_view() -> tuple[str, object | None]:
    """Текст и кнопки первого экрана каталога."""
    categories = await queries.get_categories()
    if not categories and not await queries.count_products(status="active"):
        return "Каталог пока пуст — товары вот-вот появятся.", None
    return "Выберите, что посмотреть:", categories_kb(categories)


async def _show_page(callback: CallbackQuery, category_index: int, page: int) -> None:
    """Показывает страницу категории: заголовок, карточки товаров, перелистывание.

    Раньше здесь был список названий кнопками, и до фото с ценой клиент доходил
    в два нажатия. Теперь товары приходят сразу карточками — то же, что видит
    покупатель у ИИ-продавца.

    Номер категории приходит из callback_data и мог устареть (товар сняли с
    витрины — категория исчезла). Проверяем границы и честно возвращаем клиента
    к списку категорий, а не показываем чужую.
    """
    category: str | None = None
    if category_index != ALL_CATEGORIES:
        categories = await queries.get_categories()
        if not 0 <= category_index < len(categories):
            await _replace(callback, "Каталог обновился — выберите заново:",
                           categories_kb(categories))
            return
        category = categories[category_index]

    total = await queries.count_products(category=category, status="active", in_stock=True)
    products = await queries.list_products(
        category=category, status="active", in_stock=True,
        limit=PAGE_SIZE, offset=page * PAGE_SIZE,
    )
    if not products:
        where = f"В категории «{html.escape(category)}»" if category else "В каталоге"
        await _replace(
            callback,
            f"{where} сейчас всё разобрали. Загляните в другую категорию — "
            f"или напишите, что ищете, и я поищу под заказ.",
            nav_kb(category_index=category_index, page=0, total=0),
        )
        return

    first = page * PAGE_SIZE + 1
    head = html.escape(category) if category else "Все товары"
    # Заголовок ставим на место сообщения, по которому нажали: кнопки категорий
    # (или прошлое перелистывание) уходят, а карточки идут следом.
    await _replace(
        callback,
        f"<b>{head}</b>\n{first}–{first + len(products) - 1} из {total}",
        None,
    )
    for product in products:
        # Список даёт только шапку товара, а карточке нужны размеры и фото.
        full = await queries.get_product_full(product["id"])
        if full:
            await send_product_card(callback.message, full)

    shown = first + len(products) - 1
    tail = "Это все товары." if shown >= total else f"Показал {shown} из {total}."
    await callback.message.answer(
        tail, reply_markup=nav_kb(category_index=category_index, page=page, total=total)
    )


async def _replace(callback: CallbackQuery, text: str, markup) -> None:
    """Переписывает сообщение со списком; если правка не прошла — шлёт новое.

    Правка невозможна, когда кнопки висят под фотографией товара: у такого
    сообщения нет текста.
    """
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.message(StateFilter(None), F.text == BTN_CATALOG)
@router.message(StateFilter(None), F.text.func(_is_catalog_request))
async def open_catalog(message: Message) -> None:
    await queries.ensure_client(message.from_user.id)
    text, markup = await _categories_view()
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == CB_CATS)
async def back_to_categories(callback: CallbackQuery) -> None:
    text, markup = await _categories_view()
    await _replace(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_PAGE}:"))
async def show_page(callback: CallbackQuery) -> None:
    _, _, category_index, page = callback.data.split(":", 3)
    # Отвечаем сразу: карточек может быть пять, и Telegram успеет показать
    # клиенту «часики» на кнопке, пока они уходят.
    await callback.answer()
    await _show_page(callback, int(category_index), int(page))


@router.callback_query(F.data.startswith(f"{CB_ITEM}:"))
async def show_item(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[2])
    product = await queries.get_product_full(product_id)
    if not product or not product["is_active"]:
        await callback.answer("Этого товара уже нет в продаже", show_alert=True)
        return

    # Карточку шлём новым сообщением, а список оставляем на месте: клиент
    # смотрит товары подряд и возвращается к перечню без лишних нажатий.
    await send_product_card(callback.message, product)
    await callback.answer()
