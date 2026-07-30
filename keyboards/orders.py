"""Клавиатуры корзины, оформления заказа и оплаты.

Префиксы callback_data разведены с админкой каталога (там всё начинается с
`ap:`), потому что роутер админки подключён раньше и фильтрует апдейты по
ADMIN_ID: одинаковый префикс означал бы, что владелец магазина не может
пользоваться ботом как покупатель.

`c:` — корзина, `o:` — оформление и оплата у клиента, `oa:` — решение
владельца по заказу. Последнее обрабатывается только в handlers/admin.py под
фильтром ADMIN_ID: подтвердить оплату чужим callback'ом быть не должно.
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.format import money, variant_label

# --- Корзина ---
CB_CART = "c:show"         # показать корзину
CB_INC = "c:inc"           # c:inc:<variant_id>
CB_DEC = "c:dec"           # c:dec:<variant_id>
CB_DEL = "c:del"           # c:del:<variant_id>
CB_CLEAR = "c:clear"       # очистить корзину
CB_CLEAR_OK = "c:clearok"  # подтверждённая очистка
CB_PICK = "c:pick"         # c:pick:<product_id> — выбрать вариант с карточки
CB_ADD = "c:add"           # c:add:<variant_id> — положить 1 шт

# --- Оформление и оплата ---
CB_CHECKOUT = "o:start"    # начать оформление
CB_KEEP = "o:keep"         # оставить значение из профиля
CB_KEEP_ALL = "o:keepall"  # данные доставки с прошлого заказа подходят целиком
CB_SKIP = "o:skip"         # пропустить необязательный шаг (комментарий)
CB_CONFIRM = "o:ok"        # подтвердить сводку и создать заказ
CB_RESTART = "o:again"     # заполнить данные заново
CB_CANCEL = "o:cancel"     # выйти из оформления
CB_PAID = "o:paid"         # o:paid:<order_id> — «Я оплатил»
CB_ORDERS = "o:list"       # мои заказы
CB_ORD_CANCEL = "o:ocan"      # o:ocan:<order_id> — клиент просит отменить заказ
CB_ORD_CANCEL_OK = "o:ocanok"  # o:ocanok:<order_id> — подтверждённая отмена

# Пока накладной нет, посылка ещё не уехала — заказ можно отменить и вернуть
# товар на витрину. После отправки отменяет уже только владелец.
CANCELLABLE_STATUSES = ("new", "awaiting_payment", "paid_claimed", "confirmed")


def can_client_cancel(order: dict) -> bool:
    """Может ли покупатель отменить этот заказ сам."""
    return order["status"] in CANCELLABLE_STATUSES and not (order.get("ttn") or "")

# --- Решение владельца по заказу ---
CB_ADMIN_OK = "oa:ok"      # oa:ok:<order_id> — подтвердить оплату
CB_ADMIN_NO = "oa:no"      # oa:no:<order_id> — отклонить


def cart_kb(cart: dict) -> InlineKeyboardMarkup:
    """Корзина: по строке на позицию (−, количество, +, удалить) и действия.

    Номер позиции в кнопках совпадает с номером в тексте сообщения — иначе в
    корзине из трёх товаров не понять, к какому относится ряд кнопок.
    """
    kb = InlineKeyboardBuilder()
    rows: list[int] = []
    for number, item in enumerate(cart["items"], 1):
        variant_id = item["variant_id"]
        kb.button(text="➖", callback_data=f"{CB_DEC}:{variant_id}")
        kb.button(text=f"{number}. {item['qty']} шт", callback_data=f"{CB_CART}")
        kb.button(text="➕", callback_data=f"{CB_INC}:{variant_id}")
        kb.button(text="🗑", callback_data=f"{CB_DEL}:{variant_id}")
        rows.append(4)

    kb.button(text="✅ Оформить заказ", callback_data=CB_CHECKOUT)
    kb.button(text="🧹 Очистить корзину", callback_data=CB_CLEAR)
    kb.adjust(*rows, 1, 1)
    return kb.as_markup()


def clear_confirm_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🧹 Да, очистить", callback_data=CB_CLEAR_OK)
    kb.button(text="Отмена", callback_data=CB_CART)
    kb.adjust(1)
    return kb.as_markup()


def added_kb() -> InlineKeyboardMarkup:
    """После добавления товара: сразу в корзину или сразу оформлять."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🧺 Корзина", callback_data=CB_CART)
    kb.button(text="✅ Оформить заказ", callback_data=CB_CHECKOUT)
    kb.adjust(2)
    return kb.as_markup()


def product_card_kb(product: dict) -> InlineKeyboardMarkup | None:
    """Кнопки под карточкой товара. None — если купить нечего.

    Один доступный вариант кладётся в корзину сразу; когда их несколько,
    кнопка ведёт к выбору размера и цвета — за клиента их выбирать нельзя.
    """
    in_stock = [v for v in product.get("variants", []) if v.get("stock", 0) > 0]
    if not in_stock:
        return None

    kb = InlineKeyboardBuilder()
    if len(in_stock) == 1:
        kb.button(text="🛒 В корзину", callback_data=f"{CB_ADD}:{in_stock[0]['id']}")
    else:
        kb.button(text="🛒 В корзину", callback_data=f"{CB_PICK}:{product['id']}")
    kb.button(text="🧺 Корзина", callback_data=CB_CART)
    kb.adjust(2)
    return kb.as_markup()


def variants_pick_kb(variants: list[dict]) -> InlineKeyboardMarkup:
    """Выбор конкретного варианта товара перед добавлением в корзину."""
    kb = InlineKeyboardBuilder()
    for variant in variants:
        kb.button(
            text=f"{variant_label(variant)} — {variant['stock']} шт",
            callback_data=f"{CB_ADD}:{variant['id']}",
        )
    kb.adjust(1)
    return kb.as_markup()


def step_kb(keep: str | None = None, *, skip: bool = False) -> InlineKeyboardMarkup:
    """Кнопки шага оформления: подставить известное, пропустить, отменить.

    `keep` — значение из профиля клиента: имя, город и отделение бот уже мог
    узнать в разговоре, и заставлять вводить их заново — лишний шаг к отказу.
    """
    kb = InlineKeyboardBuilder()
    if keep:
        shown = keep if len(keep) <= 30 else keep[:29] + "…"
        kb.button(text=f"✅ Оставить: {shown}", callback_data=CB_KEEP)
    if skip:
        kb.button(text="Пропустить", callback_data=CB_SKIP)
    kb.button(text="❌ Отменить оформление", callback_data=CB_CANCEL)
    kb.adjust(1)
    return kb.as_markup()


def saved_data_kb() -> InlineKeyboardMarkup:
    """Повторный заказ: оставить прошлые данные или поменять. Всё.

    Показывается вместо всей формы, когда бот уже знает получателя, телефон,
    город и отделение. Кнопок ровно две и третьей быть не должно: смысл экрана
    в том, что постоянный покупатель оформляет заказ одним нажатием, а не
    подтверждает по очереди то, что и так не менялось.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Оставить эти данные", callback_data=CB_KEEP_ALL)
    kb.button(text="✏️ Поменять данные", callback_data=CB_RESTART)
    kb.adjust(1)
    return kb.as_markup()


def phone_kb() -> ReplyKeyboardMarkup:
    """Нижняя клавиатура шага телефона: номер отдаёт сам Telegram.

    one_time_keyboard — чтобы после нажатия она свернулась и не мешала: на
    следующем шаге клиент вводит город текстом.
    """
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Или напишите номер вручную",
    )


def summary_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Всё верно, оформить", callback_data=CB_CONFIRM)
    kb.button(text="✏️ Ввести данные заново", callback_data=CB_RESTART)
    kb.button(text="❌ Отменить", callback_data=CB_CANCEL)
    kb.adjust(1)
    return kb.as_markup()


def payment_kb(order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Я оплатил", callback_data=f"{CB_PAID}:{order_id}")
    kb.button(text="❌ Отменить заказ", callback_data=f"{CB_ORD_CANCEL}:{order_id}")
    kb.adjust(1)
    return kb.as_markup()


def order_cancel_confirm_kb(order_id: int) -> InlineKeyboardMarkup:
    """Переспрос перед отменой: тап по «Отменить» бывает случайным."""
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Да, отменить заказ",
              callback_data=f"{CB_ORD_CANCEL_OK}:{order_id}")
    kb.button(text="Нет, оставить", callback_data=CB_ORDERS)
    kb.adjust(1)
    return kb.as_markup()


def orders_kb(orders: list[dict]) -> InlineKeyboardMarkup | None:
    """Кнопки под списком заказов: оплатить и отменить.

    «Я оплатил» нужна, потому что сообщение с реквизитами теряется в переписке.
    «Отменить» — потому что до отправки посылки передумать имеет право клиент,
    а не только владелец: иначе товар висит списанным до таймаута.
    """
    waiting = [o for o in orders if o["status"] == "awaiting_payment"]
    cancellable = [o for o in orders if can_client_cancel(o)]
    if not waiting and not cancellable:
        return None

    kb = InlineKeyboardBuilder()
    for order in waiting:
        kb.button(
            text=f"💳 Оплатил заказ №{order['id']} — {money(order['total'])}",
            callback_data=f"{CB_PAID}:{order['id']}",
        )
    for order in cancellable:
        kb.button(
            text=f"❌ Отменить заказ №{order['id']}",
            callback_data=f"{CB_ORD_CANCEL}:{order['id']}",
        )
    kb.adjust(1)
    return kb.as_markup()


def admin_order_kb(order_id: int) -> InlineKeyboardMarkup:
    """Заявка владельцу: подтвердить оплату или отклонить заказ."""
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Оплата пришла", callback_data=f"{CB_ADMIN_OK}:{order_id}")
    kb.button(text="❌ Отклонить", callback_data=f"{CB_ADMIN_NO}:{order_id}")
    kb.adjust(2)
    return kb.as_markup()
