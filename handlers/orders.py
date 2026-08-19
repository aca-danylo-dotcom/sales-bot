"""Корзина, оформление заказа и оплата.

Роутер подключается МЕЖДУ админкой и клиентским контуром: клиентский ловит
свободный текст целиком и иначе перехватил бы и кнопки корзины, и ответы на
шаги оформления.

Почему оформление — детерминированный FSM, а не работа ИИ: имя, телефон,
город и отделение попадают в заказ, по которому поедет посылка. Модель может
переспросить, перепутать или «додумать» адрес; здесь же каждый шаг проверяется
кодом. ИИ доводит клиента до корзины, дальше ведёт форма.

Остатки списываются в момент создания заказа (`queries.create_order`), поэтому
любая отмена — клиентом, владельцем или по таймауту — обязана вернуть их через
`queries.cancel_order`.
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import config
from db import queries
from keyboards.menus import (
    BTN_CART,
    BTN_CATALOG,
    BTN_HELP,
    BTN_ORDERS,
    asks_for,
    main_menu,
)
from keyboards.orders import (
    CB_ADD,
    CB_CANCEL,
    CB_CART,
    CB_CHECKOUT,
    CB_CLEAR,
    CB_CLEAR_OK,
    CB_CONFIRM,
    CB_DEC,
    CB_DEL,
    CB_INC,
    CB_KEEP,
    CB_KEEP_ALL,
    CB_ORD_CANCEL,
    CB_ORD_CANCEL_OK,
    CB_ORDERS,
    CB_PAID,
    CB_PICK,
    CB_RESTART,
    CB_SKIP,
    admin_order_kb,
    added_kb,
    can_client_cancel,
    cart_kb,
    clear_confirm_kb,
    order_cancel_confirm_kb,
    orders_kb,
    payment_kb,
    phone_kb,
    saved_data_kb,
    step_kb,
    summary_kb,
    variants_pick_kb,
)
from services import agent_stats, mail, payments
from services.format import (
    ORDER_STATUS_RU,
    clean_phone,
    looks_like_branch,
    looks_like_name,
    money,
    variant_label,
)

logger = logging.getLogger(__name__)
router = Router(name="orders")

_HTML = "HTML"

# Просьбы открыть раздел: «корзина», «мои заказы», «каталог». Во время
# оформления они приходят обычным текстом и иначе записались бы в имя или
# город получателя — человек хотел уйти в корзину, а уехал бы в накладную.
_SECTION_WORDS = (BTN_CATALOG, BTN_CART, BTN_ORDERS, BTN_HELP)


def _asks_for_section(text: str | None) -> bool:
    return any(asks_for(text, word) for word in _SECTION_WORDS)

# Ограничения полей заказа. Верхние границы — чтобы в накладную Новой Почты не
# уехала «простыня», нижние — чтобы не приняли пустую строку из одного символа.
_LIMITS = {
    "name": (2, 60),
    "city": (2, 60),
    "np_branch": (1, 100),
    "comment": (0, 300),
    "email": (0, 120),
}

_MAX_ORDERS_SHOWN = 5

# Статусы, в которых заказ владельцу в Telegram ещё не показывали: заявка уходит
# только после того, как клиент нажал «Я оплатил». Пока заказ здесь, событий по
# нему владельцу не шлём — он их не поймёт, заказ он в глаза не видел.
_UNSEEN_BY_ADMIN = ("new", "awaiting_payment")

# Сколько раз переспрашиваем имя, прежде чем принять как есть: проверка не знает
# всех имён на свете, и упереться на первом шаге хуже, чем странное имя в заказе.
_MAX_NAME_ATTEMPTS = 3


def _esc(value: object) -> str:
    return html.escape(str(value), quote=False)


# Тот же разбор номера, что и у мини-приложения (см. services/format.py):
# заказ из чата и заказ из витрины должны попадать в базу одинаковыми.
_clean_phone = clean_phone


class Checkout(StatesGroup):
    """Шаги оформления. Порядок задаёт _NEXT_FIELD, а не сами состояния."""

    saved = State()   # показали данные с прошлого заказа, ждём «Всё верно»
    name = State()
    phone = State()
    city = State()
    np_branch = State()
    comment = State()
    email = State()
    confirm = State()


_STATE_BY_FIELD = {
    "name": Checkout.name,
    "phone": Checkout.phone,
    "city": Checkout.city,
    "np_branch": Checkout.np_branch,
    "comment": Checkout.comment,
    "email": Checkout.email,
}

_NEXT_FIELD = {
    "name": "phone",
    "phone": "city",
    "city": "np_branch",
    "np_branch": "comment",
    "comment": "email",
    "email": None,        # дальше — сводка заказа
}

_PROMPTS = {
    "name": "На чьё имя оформляем? Напишите имя и фамилию получателя.",
    "phone": ("Нужен номер телефона — по нему свяжется курьер.\n"
              "Нажмите кнопку ниже или напишите номер вручную."),
    "city": "В какой город доставляем?",
    "np_branch": ("Номер отделения Новой Почты — например «12» или «Поштомат 4521».\n"
                  "Можно дописать улицу, но номер нужен обязательно."),
    "comment": "Комментарий к заказу — если есть. Или нажмите «Пропустить».",
    # Почта — единственный необязательный контакт, и сказано об этом прямо.
    # Доставку она ни на что не меняет: адрес и телефон уже собраны выше, а
    # заказ ведётся здесь, в чате. Нужна она только для напоминаний, и человек
    # должен видеть, что «Пропустить» — нормальный ответ, а не отказ от заказа.
    "email": ("Почта — если хотите получать напоминания и промокоды ещё и на неё.\n"
              "Это по желанию: нажмите «Пропустить», и всё останется здесь, в чате."),
}

# Шаги, которые можно пропустить кнопкой. Комментарий к заказу и почта — оба
# необязательные, и оба не мешают собрать посылку.
_SKIPPABLE = ("comment", "email")

# Поля, которые бот мог узнать раньше: их подставляем кнопкой «Оставить».
# Телефон сюда тоже входит — он остаётся с прошлого заказа.
_PROFILE_FIELDS = ("name", "phone", "city", "np_branch")


# ─────────────────────────── Тексты ───────────────────────────


def cart_text(cart: dict) -> str:
    """Корзина списком с итогом. Отдельно помечает то, что разобрали."""
    if not cart["items"]:
        return ("🧺 Корзина пуста.\n\n"
                "Напишите, что ищете — подберу и положу в корзину.")

    lines = ["<b>🧺 Ваша корзина</b>", ""]
    for number, item in enumerate(cart["items"], 1):
        lines.append(f"{number}. {_esc(item['title'])} — {_esc(variant_label(item))}")
        lines.append(f"    {item['qty']} × {money(item['price'])} = {money(item['sum'])}")
        if not item["is_active"] or item["stock"] < item["qty"]:
            left = item["stock"] if item["is_active"] else 0
            lines.append(f"    ⚠️ доступно только {left} шт — уменьшите количество")
    lines += ["", f"<b>Итого: {money(cart['total'])}</b>"]
    return "\n".join(lines)


def order_items_text(order: dict) -> str:
    lines = []
    for number, item in enumerate(order["items"], 1):
        label = " / ".join(p for p in (item["size"], item["color"]) if p) or "один вариант"
        lines.append(
            f"{number}. {_esc(item['title_snapshot'])} — {_esc(label)}\n"
            f"    {item['qty']} × {money(item['price_snapshot'])} = {money(item['sum'])}"
        )
    return "\n".join(lines)


# Слова, которые клиент слышит о своём заказе. Живут здесь, а не по месту
# нажатия, потому что решение по одному и тому же заказу принимают из двух мест:
# кнопкой под пушем в Telegram (handlers/admin.py) и из веб-CRM (web/api/orders.py).
# Разойдись формулировки — клиент получал бы разные письма за одно и то же.


def client_confirmed_text(order: dict) -> str:
    """Оплата подтверждена, заказ пошёл в сборку."""
    return (
        f"✅ Оплата по заказу №{order['id']} получена, спасибо!\n\n"
        f"{order_items_text(order)}\n\n"
        f"Собираем заказ и отправляем Новой Почтой — номер накладной пришлём сюда же."
    )


def client_shipped_text(order: dict, ttn: str) -> str:
    """Посылка уехала. Накладная отдельной строкой — её копируют в трекер."""
    return (
        f"🚚 Заказ №{order['id']} отправлен Новой Почтой.\n\n"
        f"Накладная: <code>{_esc(ttn)}</code>\n\n"
        f"Отследить посылку можно по этому номеру в приложении или на сайте "
        f"Новой Почты. Спасибо за покупку!"
    )


def client_payment_missing_text(order: dict) -> str:
    """Оплату не нашли — заказ отменён, товар вернулся на витрину."""
    return (
        f"К сожалению, оплату по заказу №{order['id']} на {money(order['total'])} "
        f"мы не нашли, поэтому заказ отменён — товар вернулся на витрину.\n\n"
        f"Если оплата всё-таки прошла, напишите сюда: разберёмся и оформим заново 🙏"
    )


def client_cancelled_text(order: dict) -> str:
    """Отмена по решению менеджера. Причину пишем внутрь заказа, а не клиенту:
    в заметке бывает «дозвониться не смогли, товар битый» — это не текст письма."""
    return (
        f"Заказ №{order['id']} отменён, товар вернулся на витрину.\n\n"
        f"Если это недоразумение — напишите сюда, оформим заново 🙏"
    )


def _discount_line(order: dict) -> str:
    """Строка про сработавшую скидку — пустая, если промокода не было.

    Показывать её обязательно: `orders.total` уже со скидкой, и без пояснения
    сумма к оплате просто не сходится с ценами позиций выше.
    """
    discount = order.get("discount") or 0
    if not discount:
        return ""
    code = order.get("promo_code") or ""
    return f"Скидка по промокоду {_esc(code)}: −{money(discount)}\n"


def payment_text(order: dict) -> str:
    """Реквизиты для оплаты. Берутся из config и в промпт ИИ не попадают."""
    return (
        f"<b>Заказ №{order['id']} оформлен.</b>\n\n"
        f"{_discount_line(order)}"
        f"К оплате: <b>{money(order['total'])}</b>\n\n"
        f"Карта: <code>{_esc(config.PAYMENT_CARD)}</code>\n"
        f"Получатель: {_esc(config.PAYMENT_CARD_HOLDER)}\n\n"
        f"После оплаты нажмите «Я оплатил» — мы проверим поступление и "
        f"подтвердим заказ.\n"
        f"Заказ ждёт оплаты {config.ORDER_PAYMENT_TIMEOUT_HOURS} ч, "
        f"потом отменяется автоматически и товар возвращается на витрину."
    )


def admin_order_text(
    order: dict, username: str | None = None, *, head: tuple[str, str] | None = None
) -> str:
    """Пуш владельцу об оплате: состав, сумма, получатель, контакт клиента.

    Единственное сообщение по заказу — по нему владелец и принимает решение,
    поэтому в нём сразу всё: лезть за составом и адресом в переписку выше он не
    должен.

    `head` — заголовок и строка под ним. Меняется он потому, что об оплате
    сообщают двое: клиент кнопкой «Я оплатил» (поступление надо сверить) и
    платёжная система Telegram (сверять нечего, деньги уже пришли). Всё
    остальное — состав, сумма, адрес — в обоих случаях одно и то же, и
    расходиться этим текстам нельзя.
    """
    contact = f"@{username}" if username else f"id {order['client_id']}"
    title, subtitle = head or (
        f"💳 <b>Клиент оплатил заказ №{order['id']}</b>",
        "Проверьте поступление и подтвердите.",
    )
    lines = [
        title,
        subtitle,
        "",
        order_items_text(order),
        "",
        # Скидка отдельной строкой: владелец сверяет поступление на карту с
        # суммой заказа, и «пришло меньше» должно объясняться прямо здесь.
        *( [f"Скидка по промокоду {_esc(order.get('promo_code') or '')}: "
            f"−{money(order.get('discount') or 0)}"] if order.get("discount") else []),
        f"<b>Сумма: {money(order['total'])}</b>",
        "",
        f"Получатель: {_esc(order['name'])}",
        f"Телефон: {_esc(order['phone'])}",
        f"Доставка: {_esc(order['city'])}, {_esc(order['np_branch'])}",
    ]
    if order["comment"]:
        lines.append(f"Комментарий: {_esc(order['comment'])}")
    lines += ["", f"Клиент: {_esc(contact)}"]
    return "\n".join(lines)


def _summary_text(cart: dict, data: dict, *, tail: str = "",
                  promo: dict | None = None) -> str:
    """Заказ целиком: состав, сумма, куда и кому везём.

    Один и тот же текст показывается в конце формы и вместо неё — постоянному
    покупателю с прошлыми данными. Меняется только последняя строка: в форме
    остаётся подтвердить, а на повторном заказе — решить, менять ли данные.

    Скидка показывается ЗДЕСЬ же, а не только в счёте: человек с промокодом
    ищет глазами, применился он или нет, и «узнаете после оформления» — худший
    из возможных ответов.
    """
    lines = ["<b>Проверьте заказ</b>", ""]
    for number, item in enumerate(cart["items"], 1):
        lines.append(f"{number}. {_esc(item['title'])} — {_esc(variant_label(item))}")
        lines.append(f"    {item['qty']} × {money(item['price'])} = {money(item['sum'])}")

    if promo:
        # Считаем ровно так же, как queries.create_order, — иначе обещанная
        # здесь сумма разойдётся со счётом на копейку, и объяснить это нечем.
        discount = round(cart["total"] * promo["percent"] / 100, 2)
        lines += [
            "",
            f"Сумма: {money(cart['total'])}",
            f"Промокод {_esc(promo['code'])} (−{promo['percent']}%): −{money(discount)}",
        ]
        total_text = money(round(cart["total"] - discount, 2))
    else:
        total_text = money(cart["total"])

    lines += [
        "",
        f"<b>Итого: {total_text}</b>",
        "",
        f"Получатель: {_esc(data.get('name', ''))}",
        f"Телефон: {_esc(data.get('phone', ''))}",
        "",
        # Адрес отдельным блоком и жирным: именно его чаще всего и не замечают
        # в общем списке, а потом посылка уезжает не в то отделение.
        "<b>📦 Куда везём:</b>",
        f"<b>{_esc(data.get('city', ''))}, отделение "
        f"{_esc(data.get('np_branch', ''))}</b>",
    ]
    if data.get("comment"):
        lines.append("")
        lines.append(f"Комментарий: {_esc(data['comment'])}")
    lines += ["", tail or "Всё верно? Если адрес не тот — «Ввести данные заново»."]
    return "\n".join(lines)


# Хвост экрана повторного заказа. Формы здесь не будет ни в каком случае:
# «Оставить эти данные» сразу оформляет заказ.
_SAVED_TAIL = ("Это данные вашего прошлого заказа. Оставляем их — и оформляю "
               "заказ. Что-то поменялось — «Поменять данные».")


# ─────────────────────────── Отправка ───────────────────────────


async def _edit_or_send(callback: CallbackQuery, text: str, markup=None) -> None:
    """Переписывает сообщение под кнопкой или шлёт новое.

    Новое приходится слать, когда кнопка висела под фотографией товара
    (у сообщения с фото нет текста) или Telegram отказал в правке.
    """
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode=_HTML)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup, parse_mode=_HTML)


async def notify_client(bot: Bot, client_id: int, text: str, markup=None) -> bool:
    """Сообщение клиенту от имени бота. False — клиент недоступен.

    Заблокированный бот не должен ронять подтверждение оплаты: заказ уже
    подтверждён, а владелец увидит, что уведомление не дошло.
    """
    try:
        await bot.send_message(client_id, text, reply_markup=markup, parse_mode=_HTML)
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        logger.warning("Не удалось уведомить клиента %s", client_id)
        return False


async def notify_admin_order(bot: Bot, order: dict, username: str | None) -> None:
    """Заявка владельцу: клиент говорит, что оплатил. Ошибку глушим — заказ создан.

    Единственный пуш по заказу за всю его жизнь. Про оформление владельцу не
    сообщаем намеренно: пока клиент не нажал «Я оплатил», решать нечего, а
    сообщения о заказах, которые ещё никто не оплатил, только копятся в чате.
    Оформленные заказы видны в веб-CRM, там же подтверждается оплата, если
    деньги пришли, а кнопку клиент нажать забыл.

    Повторное решение по тому же заказу отсекается проверкой статуса в
    handlers/admin.py.
    """
    try:
        await bot.send_message(
            config.ADMIN_ID,
            admin_order_text(order, username),
            reply_markup=admin_order_kb(order["id"]),
            parse_mode=_HTML,
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        logger.exception("Не удалось отправить заявку по заказу %s владельцу", order["id"])


# ─────────────────────────── Корзина ───────────────────────────


@router.message(StateFilter(None), F.text.func(lambda t: asks_for(t, BTN_CART)))
async def show_cart_message(message: Message) -> None:
    await queries.ensure_client(message.from_user.id)
    cart = await queries.get_cart(message.from_user.id)
    await message.answer(
        cart_text(cart),
        reply_markup=cart_kb(cart) if cart["items"] else None,
        parse_mode=_HTML,
    )


@router.callback_query(F.data == CB_CART)
async def show_cart_callback(callback: CallbackQuery) -> None:
    cart = await queries.get_cart(callback.from_user.id)
    await _edit_or_send(
        callback, cart_text(cart), cart_kb(cart) if cart["items"] else None
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_PICK}:"))
async def pick_variant(callback: CallbackQuery) -> None:
    """Товар с несколькими вариантами: сначала выбор размера и цвета."""
    product_id = int(callback.data.split(":")[-1])
    product = await queries.get_product_full(product_id)
    if not product or not product["is_active"]:
        await callback.answer("Товар больше не продаётся", show_alert=True)
        return

    in_stock = [v for v in product["variants"] if v["stock"] > 0]
    if not in_stock:
        await callback.answer("Этого товара сейчас нет в наличии", show_alert=True)
        return

    await callback.message.answer(
        f"<b>{_esc(product['title'])}</b>\nВыберите вариант:",
        reply_markup=variants_pick_kb(in_stock),
        parse_mode=_HTML,
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_ADD}:"))
async def add_to_cart(callback: CallbackQuery) -> None:
    variant_id = int(callback.data.split(":")[-1])
    await queries.ensure_client(callback.from_user.id)

    variant = await queries.get_variant(variant_id)
    if not variant or not variant["is_active"]:
        await callback.answer("Товар больше не продаётся", show_alert=True)
        return

    status = await queries.add_to_cart(callback.from_user.id, variant_id, 1)
    if status == "out_of_stock":
        await callback.answer(
            f"В наличии только {variant['stock']} шт — больше положить не получится",
            show_alert=True,
        )
        return
    if status != "ok":
        await callback.answer("Не получилось добавить товар", show_alert=True)
        return

    # Цель для дашборда агентства: положили товар в корзину. Считаем здесь, а
    # не в queries.add_to_cart, потому что цель — про действие человека, а
    # функцию базы дёргают ещё и правки количества.
    agent_stats.report_goal(
        "cart_add",
        callback.from_user.id,
        variant_id=variant_id,
        title=variant["title"],
        qty=1,
        source="button",
    )

    cart = await queries.get_cart(callback.from_user.id)
    await callback.message.answer(
        f"✅ В корзине: {_esc(variant['title'])} — {_esc(variant_label(variant))}\n\n"
        f"Всего позиций: {cart['count']}, сумма {money(cart['total'])}",
        reply_markup=added_kb(),
        parse_mode=_HTML,
    )
    await callback.answer("Добавлено в корзину")


@router.callback_query(F.data.startswith((f"{CB_INC}:", f"{CB_DEC}:", f"{CB_DEL}:")))
async def change_qty(callback: CallbackQuery) -> None:
    """Плюс, минус и удаление позиции. Остаток проверяет queries.set_cart_qty."""
    action, variant_id = callback.data.rsplit(":", 1)
    variant_id = int(variant_id)
    client_id = callback.from_user.id

    if action == CB_DEL:
        await queries.remove_from_cart(client_id, variant_id)
        note = "Позиция убрана"
    else:
        cart = await queries.get_cart(client_id)
        current = next(
            (i["qty"] for i in cart["items"] if i["variant_id"] == variant_id), 0
        )
        if not current:
            await callback.answer("Этой позиции уже нет в корзине")
            await show_cart_callback(callback)
            return

        new_qty = current + (1 if action == CB_INC else -1)
        status = await queries.set_cart_qty(client_id, variant_id, new_qty)
        if status == "out_of_stock":
            await callback.answer("Больше на складе нет", show_alert=True)
            return
        if status == "not_found":
            await callback.answer("Товар больше не продаётся", show_alert=True)
            return
        note = "Обновил"

    cart = await queries.get_cart(client_id)
    await _edit_or_send(
        callback, cart_text(cart), cart_kb(cart) if cart["items"] else None
    )
    await callback.answer(note)


@router.callback_query(F.data == CB_CLEAR)
async def clear_ask(callback: CallbackQuery) -> None:
    await _edit_or_send(callback, "Очистить корзину полностью?", clear_confirm_kb())
    await callback.answer()


@router.callback_query(F.data == CB_CLEAR_OK)
async def clear_confirm(callback: CallbackQuery) -> None:
    await queries.clear_cart(callback.from_user.id)
    cart = await queries.get_cart(callback.from_user.id)
    await _edit_or_send(callback, cart_text(cart))
    await callback.answer("Корзина пуста")


# ─────────────────────── Оформление заказа ───────────────────────


async def _ask(message: Message, state: FSMContext, field: str) -> None:
    """Задаёт вопрос текущего шага и переводит FSM в соответствующее состояние."""
    await state.set_state(_STATE_BY_FIELD[field])
    data = await state.get_data()
    keep = (data.get("profile") or {}).get(field) if field in _PROFILE_FIELDS else None

    markup = step_kb(keep, skip=(field in _SKIPPABLE))
    if field == "phone":
        # Нижнюю клавиатуру и inline-кнопки одним сообщением не отправить —
        # шлём двумя: сначала кнопка контакта, следом «Оставить»/«Отменить».
        await message.answer(_PROMPTS[field], reply_markup=phone_kb())
        await message.answer("Или воспользуйтесь кнопками ниже:", reply_markup=markup)
    else:
        await message.answer(_PROMPTS[field], reply_markup=markup)


async def _show_summary(message: Message, state: FSMContext) -> None:
    """Сводка перед созданием заказа. Корзину перечитываем: она могла измениться."""
    cart = await queries.get_cart(message.chat.id)
    if not cart["items"]:
        await state.clear()
        await message.answer(
            "Корзина опустела, пока мы оформляли заказ. Соберите её заново 🙏",
            reply_markup=main_menu(),
        )
        return

    await state.set_state(Checkout.confirm)
    data = await state.get_data()
    promo = await queries.active_promo(message.chat.id)
    await message.answer(
        _summary_text(cart, data, promo=promo),
        reply_markup=summary_kb(),
        parse_mode=_HTML,
    )


async def _known_delivery(client_id: int) -> dict:
    """Данные доставки, которые бот уже знает про этого клиента.

    Последний заказ важнее профиля: в заказ данные попали через эту же форму —
    их проверял код, и именно по ним уехала посылка. Поэтому заказ берётся как
    есть: перепроверять имя, которое форма уже приняла, нельзя. Проверка имён
    приблизительная и живого человека узнаёт не всегда — на трёх попытках форма
    принимает что дали, и такое имя обнулялось здесь, а клиент из-за одной этой
    строчки снова получал все четыре вопроса.

    Из профиля добираем только то, чего в заказе не нашлось, и вот его как раз
    фильтруем: туда мог что-то записать ИИ по ходу разговора.

    Отделение без номера отсекаем в любом случае — по такой строке посылку не
    отправить, откуда бы она ни пришла (например, из правки в веб-CRM).
    """
    orders = await queries.get_client_orders(client_id, 1)
    last = orders[0] if orders else {}
    known = {field: str(last.get(field) or "").strip() for field in _PROFILE_FIELDS}

    client = await queries.get_client(client_id) or {}
    for field in _PROFILE_FIELDS:
        if known[field]:
            continue
        value = str(client.get(field) or "").strip()
        if field == "name" and value and not looks_like_name(value):
            value = ""
        known[field] = value

    if known["np_branch"] and not looks_like_branch(known["np_branch"]):
        known["np_branch"] = ""

    missing = [field for field in _PROFILE_FIELDS if not known[field]]
    if missing and last:
        # В логах видно, почему постоянному покупателю всё же досталась форма.
        logger.info("Заказ %s клиента %s без полей доставки: %s",
                    last.get("id"), client_id, ", ".join(missing))
    return known


async def _start_checkout(message: Message, state: FSMContext, client_id: int,
                          *, offer_saved: bool = True) -> None:
    """Проверяет корзину и запускает оформление. Профиль кладём в state целиком.

    Постоянному покупателю формы не будет вовсе: все данные доставки уже
    известны — показываем готовый заказ и спрашиваем один раз, менять их или
    нет. `offer_saved=False` приходит от кнопки «Поменять данные»: там как раз
    просили форму, и предлагать те же данные снова было бы петлёй.
    """
    cart = await queries.get_cart(client_id)
    if not cart["items"]:
        await message.answer(
            "Корзина пуста — сначала выберем товар 🙂", reply_markup=main_menu()
        )
        return

    # Товар мог разойтись, пока корзина лежала. Списание остатков делает
    # create_order, но узнать об этом на последнем шаге — обидно.
    stale = [i for i in cart["items"] if not i["is_active"] or i["stock"] < i["qty"]]
    if stale:
        await message.answer(
            cart_text(cart) + "\n\n⚠️ Поправьте количество — и оформим заказ.",
            reply_markup=cart_kb(cart),
            parse_mode=_HTML,
        )
        return

    profile = await _known_delivery(client_id)
    await state.set_data({"profile": profile})

    # Известно всё до единого поля — значит, человек у нас уже заказывал и
    # спрашивать по кругу имя, телефон, город и отделение незачем. Данные сразу
    # кладём в заказ: нажатие «Оставить эти данные» его и оформит.
    if offer_saved and all(profile[field] for field in _PROFILE_FIELDS):
        await state.set_state(Checkout.saved)
        await state.update_data(**profile)
        await message.answer(
            _summary_text(cart, profile, tail=_SAVED_TAIL,
                          promo=await queries.active_promo(client_id)),
            reply_markup=saved_data_kb(),
            parse_mode=_HTML,
        )
        return

    await _ask(message, state, "name")


@router.callback_query(F.data == CB_CHECKOUT)
async def checkout_start(callback: CallbackQuery, state: FSMContext) -> None:
    await queries.ensure_client(callback.from_user.id)
    await callback.answer()
    await _start_checkout(callback.message, state, callback.from_user.id)


@router.callback_query(StateFilter(Checkout), F.data == CB_CANCEL)
async def checkout_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Выход из формы. Корзину не трогаем — человек вернётся к ней позже."""
    await state.clear()
    await callback.message.answer(
        "Оформление отменено. Корзина сохранена — вернуться к ней можно кнопкой ниже.",
        reply_markup=main_menu(),
    )
    await callback.answer()


@router.callback_query(StateFilter(Checkout), F.data == CB_RESTART)
async def checkout_restart(callback: CallbackQuery, state: FSMContext) -> None:
    """«Изменить данные» / «Ввести данные заново» — обычная форма с первого шага."""
    await callback.answer()
    await _start_checkout(callback.message, state, callback.from_user.id,
                          offer_saved=False)


@router.callback_query(Checkout.saved, F.data == CB_KEEP_ALL)
async def checkout_keep_all(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """«Оставить эти данные» — и заказ оформлен. Больше ничего не спрашиваем.

    Ни комментария, ни повторной сводки: весь заказ человек видел прямо над
    кнопкой, а лишний шаг здесь и был тем, от чего уходили.
    """
    await callback.answer()
    data = await state.get_data()
    if not all(data.get(field) for field in _PROFILE_FIELDS):
        # Данные разъехались (например, кнопку нажали в старом сообщении) —
        # спокойно возвращаемся к форме, а не оформляем заказ на пустоту.
        await _start_checkout(callback.message, state, callback.from_user.id,
                              offer_saved=False)
        return

    await _place_order(callback, state, bot)


async def _accept(message: Message, state: FSMContext, field: str, value: str) -> None:
    """Сохраняет ответ шага и переходит к следующему (или к сводке)."""
    await state.update_data(**{field: value})
    next_field = _NEXT_FIELD[field]
    if next_field:
        await _ask(message, state, next_field)
    else:
        await _show_summary(message, state)


def _current_field(state_name: str | None) -> str | None:
    for field, marker in _STATE_BY_FIELD.items():
        if marker.state == state_name:
            return field
    return None


@router.callback_query(StateFilter(Checkout), F.data == CB_KEEP)
async def step_keep(callback: CallbackQuery, state: FSMContext) -> None:
    """«Оставить: …» — подставляет значение из профиля клиента."""
    field = _current_field(await state.get_state())
    if not field:
        await callback.answer()
        return

    data = await state.get_data()
    value = (data.get("profile") or {}).get(field, "")
    if not value:
        await callback.answer("Это значение придётся ввести")
        return

    await callback.answer()
    await _accept(callback.message, state, field, value)


@router.callback_query(StateFilter(Checkout.comment, Checkout.email), F.data == CB_SKIP)
async def step_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """«Пропустить» на необязательном шаге — комментарий или почта."""
    field = _current_field(await state.get_state())
    if not field:
        await callback.answer()
        return
    await callback.answer()
    await _accept(callback.message, state, field, "")


@router.message(Checkout.phone, F.contact)
async def step_phone_contact(message: Message, state: FSMContext) -> None:
    """Номер, отданный самим Telegram, — самый надёжный источник."""
    phone = _clean_phone(message.contact.phone_number) or message.contact.phone_number
    # Убираем клавиатуру с кнопкой контакта: дальше идёт обычный ввод.
    await message.answer(f"Записал телефон: {phone}", reply_markup=main_menu())
    await _accept(message, state, "phone", phone)


@router.message(StateFilter(Checkout), F.text)
async def step_text(message: Message, state: FSMContext) -> None:
    """Общий обработчик текстовых ответов на шаги формы."""
    current = await state.get_state()
    if current == Checkout.saved.state:
        # Кнопки «Отменить» на этом экране нет намеренно, поэтому выходом
        # служит нижнее меню: нажал «Каталог» — значит, передумал оформлять.
        if _asks_for_section(message.text):
            await state.clear()
            await message.answer(
                "Оформление отложено, корзина сохранена. Напишите, когда "
                "будете готовы продолжить 🙂",
                reply_markup=main_menu(),
            )
            return
        await message.answer(
            "Данные доставки те же — нажмите «Оставить эти данные», и оформлю "
            "заказ. Что-то поменялось — «Поменять данные»."
        )
        return

    field = _current_field(current)
    if not field:  # состояние confirm — ждём кнопку, а не текст
        await message.answer(
            "Проверьте заказ выше и нажмите «Всё верно, оформить».",
        )
        return

    text = message.text.strip()
    if _asks_for_section(text):
        await message.answer(
            "Сейчас оформляем заказ. Чтобы выйти — нажмите «Отменить оформление» "
            "под вопросом выше."
        )
        return

    if field == "phone":
        phone = _clean_phone(text)
        if not phone:
            await message.answer(
                "Не похоже на номер телефона. Напишите в формате +380XXXXXXXXX "
                "или нажмите кнопку «Поделиться контактом»."
            )
            return
        await message.answer(f"Записал телефон: {phone}", reply_markup=main_menu())
        await _accept(message, state, "phone", phone)
        return

    if field == "email":
        # Отказ словами принимаем наравне с кнопкой: человек в чате отвечает
        # «нет, спасибо», а не ищет глазами, куда нажать.
        if text.lower() in ("нет", "не надо", "не хочу", "пропустить", "-", "—"):
            await _accept(message, state, "email", "")
            return
        if not mail.looks_like_email(text):
            await message.answer(
                "Это не похоже на адрес почты. Напишите так: ivan@example.com — "
                "или нажмите «Пропустить», почта не обязательна."
            )
            return
        await _accept(message, state, "email", mail.normalize_email(text))
        return

    low, high = _LIMITS[field]
    if len(text) < low:
        await message.answer("Слишком коротко — напишите, пожалуйста, подробнее.")
        return

    if field == "np_branch" and not looks_like_branch(text):
        await message.answer(
            "В отделении Новой Почты должен быть номер. Напишите его цифрой — "
            "например «12», «№7» или «Поштомат 4521»."
        )
        return

    if field == "name" and not looks_like_name(text):
        # Не упираемся: после двух переспросов принимаем что дали. Иначе человек с
        # именем, которое проверка не узнала, застрянет на первом же шаге и уйдёт
        # без заказа — а имя владелец всегда может поправить в CRM.
        attempts = (await state.get_data()).get("name_attempts", 0) + 1
        await state.update_data(name_attempts=attempts)
        if attempts < _MAX_NAME_ATTEMPTS:
            await message.answer(
                "Похоже, это не имя 🙂 Курьер будет искать получателя по нему, "
                "поэтому напишите имя и фамилию — например «Анна Ковальчук»."
            )
            return
        logger.info("Имя получателя принято без проверки после %s попыток", attempts)

    await _accept(message, state, field, text[:high])


@router.message(StateFilter(Checkout), F.photo)
async def step_photo(message: Message) -> None:
    """Фото посреди оформления: разговор с ИИ тут не к месту, но и молчать нельзя.

    Клиентский роутер разбирает снимки только вне формы, поэтому здесь мы
    объясняем, что сейчас ждём ответ на вопрос, — иначе человек шлёт фото и не
    получает ничего.
    """
    await message.answer(
        "Сейчас оформляем заказ, поэтому фото я посмотрю чуть позже — ответьте, "
        "пожалуйста, на вопрос выше. Не хотите продолжать сейчас — нажмите "
        "«Отменить оформление»."
    )


@router.callback_query(Checkout.confirm, F.data == CB_CONFIRM)
async def checkout_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """«Всё верно, оформить» в конце формы."""
    await _place_order(callback, state, bot)


async def _place_order(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Создаёт заказ: списывает остатки, чистит корзину, шлёт реквизиты.

    Вызывается из двух мест — с конца формы и с экрана повторного заказа, где
    подтверждение данных и есть оформление.
    """
    data = await state.get_data()
    client_id = callback.from_user.id

    status, order_id = await queries.create_order(
        client_id,
        name=data.get("name", ""),
        phone=data.get("phone", ""),
        city=data.get("city", ""),
        np_branch=data.get("np_branch", ""),
        comment=data.get("comment", ""),
    )

    if status == "empty_cart":
        await state.clear()
        await callback.message.answer(
            "Корзина уже пуста — заказ создавать не из чего.", reply_markup=main_menu()
        )
        await callback.answer()
        return

    if status == "out_of_stock":
        # Ничего не списано и заказ не создан: показываем актуальную корзину,
        # чтобы клиент увидел, чего именно не хватает.
        await state.clear()
        cart = await queries.get_cart(client_id)
        await callback.message.answer(
            "Пока мы оформляли, товар успели разобрать 😔\n"
            "Проверьте корзину — что-то придётся заменить или уменьшить.",
            reply_markup=main_menu(),
        )
        await callback.message.answer(
            cart_text(cart),
            reply_markup=cart_kb(cart) if cart["items"] else None,
            parse_mode=_HTML,
        )
        await callback.answer()
        return

    # Данные доставки запоминаем в профиле: следующий заказ клиент оформит
    # в два тапа кнопкой «Оставить».
    #
    # Почта — исключение: пустое значение сюда НЕ передаём. Пустая строка в
    # update_client значит «убрать адрес», а пропущенный шаг значит всего лишь
    # «сейчас не хочу вводить» — стирать этим уже записанную почту нельзя.
    email = data.get("email", "")
    await queries.update_client(
        client_id,
        name=data.get("name", ""),
        phone=data.get("phone", ""),
        city=data.get("city", ""),
        np_branch=data.get("np_branch", ""),
        email=email or None,
    )
    await state.clear()

    order = await queries.get_order(order_id)
    agent_stats.report_goal(
        "order_created", client_id, order_id=order_id, total=order["total"]
    )
    await callback.message.answer(
        f"Спасибо! Заказ №{order_id} принят.", reply_markup=main_menu()
    )

    # Провайдер подключён — считаем оплату счётом в Telegram, а не переводом
    # на карту. Дальше решает handlers/payments.py: pre_checkout_query и
    # successful_payment, заказ подтвердится сам, без «Я оплатил» и владельца.
    if not await payments.send_invoice(bot, order):
        await callback.message.answer(
            payment_text(order), reply_markup=payment_kb(order_id), parse_mode=_HTML
        )
    # Владельцу здесь не пишем: заказ ждёт оплаты, решать по нему нечего.
    # При переводе на карту заявка уйдёт по кнопке «Я оплатил» (см. claim_paid);
    # при оплате картой в Telegram — сама придёт после successful_payment. Сам
    # заказ виден в веб-CRM с момента оформления.
    await callback.answer()


# ─────────────────────────── Оплата ───────────────────────────


@router.callback_query(F.data.startswith(f"{CB_PAID}:"))
async def claim_paid(callback: CallbackQuery, bot: Bot) -> None:
    """«Я оплатил»: помечает заказ и шлёт заявку владельцу.

    Повторный тап дубля не создаёт: смену статуса делает один UPDATE с
    условием status = 'awaiting_payment' — второй раз он ничего не меняет.
    """
    order_id = int(callback.data.split(":")[-1])
    order = await queries.get_order(order_id)

    if not order or order["client_id"] != callback.from_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    if not await queries.mark_paid_claimed(order_id):
        human = ORDER_STATUS_RU.get(order["status"], order["status"])
        await callback.answer(f"Заказ №{order_id}: {human}", show_alert=True)
        return

    order = await queries.get_order(order_id)
    await _edit_or_send(
        callback,
        f"Спасибо! Проверяем поступление по заказу №{order_id} и вернёмся с "
        f"подтверждением 🙌",
    )
    await notify_admin_order(bot, order, callback.from_user.username)
    await callback.answer()


# ─────────────────────────── Мои заказы ───────────────────────────


async def _orders_view(client_id: int) -> tuple[str, object]:
    """Текст и кнопки списка заказов. Один источник для сообщения и для callback."""
    orders = await queries.get_client_orders(client_id, _MAX_ORDERS_SHOWN)
    if not orders:
        return "Заказов пока нет. Напишите, что ищете — подберу 🙂", None

    lines = ["<b>📦 Ваши заказы</b>", ""]
    for order in orders:
        lines.append(
            f"№{order['id']} от {order['created_at'][:10]} — {money(order['total'])}\n"
            f"    {ORDER_STATUS_RU.get(order['status'], order['status'])}"
        )
        if order["ttn"]:
            lines.append(f"    Накладная: {_esc(order['ttn'])}")
    return "\n".join(lines), orders_kb(orders)


@router.message(StateFilter(None), F.text.func(lambda t: asks_for(t, BTN_ORDERS)))
async def my_orders(message: Message) -> None:
    """Последние заказы со статусами, кнопками оплаты и отмены."""
    await queries.ensure_client(message.from_user.id)
    text, markup = await _orders_view(message.from_user.id)
    await message.answer(
        text, reply_markup=markup or main_menu(), parse_mode=_HTML
    )


@router.callback_query(F.data == CB_ORDERS)
async def my_orders_callback(callback: CallbackQuery) -> None:
    """Возврат к списку заказов — например, из отказа от отмены."""
    text, markup = await _orders_view(callback.from_user.id)
    await _edit_or_send(callback, text, markup)
    await callback.answer()


# ─────────────────────── Отмена заказа клиентом ───────────────────────


async def _client_order(callback: CallbackQuery) -> dict | None:
    """Заказ из callback'а, если он принадлежит нажавшему. Иначе None."""
    order_id = int(callback.data.split(":")[-1])
    order = await queries.get_order(order_id)
    if not order or order["client_id"] != callback.from_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return None
    return order


@router.callback_query(F.data.startswith(f"{CB_ORD_CANCEL}:"))
async def order_cancel_ask(callback: CallbackQuery) -> None:
    """Переспрос перед отменой: показываем, что именно отменяем."""
    order = await _client_order(callback)
    if not order:
        return

    if not can_client_cancel(order):
        await callback.answer(
            f"Заказ №{order['id']} уже отменить нельзя — "
            f"{ORDER_STATUS_RU.get(order['status'], order['status'])}. "
            f"Напишите нам, разберёмся.",
            show_alert=True,
        )
        return

    await _edit_or_send(
        callback,
        f"Отменить заказ №{order['id']} на {money(order['total'])}?\n\n"
        f"{order_items_text(order)}\n\n"
        f"Товар вернётся на витрину, а заказ — восстановить не получится: "
        f"если передумаете, оформим заново.",
        order_cancel_confirm_kb(order["id"]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_ORD_CANCEL_OK}:"))
async def order_cancel_do(callback: CallbackQuery, bot: Bot) -> None:
    """Отмена заказа клиентом: возвращает остатки и уведомляет владельца.

    Статус перечитываем прямо перед отменой: пока висел переспрос, владелец мог
    успеть отправить посылку и вбить накладную.
    """
    order = await _client_order(callback)
    if not order:
        return

    if not can_client_cancel(order):
        await callback.answer(
            f"Заказ №{order['id']} уже отменить нельзя — "
            f"{ORDER_STATUS_RU.get(order['status'], order['status'])}.",
            show_alert=True,
        )
        return

    if not await queries.cancel_order(order["id"], note="Отменён покупателем"):
        await callback.answer("Заказ уже отменён", show_alert=True)
        return

    await _edit_or_send(
        callback,
        f"Заказ №{order['id']} отменён, товар вернулся на витрину.\n\n"
        f"Будет нужно — соберём новый заказ 🙂",
    )
    await callback.answer("Заказ отменён")
    await _notify_admin_cancelled(bot, order, callback.from_user.username)


async def _notify_admin_cancelled(bot: Bot, order: dict, username: str | None) -> None:
    """Владельцу — что заказ отменил сам покупатель. Ошибку глушим: заказ уже отменён.

    `order` — состояние ДО отмены. Про неоплаченные заказы молчим: владелец о
    них не знал (заявка уходит только после кнопки «Я оплатил»), и сообщение об
    отмене того, чего он не видел, — лишний шум. Отменённый заказ виден в
    веб-CRM.
    """
    if order["status"] in _UNSEEN_BY_ADMIN:
        return

    contact = f"@{username}" if username else f"id {order['client_id']}"
    text = (
        f"🚫 <b>Покупатель отменил заказ №{order['id']}</b>\n"
        f"Товар вернулся на витрину.\n\n"
        f"{order_items_text(order)}\n\n"
        f"<b>Сумма: {money(order['total'])}</b>\n\n"
        f"Получатель: {_esc(order['name'])}\n"
        f"Телефон: {_esc(order['phone'])}\n"
        f"Клиент: {_esc(contact)}"
    )
    try:
        await bot.send_message(config.ADMIN_ID, text, parse_mode=_HTML)
    except (TelegramForbiddenError, TelegramBadRequest):
        logger.exception("Не удалось сообщить владельцу об отмене заказа %s", order["id"])
