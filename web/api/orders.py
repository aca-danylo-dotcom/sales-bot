"""Раздел «Заказы»: рабочее место менеджера, теперь в виде JSON-API.

Здесь заказ проходит весь путь — проверили оплату, собрали, отправили, закрыли —
и на каждом шаге клиент получает сообщение от бота в свой чат. Поэтому в
приложение прокинут сам `bot` (см. web/app.py): панель не «показывает данные»,
а разговаривает с покупателем от имени магазина.

Две вещи, из-за которых код выглядит осторожнее обычного CRUD:

1. Панель открыта у нескольких человек сразу, и заказ у всех на экране разный по
   свежести. Поэтому каждый переход статуса — условный UPDATE («переведи в
   confirmed, если сейчас awaiting_payment или paid_claimed»), а не «прочитал,
   решил, записал». Нажатие по устаревшей карточке честно отвечает «заказ уже
   не там» вместо того, чтобы откатить чужую работу.
2. Уведомление клиенту не должно ронять действие: заказ уже подтверждён, а
   заблокированный бот — это повод показать предупреждение менеджеру, а не
   потерять сделанное.

Входа в панель нет (решение владельца), поэтому «взять в работу» подписывается
именем, которое менеджер вписывает сам; браузер помнит его в cookie.
"""
from __future__ import annotations

import logging

from aiohttp import web

from db import queries
from handlers.orders import (
    client_cancelled_text,
    client_confirmed_text,
    client_shipped_text,
    notify_client,
)
from services import agent_stats, format
from services.format import ORDER_STATUS_RU
from web import forms
from web.api.helpers import body, fail, not_found, ok

logger = logging.getLogger(__name__)

PAGE_SIZE = 20

# Имя менеджера живёт в браузере полгода: вход не предусмотрен, а вписывать себя
# заново на каждом заказе никто не станет — и поле «кто ведёт» осталось бы пустым.
MANAGER_COOKIE = "manager"
MANAGER_COOKIE_MAX_AGE = 180 * 24 * 3600

# Вкладки списка. Порядок — рабочий, а не алфавитный: сверху то, что горит
# (клиент говорит, что оплатил), потом то, что собирают и отправляют.
# Подписи короче, чем ORDER_STATUS_RU: на вкладке нужен ярлык, а не описание.
STATUS_TABS = [
    ("", "Все"),
    ("paid_claimed", "Ждут проверки"),
    ("confirmed", "К отправке"),
    ("shipped", "Отправлены"),
    ("awaiting_payment", "Ждут оплаты"),
    ("done", "Выполнены"),
    ("cancelled", "Отменены"),
]
# 'new' — заказ, который ещё не дошёл до оплаты. Вкладку показываем, только
# когда такие есть, чтобы не держать вечно пустую.
EXTRA_TAB = ("new", "Новые")

# Ярлыки для таблицы и заголовка карточки. В ORDER_STATUS_RU формулировки
# написаны для клиента («клиент оплатил, ждём подтверждения») — в колонку
# таблицы такое не помещается, а менеджеру и не нужно.
SHORT_STATUS = {
    "new": "Новый",
    "awaiting_payment": "Ждёт оплаты",
    "paid_claimed": "Ждёт проверки",
    "confirmed": "К отправке",
    "shipped": "Отправлен",
    "done": "Выполнен",
    "cancelled": "Отменён",
}

MESSAGES = {
    "confirmed": "Оплата подтверждена, клиенту отправлено сообщение.",
    "shipped": "Накладная сохранена, клиент получил её номер.",
    "done": "Заказ закрыт.",
    "cancelled": "Заказ отменён, товар вернулся на склад.",
    "taken": "Заказ теперь ваш.",
    "released": "Заказ свободен — его может взять любой менеджер.",
    "note": "Заметка сохранена.",
    "note_deleted": "Бот больше не помнит этот факт о клиенте.",
    "saved": "Данные заказа сохранены.",
}

WARNINGS = {
    "undelivered": "Сообщение клиенту не дошло: бот заблокирован или чат удалён. "
                   "Свяжитесь с ним другим способом.",
}

ERRORS = {
    "status": "Заказ уже в другом состоянии — обновите страницу и посмотрите, что с ним стало.",
    "ttn_empty": "Введите номер накладной.",
    "need_assignee": "Сначала укажите, кто взялся за заказ: впишите имя вверху страницы и "
                     "нажмите «Взять в работу». После этого можно сохранять накладную.",
    "taken": "Заказ уже взял другой менеджер — обновите страницу, там видно, кто именно.",
    "manager_empty": "Впишите своё имя — оно останется на заказе.",
    "cancel_twice": "Заказ уже был отменён, второй раз товар не вернётся.",
    "cancel_closed": "Заказ уже выполнен — отменить его нельзя. Если товар вернули, "
                     "заведите остаток вручную во вкладке «Остатки».",
    "not_found": "Заказ не найден.",
}

# Статусы, из которых отмена уже не имеет смысла: выполненный заказ забрали,
# отменённый отменён.
_CANCEL_CLOSED = ("done", "cancelled")

# Конфликт состояния — 409, а не 400: менеджер ничего не напутал, просто заказ
# успели увести. Клиент по этому коду обновляет карточку молча.
_CONFLICT = 409


def _status(value: object) -> str | None:
    """Фильтр по статусу. Незнакомое значение — это отсутствие фильтра."""
    return value if value in ORDER_STATUS_RU else None


def _filters(request: web.Request) -> dict:
    query = request.query
    return {
        "status": _status(query.get("status")),
        "search": forms.text(query, "q", max_len=100) or None,
        "date_from": forms.date_value(query.get("from")),
        "date_to": forms.date_value(query.get("to")),
    }


def _manager(request: web.Request) -> str:
    """Кто сидит за этим браузером. Пусто — имя ещё не вводили."""
    return (request.cookies.get(MANAGER_COOKIE) or "").strip()[:forms.MAX_SHORT]


async def _notify(request: web.Request, client_id: int, text: str) -> bool:
    """Сообщение клиенту от бота. False — не дошло (или бот в панель не передан).

    Ошибку сюда не пускаем: действие менеджера уже выполнено, и падение
    запроса после подтверждённой оплаты выглядело бы как «ничего не вышло».
    """
    bot = request.app.get("bot")
    if bot is None:
        logger.warning("Бот в веб-приложение не передан, клиент %s не уведомлён", client_id)
        return False
    try:
        return await notify_client(bot, client_id, text)
    except Exception:  # noqa: BLE001 — сеть Telegram не должна ронять CRM
        logger.exception("Не удалось уведомить клиента %s", client_id)
        return False


def _done(key: str, delivered: bool = True) -> web.Response:
    """Ответ после действия: что сделали и дошло ли это до клиента."""
    return ok(message=MESSAGES[key], warning="" if delivered else WARNINGS["undelivered"])


async def _order_or_404(request: web.Request) -> dict:
    order = await queries.get_order_full(int(request.match_info["id"]))
    if not order:
        raise not_found(ERRORS["not_found"])
    return order


# ─────────────────────────── Список ───────────────────────────


def _row(order: dict) -> dict:
    """Строка таблицы: то же, что было в шаблоне, плюс готовые подписи."""
    return {
        **order,
        "total_text": format.money(order["total"]),
        "status_short": SHORT_STATUS.get(order["status"], order["status"]),
    }


async def orders_list(request: web.Request) -> web.Response:
    filters = _filters(request)
    page = forms.page(request.query.get("page"))

    total = await queries.count_orders(**filters)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages - 1)
    orders = await queries.list_orders(**filters, limit=PAGE_SIZE, offset=page * PAGE_SIZE)

    # Счётчики считаем по тому же поиску и периоду, но без фильтра статуса:
    # цифра на вкладке должна означать «сколько там окажется, если нажать».
    counts = await queries.count_orders_by_status(
        search=filters["search"], date_from=filters["date_from"], date_to=filters["date_to"]
    )
    tabs = list(STATUS_TABS)
    if counts.get(EXTRA_TAB[0]):
        tabs.insert(1, EXTRA_TAB)

    return ok({
        "orders": [_row(order) for order in orders],
        "tabs": [
            {
                "value": value,
                "title": title,
                "count": sum(counts.values()) if not value else counts.get(value, 0),
            }
            for value, title in tabs
        ],
        "total": total,
        "page": page + 1,
        "pages": pages,
    })


# ─────────────────────────── Карточка ───────────────────────────


async def order_card(request: web.Request) -> web.Response:
    order = await _order_or_404(request)
    tab = "client" if request.query.get("tab") == "client" else "order"

    for item in order["items"]:
        item["variant"] = format.variant_label(item)
        item["price_text"] = format.money(item["price_snapshot"])
        item["sum_text"] = format.money(item["sum"])

    # Скидка по промокоду. `total` в базе уже со скидкой, поэтому без этой пары
    # полей менеджер видит сумму, которая не сходится с ценами позиций, и идёт
    # спрашивать, почему клиент недоплатил.
    discount = order.get("discount") or 0
    payload = {
        "order": {
            **order,
            "total_text": format.money(order["total"]),
            "discount_text": format.money(discount) if discount else "",
            "full_text": format.money(round(order["total"] + discount, 2)) if discount else "",
            "status_short": SHORT_STATUS.get(order["status"], order["status"]),
            "status_name": ORDER_STATUS_RU.get(order["status"], order["status"]),
        },
        "manager": _manager(request),
        "can_confirm": order["status"] in ("new", "awaiting_payment", "paid_claimed"),
        "can_ship": order["status"] in queries.SHIPPABLE_STATUSES,
        "can_finish": order["status"] == "shipped",
        # Выполненный заказ отменять нечего: посылку забрали, и «возврат товара на
        # склад» только испортил бы остатки. Отменить можно всё до этого — включая
        # отправленный, посылку ведь могут и не забрать.
        "can_cancel": order["status"] not in _CANCEL_CLOSED,
        "timeline": [
            {"title": "Оформлен", "stamp": order["created_at"]},
            {"title": "Клиент сказал, что оплатил", "stamp": order["paid_at"]},
            {"title": "Оплата подтверждена", "stamp": order["confirmed_at"]},
            {"title": "Отправлен", "stamp": order["shipped_at"]},
        ],
    }

    if tab == "client":
        # Прошлые заказы и переписка нужны, когда клиент звонит с вопросом
        # «а что там с моим заказом»: всё про человека — на одной вкладке.
        other = await queries.get_client_orders(order["client_id"], limit=10)
        payload["client_orders"] = [_row(o) for o in other if o["id"] != order["id"]]
        payload["history"] = await queries.get_history(order["client_id"], limit=40)
        # Память бота о клиенте — то, что уходит в промпт на каждом сообщении.
        # Менеджеру она видна затем, чтобы понять, почему бот советует именно
        # это, и вычистить факт, который бот понял не так.
        payload["client_notes"] = await queries.get_client_notes(order["client_id"])
        # Почта (если клиент её оставил) и выписанные ему промокоды. Второе —
        # чтобы на вопрос «мне обещали скидку» менеджер отвечал по факту, а не
        # по памяти: видно и сам код, и сгорел ли он.
        client = await queries.get_client(order["client_id"]) or {}
        payload["client_email"] = client.get("email") or ""
        payload["client_promos"] = await queries.get_client_promos(order["client_id"])
    return ok(payload)


# ─────────────────────────── Действия ───────────────────────────


async def order_confirm(request: web.Request) -> web.Response:
    """«Оплата пришла»: заказ в сборку, клиенту — подтверждение."""
    order = await _order_or_404(request)
    moved = await queries.advance_order_status(
        order["id"], "confirmed", allowed_from=("new", "awaiting_payment", "paid_claimed")
    )
    if not moved:
        return fail(ERRORS["status"], _CONFLICT)

    # Цель — только после состоявшегося перехода: повторное нажатие по
    # устаревшей карточке выходит выше и второй «оплаченный заказ» не рисует.
    agent_stats.report_goal(
        "order_paid",
        order["client_id"],
        order_id=order["id"],
        total=order["total"],
        source="crm",
    )
    delivered = await _notify(request, order["client_id"], client_confirmed_text(order))
    return _done("confirmed", delivered)


async def order_ship(request: web.Request) -> web.Response:
    """Ввод накладной: заказ уезжает, клиент получает её номер.

    Отправить можно только взятый заказ. Отправка — то место, где заказ уходит
    из магазина, и по нему потом разбираются с претензиями: без имени в заказе
    спрашивать не с кого. Проверка серверная, а не только в интерфейсе: карточка
    могла открыться до того, как заказ отпустили.
    """
    order = await _order_or_404(request)
    if not order["assignee"]:
        return fail(ERRORS["need_assignee"], _CONFLICT)

    ttn = forms.text(await body(request), "ttn", max_len=40)
    if not ttn:
        return fail(ERRORS["ttn_empty"])

    if not await queries.set_order_ttn(order["id"], ttn):
        return fail(ERRORS["status"], _CONFLICT)

    delivered = await _notify(request, order["client_id"], client_shipped_text(order, ttn))
    return _done("shipped", delivered)


async def order_finish(request: web.Request) -> web.Response:
    """«Выполнен» — посылку забрали. Клиенту не пишем: он и так всё знает."""
    order = await _order_or_404(request)
    if not await queries.advance_order_status(order["id"], "done", allowed_from=("shipped",)):
        return fail(ERRORS["status"], _CONFLICT)
    return _done("done")


async def order_cancel(request: web.Request) -> web.Response:
    """Отмена с возвратом остатков. Причина остаётся внутри, клиенту не уходит."""
    order = await _order_or_404(request)
    # Проверка серверная, а не только в интерфейсе: карточка могла открыться до
    # того, как заказ отметили выполненным.
    if order["status"] in _CANCEL_CLOSED:
        return fail(ERRORS["cancel_closed"], _CONFLICT)

    reason = forms.text(await body(request), "reason", max_len=200)
    who = _manager(request)
    label = f"Отменён в CRM ({who})" if who else "Отменён в CRM"
    note = f"{label} · {reason}" if reason else label

    if not await queries.cancel_order(order["id"], note=note):
        return fail(ERRORS["cancel_twice"], _CONFLICT)

    delivered = await _notify(request, order["client_id"], client_cancelled_text(order))
    return _done("cancelled", delivered)


async def order_take(request: web.Request) -> web.Response:
    """«Беру» — заказ подписывается именем менеджера.

    Захват условный: если заказ уже кто-то взял, второй менеджер получает отказ,
    а не молча перебивает чужое имя. Имя заодно запоминается в браузере.
    """
    order = await _order_or_404(request)
    name = forms.text(await body(request), "manager") or _manager(request)
    if not name:
        return fail(ERRORS["manager_empty"])

    taken, current = await queries.take_order(order["id"], name)
    if not taken:
        logger.info("Заказ %s уже ведёт %s, отказ для %s", order["id"], current, name)
        return fail(ERRORS["taken"], _CONFLICT)

    response = _done("taken")
    # Cookie ставим и при отказе, и при успехе? Нет: имя запоминаем только когда
    # оно на заказе и осело — иначе браузер запомнит промах.
    response.set_cookie(
        MANAGER_COOKIE, name, max_age=MANAGER_COOKIE_MAX_AGE,
        httponly=True, samesite="Lax", path="/",
    )
    return response


async def order_release(request: web.Request) -> web.Response:
    """Снять с себя заказ — например, уходя со смены."""
    order = await _order_or_404(request)
    await queries.release_order(order["id"])
    return _done("released")


async def order_note(request: web.Request) -> web.Response:
    """Внутренняя заметка: видна только в панели, клиенту не показывается."""
    order = await _order_or_404(request)
    data = await body(request)
    await queries.set_order_fields(order["id"], note=forms.text(data, "note", max_len=500))
    return _done("note")


async def order_contacts(request: web.Request) -> web.Response:
    """Правка получателя и адреса — телефон с опечаткой правят чаще, чем кажется."""
    order = await _order_or_404(request)
    data = await body(request)
    await queries.set_order_fields(
        order["id"],
        name=forms.text(data, "name", max_len=100),
        phone=forms.text(data, "phone", max_len=30),
        city=forms.text(data, "city", max_len=100),
        np_branch=forms.text(data, "np_branch", max_len=120),
        comment=forms.text(data, "comment", max_len=300),
    )
    return _done("saved")


async def client_note_delete(request: web.Request) -> web.Response:
    """Убрать факт из памяти бота: он понял клиента не так — пусть забудет."""
    await _order_or_404(request)
    data = await body(request)
    note_id = forms.integer(data.get("note_id"), minimum=1)
    if note_id:
        await queries.delete_client_note(note_id)
    return _done("note_deleted")


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/api/orders", orders_list)
    app.router.add_get(r"/api/orders/{id:\d+}", order_card)
    app.router.add_post(r"/api/orders/{id:\d+}/confirm", order_confirm)
    app.router.add_post(r"/api/orders/{id:\d+}/ship", order_ship)
    app.router.add_post(r"/api/orders/{id:\d+}/done", order_finish)
    app.router.add_post(r"/api/orders/{id:\d+}/cancel", order_cancel)
    app.router.add_post(r"/api/orders/{id:\d+}/take", order_take)
    app.router.add_post(r"/api/orders/{id:\d+}/release", order_release)
    app.router.add_post(r"/api/orders/{id:\d+}/note", order_note)
    app.router.add_post(r"/api/orders/{id:\d+}/contacts", order_contacts)
    app.router.add_post(r"/api/orders/{id:\d+}/client-note-delete", client_note_delete)
