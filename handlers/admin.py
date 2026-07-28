"""Админка каталога: добавление и правка товаров прямо в Telegram.

Расчёт на телефон: фотографировать товар и заводить его удобнее там, где
камера. Массовые правки цен и остатков появятся позже в веб-CRM — эти два
инструмента дополняют друг друга и работают с одной базой.

Товар создаётся в базе сразу после ввода цены и до конца мастера остаётся
скрытым (is_active = 0). Так фото сохраняются под настоящим product_id, а
брошенный на середине мастер оставляет черновик, который видно в каталоге и
можно доделать или удалить — вместо потерянного ввода.
"""
from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, Message

import config
from db import queries
from keyboards.admin import (
    CB_ADD,
    CB_CARD,
    CB_CAT,
    CB_DEL,
    CB_DEL_OK,
    CB_DONE,
    CB_EDIT,
    CB_LIST,
    CB_MENU,
    CB_PHOTO_ADD,
    CB_PHOTO_DEL,
    CB_PHOTO_MAIN,
    CB_PHOTOS,
    CB_PUBLISH,
    CB_SKIP,
    CB_STOCK,
    CB_TOGGLE,
    CB_VAR,
    CB_VAR_ADD,
    CB_VAR_DEL,
    CATEGORIES,
    admin_main_menu,
    back_to_menu_kb,
    categories_kb,
    confirm_delete_kb,
    photo_upload_kb,
    photos_kb,
    product_card_kb,
    products_kb,
    publish_kb,
    skip_kb,
    variant_kb,
    variant_label,
    variants_kb,
)
from services import media

logger = logging.getLogger(__name__)

router = Router()

# Весь роутер — только владельцу магазина. Для остальных апдейты идут дальше,
# в клиентские хендлеры.
router.message.filter(F.from_user.id == config.ADMIN_ID)
router.callback_query.filter(F.from_user.id == config.ADMIN_ID)

_MENU_TITLE = "🛠 Админка каталога\nВыберите раздел:"
_PAGE_SIZE = 8
_MAX_TITLE = 120
_MAX_TEXT = 1000

_VARIANTS_HINT = (
    "Пришлите варианты одной строкой или списком:\n\n"
    "<code>S:чёрный:5, M:чёрный:3, L:серый:2</code>\n\n"
    "Формат — <b>размер:цвет:количество</b>. Можно короче:\n"
    "• <code>42:3</code> — только размер и количество\n"
    "• <code>10</code> — товар без размеров и цветов\n"
    "• <code>-:красный:4</code> — только цвет (прочерк вместо размера)"
)


class AddProduct(StatesGroup):
    title = State()
    description = State()
    category = State()
    price = State()
    variants = State()
    photos = State()


class EditField(StatesGroup):
    """Правка одного поля готового товара; что именно правим — в data."""

    value = State()


class VariantStock(StatesGroup):
    value = State()


class AddVariant(StatesGroup):
    value = State()


class AddPhoto(StatesGroup):
    waiting = State()


# ─────────────────────────── Разбор ввода ───────────────────────────


def parse_variants(text: str) -> tuple[list[dict], list[str]]:
    """Разбирает пачку вариантов в список и список непонятных кусков.

    Возвращаем ошибки, а не падаем на первой: если в строке из десяти позиций
    одна с опечаткой, честнее завести девять и показать, что не разобрали.
    """
    variants: list[dict] = []
    errors: list[str] = []
    chunks = [c.strip() for c in text.replace("\n", ",").split(",")]

    for chunk in chunks:
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(":")]

        if len(parts) == 1:
            if not parts[0].isdigit():
                errors.append(chunk)
                continue
            size, color, stock = "", "", int(parts[0])
        elif len(parts) == 2:
            # «42:3» — размер и количество, «S:чёрный» — размер и цвет без остатка
            if parts[1].isdigit():
                size, color, stock = parts[0], "", int(parts[1])
            else:
                size, color, stock = parts[0], parts[1], 0
        elif len(parts) == 3:
            if not parts[2].isdigit():
                errors.append(chunk)
                continue
            size, color, stock = parts[0], parts[1], int(parts[2])
        else:
            errors.append(chunk)
            continue

        # Прочерк — способ сказать «этого признака у товара нет»
        size = "" if size == "-" else size[:50]
        color = "" if color == "-" else color[:50]
        variants.append({"size": size, "color": color, "stock": stock})

    return variants, errors


def parse_price(text: str) -> float | None:
    """«1 200,50» → 1200.5. None — если это не похоже на цену."""
    cleaned = text.replace(",", ".").replace(" ", "").replace(" ", "").strip()
    try:
        price = float(cleaned)
    except ValueError:
        return None
    return price if price > 0 else None


# ─────────────────────────── Отрисовка ───────────────────────────

# Сообщения админки размечены HTML, а бот создан без parse_mode по умолчанию
# (ответы ИИ уходят сырым текстом, чтобы < и > в них ничего не ломали). Поэтому
# режим указываем на каждой отправке, а всё, что ввёл человек, экранируем.
_HTML = "HTML"


def _esc(value: object) -> str:
    """Экранирует пользовательский текст: товар «Куртка <M>» иначе сломал бы разметку."""
    return html.escape(str(value), quote=False)


def _card_text(product: dict) -> str:
    lines = [f"<b>{_esc(product['title'])}</b>"]
    if product["category"]:
        lines.append(f"Категория: {_esc(product['category'])}")
    lines.append(f"Цена: {product['price']:g} {_esc(config.SHOP_CURRENCY)}")
    if product["description"]:
        lines.append(f"\n{_esc(product['description'])}\n")

    if product["variants"]:
        lines.append("<b>Варианты:</b>")
        for variant in product["variants"]:
            stock = f"{variant['stock']} шт" if variant["stock"] else "нет в наличии"
            lines.append(f"• {_esc(variant_label(variant))} — {stock}")
    else:
        lines.append("⚠️ Варианты не заданы — товар нельзя купить.")

    if not product["photos"]:
        lines.append("⚠️ Нет фото.")
    else:
        lines.append(f"\nФото: {len(product['photos'])}")

    lines.append("Статус: ✅ в продаже" if product["is_active"] else "Статус: 🚫 скрыт")
    return "\n".join(lines)


async def _answer(
    message: Message, text: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    await message.answer(text, reply_markup=markup, parse_mode=_HTML)


async def _edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Переписывает сообщение под кнопкой; если Telegram против — шлёт новое.

    Против бывает в двух случаях: текст не изменился (повторный тап) или
    сообщение слишком старое для правки.
    """
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode=_HTML)
    except TelegramBadRequest:
        await _answer(callback.message, text, markup)


async def _show_list(callback: CallbackQuery, page: int) -> None:
    total = await queries.count_products()
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    products = await queries.list_products(limit=_PAGE_SIZE, offset=page * _PAGE_SIZE)

    if not products:
        await _edit(
            callback,
            "Каталог пуст. Добавьте первый товар — это займёт минуту.",
            admin_main_menu(),
        )
        return

    text = f"📋 Каталог: {total} товар(ов).\nВыберите товар, чтобы открыть карточку."
    await _edit(callback, text, products_kb(products, page, total_pages))


async def _show_card(callback: CallbackQuery, product_id: int) -> None:
    product = await queries.get_product_full(product_id)
    if not product:
        await _edit(callback, "Товар не найден — возможно, он уже удалён.", admin_main_menu())
        return
    await _edit(callback, _card_text(product), product_card_kb(product))


async def _show_stock(callback: CallbackQuery, product_id: int) -> None:
    product = await queries.get_product(product_id)
    variants = await queries.get_variants(product_id)
    if not product:
        await _edit(callback, "Товар не найден.", admin_main_menu())
        return
    text = (
        f"📦 Остатки: <b>{_esc(product['title'])}</b>\n\n"
        "Нажмите на вариант, чтобы изменить количество."
        if variants
        else f"📦 <b>{_esc(product['title'])}</b>\n\nВариантов пока нет — добавьте хотя бы один."
    )
    await _edit(callback, text, variants_kb(product_id, variants))


async def _show_photos(callback: CallbackQuery, product_id: int) -> None:
    product = await queries.get_product(product_id)
    photos = await queries.get_photos(product_id)
    if not product:
        await _edit(callback, "Товар не найден.", admin_main_menu())
        return
    text = (
        f"🖼 Фото: <b>{_esc(product['title'])}</b> — {len(photos)} шт.\n\n"
        "Нажатие на «Фото N» делает его главным (⭐ показывается клиенту первым), "
        "корзина рядом — удаляет."
        if photos
        else f"🖼 <b>{_esc(product['title'])}</b>\n\nФото пока нет."
    )
    await _edit(callback, text, photos_kb(product_id, photos))


async def _send_album(message: Message, photos: list[dict]) -> None:
    """Показывает снимки альбомом в том же порядке, что и кнопки под ним."""
    if not photos:
        return
    album = [
        InputMediaPhoto(media=photo["tg_file_id"])
        for photo in photos[:10]
        if photo["tg_file_id"]
    ]
    if album:
        await message.answer_media_group(album)


# ─────────────────────────── Меню ───────────────────────────


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()  # вдруг владелец бросил незаконченный ввод
    await _answer(message, _MENU_TITLE, admin_main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _answer(message, "Отменил.", admin_main_menu())


@router.callback_query(F.data == CB_MENU)
async def back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit(callback, _MENU_TITLE, admin_main_menu())
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_LIST}:"))
async def show_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_list(callback, int(callback.data.split(":")[-1]))
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_CARD}:"))
async def show_card(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_card(callback, int(callback.data.split(":")[-1]))
    await callback.answer()


# ─────────────────────── Мастер добавления товара ───────────────────────


@router.callback_query(F.data == CB_ADD)
async def add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddProduct.title)
    await _edit(
        callback,
        "➕ Новый товар.\n\nКак он называется?\n\n<i>/cancel — отменить</i>",
        back_to_menu_kb(),
    )
    await callback.answer()


@router.message(AddProduct.title, F.text)
async def add_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()[:_MAX_TITLE]
    if not title:
        await _answer(message, "Название не может быть пустым. Напишите ещё раз.")
        return
    await state.update_data(title=title)
    await state.set_state(AddProduct.description)
    await _answer(
        message,
        "Описание товара — материал, для чего подойдёт, особенности.\n"
        "Его увидит клиент, и по нему же ищет бот.",
        skip_kb(),
    )


@router.callback_query(AddProduct.description, F.data == CB_SKIP)
async def add_description_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(description="")
    await state.set_state(AddProduct.category)
    await _edit(callback, "Категория товара:", categories_kb())
    await callback.answer()


@router.message(AddProduct.description, F.text)
async def add_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text.strip()[:_MAX_TEXT])
    await state.set_state(AddProduct.category)
    await _answer(message, "Категория товара — выберите или напишите свою:", categories_kb())


@router.callback_query(StateFilter(AddProduct.category, EditField.value),
                       F.data.startswith(f"{CB_CAT}:"))
async def choose_category(callback: CallbackQuery, state: FSMContext) -> None:
    index = int(callback.data.split(":")[-1])
    category = CATEGORIES[index] if 0 <= index < len(CATEGORIES) else ""
    data = await state.get_data()

    # Те же кнопки работают и в мастере, и при правке категории готового товара
    if "product_id" in data:
        await queries.update_product(data["product_id"], category=category)
        await state.clear()
        await _show_card(callback, data["product_id"])
        await callback.answer("Категория обновлена")
        return

    await state.update_data(category=category)
    await state.set_state(AddProduct.price)
    await _edit(
        callback, f"Цена в {_esc(config.SHOP_CURRENCY)}? Например: 2400", back_to_menu_kb()
    )
    await callback.answer()


@router.message(AddProduct.category, F.text)
async def add_category(message: Message, state: FSMContext) -> None:
    await state.update_data(category=message.text.strip()[:50])
    await state.set_state(AddProduct.price)
    await _answer(message, f"Цена в {_esc(config.SHOP_CURRENCY)}? Например: 2400")


@router.message(AddProduct.price, F.text)
async def add_price(message: Message, state: FSMContext) -> None:
    price = parse_price(message.text)
    if price is None:
        await _answer(message, "Не понял цену. Напишите числом, например: 2400")
        return

    data = await state.get_data()
    product_id = await queries.create_product(
        data["title"],
        price,
        description=data.get("description", ""),
        category=data.get("category", ""),
    )
    # Черновик: пока нет вариантов и фото, клиенту показывать нечего
    await queries.set_product_active(product_id, False)

    await state.update_data(product_id=product_id)
    await state.set_state(AddProduct.variants)
    await _answer(message, _VARIANTS_HINT)


@router.message(AddProduct.variants, F.text)
async def add_variants(message: Message, state: FSMContext) -> None:
    variants, errors = parse_variants(message.text)
    if not variants:
        await _answer(message, "Не разобрал ни одного варианта.\n\n" + _VARIANTS_HINT)
        return

    data = await state.get_data()
    product_id = data["product_id"]
    for variant in variants:
        await queries.add_variant(
            product_id, size=variant["size"], color=variant["color"], stock=variant["stock"]
        )

    reply = [f"Добавил вариантов: {len(variants)}."]
    if errors:
        reply.append("Не понял: " + _esc(", ".join(errors)))
    reply.append("\nТеперь пришлите фото товара — можно несколько подряд.")

    await state.set_state(AddProduct.photos)
    await _answer(message, "\n".join(reply), photo_upload_kb(product_id))


@router.message(StateFilter(AddProduct.photos, AddPhoto.waiting), F.photo)
async def receive_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = data["product_id"]
    # Берём последний размер: Telegram отдаёт варианты по возрастанию качества
    file_id = message.photo[-1].file_id

    file_name = await media.save_telegram_photo(message.bot, file_id, product_id)
    existing = await queries.get_photos(product_id)
    await queries.add_photo(
        product_id, tg_file_id=file_id, file_path=file_name, is_main=not existing
    )

    total = len(existing) + 1
    # Альбом из нескольких фото приходит отдельными апдейтами — отвечаем коротко,
    # чтобы на пять снимков не прилетело пять больших сообщений.
    await _answer(message, f"Фото сохранено ({total}).", photo_upload_kb(product_id))


@router.callback_query(F.data.startswith(f"{CB_DONE}:"))
async def photos_done(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[-1])
    await state.clear()
    product = await queries.get_product_full(product_id)
    if not product:
        await _edit(callback, "Товар не найден.", admin_main_menu())
        await callback.answer()
        return

    if product["is_active"]:
        await _show_photos(callback, product_id)
    else:
        await _edit(
            callback,
            _card_text(product) + "\n\nТовар готов. Опубликовать его в каталоге?",
            publish_kb(product_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_PUBLISH}:"))
async def publish_product(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[-1])
    await state.clear()
    await queries.set_product_active(product_id, True)
    await _show_card(callback, product_id)
    await callback.answer("Товар в продаже")


# ─────────────────────── Правка полей товара ───────────────────────

_FIELD_PROMPTS = {
    "title": "Новое название:",
    "description": "Новое описание:",
    "price": f"Новая цена в {html.escape(config.SHOP_CURRENCY, quote=False)}:",
    "category": "Новая категория — выберите или напишите свою:",
}


@router.callback_query(F.data.startswith(f"{CB_EDIT}:"))
async def edit_field_start(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, field, product_id = callback.data.split(":")
    await state.set_state(EditField.value)
    await state.update_data(product_id=int(product_id), field=field)

    markup = categories_kb() if field == "category" else back_to_menu_kb()
    await _edit(callback, _FIELD_PROMPTS[field] + "\n\n<i>/cancel — отменить</i>", markup)
    await callback.answer()


@router.message(EditField.value, F.text)
async def edit_field_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field, product_id = data["field"], data["product_id"]

    if field == "price":
        value = parse_price(message.text)
        if value is None:
            await _answer(message, "Не понял цену. Напишите числом, например: 2400")
            return
    else:
        limit = _MAX_TITLE if field == "title" else _MAX_TEXT
        value = message.text.strip()[:limit]
        if field == "title" and not value:
            await _answer(message, "Название не может быть пустым.")
            return

    await queries.update_product(product_id, **{field: value})
    await state.clear()

    product = await queries.get_product_full(product_id)
    await _answer(message, _card_text(product), product_card_kb(product))


# ─────────────────────── Остатки и варианты ───────────────────────


@router.callback_query(F.data.startswith(f"{CB_STOCK}:"))
async def show_stock(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_stock(callback, int(callback.data.split(":")[-1]))
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_VAR}:"))
async def variant_open(callback: CallbackQuery, state: FSMContext) -> None:
    variant_id = int(callback.data.split(":")[-1])
    variant = await queries.get_variant(variant_id)
    if not variant:
        await _edit(callback, "Вариант не найден.", admin_main_menu())
        await callback.answer()
        return

    await state.set_state(VariantStock.value)
    await state.update_data(variant_id=variant_id, product_id=variant["product_id"])
    await _edit(
        callback,
        f"<b>{_esc(variant['title'])}</b>\n{_esc(variant_label(variant))}\n"
        f"Сейчас на складе: {variant['stock']} шт.\n\n"
        "Напишите новое количество числом.",
        variant_kb(variant),
    )
    await callback.answer()


@router.message(VariantStock.value, F.text)
async def variant_set_stock(message: Message, state: FSMContext) -> None:
    if not message.text.strip().isdigit():
        await _answer(message, "Нужно число, например: 5")
        return

    data = await state.get_data()
    await queries.set_variant_stock(data["variant_id"], int(message.text.strip()))
    await state.clear()

    product_id = data["product_id"]
    variants = await queries.get_variants(product_id)
    product = await queries.get_product(product_id)
    await _answer(
        message,
        f"📦 Остатки: <b>{_esc(product['title'])}</b>\n\nОстаток обновлён.",
        variants_kb(product_id, variants),
    )


@router.callback_query(F.data.startswith(f"{CB_VAR_DEL}:"))
async def variant_delete(callback: CallbackQuery, state: FSMContext) -> None:
    variant_id = int(callback.data.split(":")[-1])
    variant = await queries.get_variant(variant_id)
    await state.clear()
    if not variant:
        await _edit(callback, "Вариант не найден.", admin_main_menu())
        await callback.answer()
        return

    await queries.delete_variant(variant_id)
    await _show_stock(callback, variant["product_id"])
    await callback.answer("Вариант удалён")


@router.callback_query(F.data.startswith(f"{CB_VAR_ADD}:"))
async def variant_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[-1])
    await state.set_state(AddVariant.value)
    await state.update_data(product_id=product_id)
    await _edit(callback, _VARIANTS_HINT + "\n\n<i>/cancel — отменить</i>", back_to_menu_kb())
    await callback.answer()


@router.message(AddVariant.value, F.text)
async def variant_add_value(message: Message, state: FSMContext) -> None:
    variants, errors = parse_variants(message.text)
    if not variants:
        await _answer(message, "Не разобрал ни одного варианта.\n\n" + _VARIANTS_HINT)
        return

    data = await state.get_data()
    product_id = data["product_id"]
    for variant in variants:
        await queries.add_variant(
            product_id, size=variant["size"], color=variant["color"], stock=variant["stock"]
        )
    await state.clear()

    reply = [f"Добавил вариантов: {len(variants)}."]
    if errors:
        reply.append("Не понял: " + _esc(", ".join(errors)))
    await _answer(
        message,
        "\n".join(reply),
        variants_kb(product_id, await queries.get_variants(product_id)),
    )


# ─────────────────────────── Фото ───────────────────────────


@router.callback_query(F.data.startswith(f"{CB_PHOTOS}:"))
async def show_photos(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    product_id = int(callback.data.split(":")[-1])
    photos = await queries.get_photos(product_id)
    await _send_album(callback.message, photos)
    await _show_photos(callback, product_id)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_PHOTO_ADD}:"))
async def photo_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[-1])
    await state.set_state(AddPhoto.waiting)
    await state.update_data(product_id=product_id)
    await _edit(
        callback,
        "Пришлите фото — можно несколько подряд.\n\n<i>/cancel — отменить</i>",
        photo_upload_kb(product_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_PHOTO_MAIN}:"))
async def photo_make_main(callback: CallbackQuery) -> None:
    photo_id = int(callback.data.split(":")[-1])
    product_id = await queries.set_photo_main(photo_id)
    if not product_id:
        await callback.answer("Фото уже удалено")
        return
    await _show_photos(callback, product_id)
    await callback.answer("Теперь это фото главное")


@router.callback_query(F.data.startswith(f"{CB_PHOTO_DEL}:"))
async def photo_delete(callback: CallbackQuery) -> None:
    photo_id = int(callback.data.split(":")[-1])
    photo = await queries.delete_photo(photo_id)
    if not photo:
        await callback.answer("Фото уже удалено")
        return

    media.remove_photo_file(photo["file_path"])
    # Главное фото удалили — назначаем главным первое из оставшихся, иначе
    # клиент увидит товар без картинки в карточке
    remaining = await queries.get_photos(photo["product_id"])
    if photo["is_main"] and remaining:
        await queries.set_photo_main(remaining[0]["id"])

    await _show_photos(callback, photo["product_id"])
    await callback.answer("Фото удалено")


# ─────────────────────── Скрыть / удалить товар ───────────────────────


@router.callback_query(F.data.startswith(f"{CB_TOGGLE}:"))
async def toggle_product(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[-1])
    product = await queries.get_product(product_id)
    if not product:
        await callback.answer("Товар не найден")
        return

    new_state = not product["is_active"]
    await queries.set_product_active(product_id, new_state)
    await _show_card(callback, product_id)
    await callback.answer("Товар в продаже" if new_state else "Товар скрыт от клиентов")


@router.callback_query(F.data.startswith(f"{CB_DEL}:"))
async def delete_ask(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[-1])
    product = await queries.get_product(product_id)
    if not product:
        await callback.answer("Товар не найден")
        return
    await _edit(
        callback,
        f"Удалить <b>{_esc(product['title'])}</b> вместе с вариантами и фото?\n\n"
        "В уже оформленных заказах позиция сохранится.\n"
        "Если товар просто закончился — лучше скрыть, а не удалять.",
        confirm_delete_kb(product_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_DEL_OK}:"))
async def delete_confirm(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[-1])
    # Записи о фото уйдут каскадом, а файлы на диске надо убрать руками
    for photo in await queries.get_photos(product_id):
        media.remove_photo_file(photo["file_path"])
    await queries.delete_product(product_id)

    await _show_list(callback, 0)
    await callback.answer("Товар удалён")
