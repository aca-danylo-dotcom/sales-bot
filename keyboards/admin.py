"""Клавиатуры админки каталога (inline). Управление на кнопках, без ИИ.

callback_data держим короткими: Telegram отводит на них 64 байта, а в конец
почти всегда дописывается id товара, варианта или страницы.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

CB_MENU = "ap:menu"          # главное меню админки
CB_ADD = "ap:add"            # начать добавление товара
CB_LIST = "ap:list"          # список товаров: ap:list:<page>
CB_CARD = "ap:card"          # карточка товара: ap:card:<product_id>
CB_EDIT = "ap:edit"          # правка поля: ap:edit:<field>:<product_id>
CB_STOCK = "ap:stock"        # остатки товара: ap:stock:<product_id>
CB_VAR = "ap:var"            # вариант: ap:var:<variant_id>
CB_VAR_DEL = "ap:vardel"     # удалить вариант: ap:vardel:<variant_id>
CB_VAR_ADD = "ap:varadd"     # добавить вариант: ap:varadd:<product_id>
CB_PHOTOS = "ap:photos"      # фото товара: ap:photos:<product_id>
CB_PHOTO_ADD = "ap:phadd"    # загрузить фото: ap:phadd:<product_id>
CB_PHOTO_DEL = "ap:phdel"    # удалить фото: ap:phdel:<photo_id>
CB_PHOTO_MAIN = "ap:phmain"  # сделать главным: ap:phmain:<photo_id>
CB_TOGGLE = "ap:toggle"      # скрыть/показать: ap:toggle:<product_id>
CB_DEL = "ap:del"            # спросить об удалении: ap:del:<product_id>
CB_DEL_OK = "ap:delok"       # подтверждённое удаление: ap:delok:<product_id>
CB_CAT = "ap:cat"            # выбор категории при добавлении: ap:cat:<индекс>
CB_SKIP = "ap:skip"          # пропустить необязательный шаг
CB_DONE = "ap:done"          # завершить загрузку фото: ap:done:<product_id>
CB_PUBLISH = "ap:pub"        # опубликовать черновик: ap:pub:<product_id>

# Готовые категории для кнопок. Свою можно ввести текстом — список не жёсткий.
CATEGORIES = ["одежда", "обувь", "экипировка"]


def admin_main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить товар", callback_data=CB_ADD)
    kb.button(text="📋 Каталог", callback_data=f"{CB_LIST}:0")
    kb.adjust(1)
    return kb.as_markup()


def back_to_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В меню", callback_data=CB_MENU)
    return kb.as_markup()


def skip_kb() -> InlineKeyboardMarkup:
    """Для необязательного шага (описание)."""
    kb = InlineKeyboardBuilder()
    kb.button(text="Пропустить", callback_data=CB_SKIP)
    return kb.as_markup()


def categories_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for index, name in enumerate(CATEGORIES):
        kb.button(text=name.capitalize(), callback_data=f"{CB_CAT}:{index}")
    kb.adjust(1)
    return kb.as_markup()


def products_kb(
    products: list[dict], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """Страница списка товаров. В подписи — цена, остаток и метка скрытого."""
    kb = InlineKeyboardBuilder()
    for product in products:
        mark = "" if product["is_active"] else "🚫 "
        stock = product.get("total_stock", 0)
        kb.button(
            text=f"{mark}{product['title']} · {product['price']:g} · {stock} шт",
            callback_data=f"{CB_CARD}:{product['id']}",
        )
    kb.adjust(1)

    nav = []
    if page > 0:
        nav.append(("◀️", f"{CB_LIST}:{page - 1}"))
    if total_pages > 1:
        nav.append((f"{page + 1}/{total_pages}", f"{CB_LIST}:{page}"))
    if page + 1 < total_pages:
        nav.append(("▶️", f"{CB_LIST}:{page + 1}"))
    for text, data in nav:
        kb.button(text=text, callback_data=data)
    if nav:
        kb.adjust(*([1] * len(products)), len(nav))

    kb.row()
    kb.button(text="⬅️ В меню", callback_data=CB_MENU)
    return kb.as_markup()


def product_card_kb(product: dict, page: int = 0) -> InlineKeyboardMarkup:
    """Кнопки карточки товара. У черновика вместо «скрыть» — «опубликовать»."""
    product_id = product["id"]
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Название", callback_data=f"{CB_EDIT}:title:{product_id}")
    kb.button(text="✏️ Описание", callback_data=f"{CB_EDIT}:description:{product_id}")
    kb.button(text="💰 Цена", callback_data=f"{CB_EDIT}:price:{product_id}")
    kb.button(text="🏷 Категория", callback_data=f"{CB_EDIT}:category:{product_id}")
    kb.button(text="📦 Остатки", callback_data=f"{CB_STOCK}:{product_id}")
    kb.button(text="🖼 Фото", callback_data=f"{CB_PHOTOS}:{product_id}")
    if product["is_active"]:
        kb.button(text="🚫 Скрыть", callback_data=f"{CB_TOGGLE}:{product_id}")
    else:
        kb.button(text="✅ В продажу", callback_data=f"{CB_TOGGLE}:{product_id}")
    kb.button(text="🗑 Удалить", callback_data=f"{CB_DEL}:{product_id}")
    kb.button(text="⬅️ К списку", callback_data=f"{CB_LIST}:{page}")
    kb.adjust(2, 2, 2, 2, 1)
    return kb.as_markup()


def variants_kb(product_id: int, variants: list[dict]) -> InlineKeyboardMarkup:
    """Список вариантов: нажатие — правка остатка."""
    kb = InlineKeyboardBuilder()
    for variant in variants:
        kb.button(
            text=f"{variant_label(variant)} — {variant['stock']} шт",
            callback_data=f"{CB_VAR}:{variant['id']}",
        )
    kb.button(text="➕ Добавить вариант", callback_data=f"{CB_VAR_ADD}:{product_id}")
    kb.button(text="⬅️ К товару", callback_data=f"{CB_CARD}:{product_id}")
    kb.adjust(1)
    return kb.as_markup()


def variant_kb(variant: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Удалить вариант", callback_data=f"{CB_VAR_DEL}:{variant['id']}")
    kb.button(text="⬅️ К остаткам", callback_data=f"{CB_STOCK}:{variant['product_id']}")
    kb.adjust(1)
    return kb.as_markup()


def photos_kb(product_id: int, photos: list[dict]) -> InlineKeyboardMarkup:
    """Управление фото: сделать главным / удалить, по строке на снимок."""
    kb = InlineKeyboardBuilder()
    for number, photo in enumerate(photos, 1):
        main = "⭐ " if photo["is_main"] else ""
        kb.button(text=f"{main}Фото {number}", callback_data=f"{CB_PHOTO_MAIN}:{photo['id']}")
        kb.button(text="🗑", callback_data=f"{CB_PHOTO_DEL}:{photo['id']}")
    kb.adjust(2)
    kb.row()
    kb.button(text="📷 Загрузить фото", callback_data=f"{CB_PHOTO_ADD}:{product_id}")
    kb.button(text="⬅️ К товару", callback_data=f"{CB_CARD}:{product_id}")
    kb.adjust(2, *([2] * len(photos)), 1, 1)
    return kb.as_markup()


def photo_upload_kb(product_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Готово", callback_data=f"{CB_DONE}:{product_id}")
    return kb.as_markup()


def publish_kb(product_id: int) -> InlineKeyboardMarkup:
    """Финал добавления: опубликовать сейчас или оставить черновиком."""
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Опубликовать", callback_data=f"{CB_PUBLISH}:{product_id}")
    kb.button(text="🖼 Ещё фото", callback_data=f"{CB_PHOTO_ADD}:{product_id}")
    kb.button(text="📋 Оставить черновиком", callback_data=f"{CB_CARD}:{product_id}")
    kb.adjust(1)
    return kb.as_markup()


def confirm_delete_kb(product_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Да, удалить", callback_data=f"{CB_DEL_OK}:{product_id}")
    kb.button(text="Отмена", callback_data=f"{CB_CARD}:{product_id}")
    kb.adjust(1)
    return kb.as_markup()


def variant_label(variant: dict) -> str:
    """«12 oz / чёрный», «42», «чёрный» или «без вариантов» — что заполнено."""
    parts = [part for part in (variant["size"], variant["color"]) if part]
    return " / ".join(parts) if parts else "без вариантов"
