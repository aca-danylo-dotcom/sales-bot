"""Раздел «Заказы» веб-CRM: рабочее место менеджера.

Здесь заказ проходит весь путь — проверили оплату, собрали, отправили, закрыли —
и на каждом шаге клиент получает сообщение от бота в свой чат. Поэтому в
приложение прокинут сам `bot` (см. web/app.py): панель не «показывает данные»,
а разговаривает с покупателем от имени магазина.

Две вещи, из-за которых код выглядит осторожнее обычного CRUD:

1. Панель открыта у нескольких человек сразу, и заказ у всех на экране разный по
   свежести. Поэтому каждый переход статуса — условный UPDATE («переведи в
   confirmed, если сейчас awaiting_payment или paid_claimed»), а не «прочитал,
   решил, записал». Нажатие по устаревшей странице честно отвечает «заказ уже
   не там» вместо того, чтобы откатить чужую работу.
2. Уведомление клиенту не должно ронять действие: заказ уже подтверждён, а
   заблокированный бот — это повод показать предупреждение менеджеру, а не
   потерять сделанное.

Входа в панель нет (решение владельца), поэтому «взять в работу» подписывается
именем, которое менеджер вписывает сам; браузер помнит его в cookie.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

import aiohttp_jinja2
from aiohttp import web

import config
from db import queries
from handlers.orders import (
    client_cancelled_text,
    client_confirmed_text,
    client_shipped_text,
    notify_client,
)
from services.format import ORDER_STATUS_RU
from web import forms

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
    "saved": "Данные заказа сохранены.",
}

WARNINGS = {
    "undelivered": "Сообщение клиенту не дошло: бот заблокирован или чат удалён. "
                   "Свяжитесь с ним другим способом.",
}

ERRORS = {
    "status": "Заказ уже в другом состоянии — обновите страницу и посмотрите, что с ним стало.",
    "ttn_empty": "Введите номер накладной.",
    "taken": "Заказ уже взял другой менеджер — обновите страницу, там видно, кто именно.",
    "manager_empty": "Впишите своё имя — оно останется на заказе.",
    "cancel_twice": "Заказ уже был отменён, второй раз товар не вернётся.",
    "not_found": "Заказ не найден.",
}


def _redirect(location: str, **params: str) -> web.HTTPFound:
    """Редирект после действия, с кодом результата в адресе (см. web/products.py)."""
    query = urlencode({k: v for k, v in params.items() if v})
    raise web.HTTPFound(f"{location}?{query}" if query else location)


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


def _filters_qs(request: web.Request) -> str:
    keep = {k: v for k, v in request.query.items()
            if k in ("status", "q", "from", "to") and v}
    return urlencode(keep)


def _page_context(request: web.Request) -> dict:
    return {
        "shop_name": config.SHOP_NAME,
        "section": "orders",
        "message": MESSAGES.get(request.query.get("ok", "")),
        "warning": WARNINGS.get(request.query.get("warn", "")),
        "error": ERRORS.get(request.query.get("err", "")),
        "status_names": ORDER_STATUS_RU,
        "status_short": SHORT_STATUS,
    }


def _manager(request: web.Request) -> str:
    """Кто сидит за этим браузером. Пусто — имя ещё не вводили."""
    return (request.cookies.get(MANAGER_COOKIE) or "").strip()[:forms.MAX_SHORT]


async def _notify(request: web.Request, client_id: int, text: str) -> bool:
    """Сообщение клиенту от бота. False — не дошло (или бот в панель не передан).

    Ошибку сюда не пускаем: действие менеджера уже выполнено, и падение
    страницы после подтверждённой оплаты выглядело бы как «ничего не вышло».
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


def _result(order_id: int, ok: str, delivered: bool = True) -> web.HTTPFound:
    """Редирект в карточку: что сделали и дошло ли это до клиента."""
    _redirect(f"/orders/{order_id}", ok=ok, warn="" if delivered else "undelivered")


async def _order_or_404(request: web.Request) -> dict:
    order = await queries.get_order_full(int(request.match_info["id"]))
    if not order:
        raise web.HTTPNotFound(text="Заказ не найден")
    return order


# ─────────────────────────── Список ───────────────────────────


@aiohttp_jinja2.template("orders.html")
async def orders_list(request: web.Request) -> dict:
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

    return {
        **_page_context(request),
        "orders": orders,
        "tabs": [(value, title, sum(counts.values()) if not value else counts.get(value, 0))
                 for value, title in tabs],
        "total": total,
        "page": page + 1,
        "pages": pages,
        "filters_qs": _filters_qs(request),
        "tab_qs": urlencode({k: v for k, v in request.query.items()
                             if k in ("q", "from", "to") and v}),
        "status": request.query.get("status", ""),
        "q": request.query.get("q", ""),
        "date_from": request.query.get("from", ""),
        "date_to": request.query.get("to", ""),
    }


# ─────────────────────────── Карточка ───────────────────────────


@aiohttp_jinja2.template("order.html")
async def order_card(request: web.Request) -> dict:
    order = await _order_or_404(request)
    tab = "client" if request.query.get("tab") == "client" else "order"

    context = {
        **_page_context(request),
        "order": order,
        "tab": tab,
        "manager": _manager(request),
        "back_qs": _filters_qs(request),
        "can_confirm": order["status"] in ("new", "awaiting_payment", "paid_claimed"),
        "can_ship": order["status"] in queries.SHIPPABLE_STATUSES,
        "can_finish": order["status"] == "shipped",
        "can_cancel": order["status"] != "cancelled",
        "timeline": [
            ("Оформлен", order["created_at"]),
            ("Клиент сказал, что оплатил", order["paid_at"]),
            ("Оплата подтверждена", order["confirmed_at"]),
            ("Отправлен", order["shipped_at"]),
        ],
    }

    if tab == "client":
        # Прошлые заказы и переписка нужны, когда клиент звонит с вопросом
        # «а что там с моим заказом»: всё про человека — на одной вкладке.
        other = await queries.get_client_orders(order["client_id"], limit=10)
        context["client_orders"] = [o for o in other if o["id"] != order["id"]]
        context["history"] = await queries.get_history(order["client_id"], limit=40)
    return context


# ─────────────────────────── Действия ───────────────────────────


async def order_confirm(request: web.Request) -> web.Response:
    """«Оплата пришла»: заказ в сборку, клиенту — подтверждение."""
    order = await _order_or_404(request)
    moved = await queries.advance_order_status(
        order["id"], "confirmed", allowed_from=("new", "awaiting_payment", "paid_claimed")
    )
    if not moved:
        _redirect(f"/orders/{order['id']}", err="status")

    delivered = await _notify(request, order["client_id"], client_confirmed_text(order))
    _result(order["id"], "confirmed", delivered)


async def order_ship(request: web.Request) -> web.Response:
    """Ввод накладной: заказ уезжает, клиент получает её номер."""
    order = await _order_or_404(request)
    data = await request.post()
    ttn = forms.text(data, "ttn", max_len=40)
    if not ttn:
        _redirect(f"/orders/{order['id']}", err="ttn_empty")

    if not await queries.set_order_ttn(order["id"], ttn):
        _redirect(f"/orders/{order['id']}", err="status")

    delivered = await _notify(request, order["client_id"], client_shipped_text(order, ttn))
    _result(order["id"], "shipped", delivered)


async def order_finish(request: web.Request) -> web.Response:
    """«Выполнен» — посылку забрали. Клиенту не пишем: он и так всё знает."""
    order = await _order_or_404(request)
    if not await queries.advance_order_status(order["id"], "done", allowed_from=("shipped",)):
        _redirect(f"/orders/{order['id']}", err="status")
    _redirect(f"/orders/{order['id']}", ok="done")


async def order_cancel(request: web.Request) -> web.Response:
    """Отмена с возвратом остатков. Причина остаётся внутри, клиенту не уходит."""
    order = await _order_or_404(request)
    data = await request.post()
    reason = forms.text(data, "reason", max_len=200)
    who = _manager(request)
    label = f"Отменён в CRM ({who})" if who else "Отменён в CRM"
    note = f"{label} · {reason}" if reason else label

    if not await queries.cancel_order(order["id"], note=note):
        _redirect(f"/orders/{order['id']}", err="cancel_twice")

    delivered = await _notify(request, order["client_id"], client_cancelled_text(order))
    _result(order["id"], "cancelled", delivered)


async def order_take(request: web.Request) -> web.Response:
    """«Беру» — заказ подписывается именем менеджера.

    Захват условный: если заказ уже кто-то взял, второй менеджер получает отказ,
    а не молча перебивает чужое имя. Имя заодно запоминается в браузере.
    """
    order = await _order_or_404(request)
    data = await request.post()
    name = forms.text(data, "manager") or _manager(request)
    if not name:
        _redirect(f"/orders/{order['id']}", err="manager_empty")

    taken, current = await queries.take_order(order["id"], name)
    location = f"/orders/{order['id']}?" + urlencode(
        {"ok": "taken"} if taken else {"err": "taken"}
    )
    response = web.HTTPFound(location)
    response.set_cookie(
        MANAGER_COOKIE, name, max_age=MANAGER_COOKIE_MAX_AGE,
        httponly=True, samesite="Lax", path="/",
    )
    if not taken:
        logger.info("Заказ %s уже ведёт %s, отказ для %s", order["id"], current, name)
    raise response


async def order_release(request: web.Request) -> web.Response:
    """Снять с себя заказ — например, уходя со смены."""
    order = await _order_or_404(request)
    await queries.release_order(order["id"])
    _redirect(f"/orders/{order['id']}", ok="released")


async def order_note(request: web.Request) -> web.Response:
    """Внутренняя заметка: видна только в панели, клиенту не показывается."""
    order = await _order_or_404(request)
    data = await request.post()
    await queries.set_order_fields(order["id"], note=forms.text(data, "note", max_len=500))
    _redirect(f"/orders/{order['id']}", ok="note")


async def order_contacts(request: web.Request) -> web.Response:
    """Правка получателя и адреса — телефон с опечаткой правят чаще, чем кажется."""
    order = await _order_or_404(request)
    data = await request.post()
    await queries.set_order_fields(
        order["id"],
        name=forms.text(data, "name", max_len=100),
        phone=forms.text(data, "phone", max_len=30),
        city=forms.text(data, "city", max_len=100),
        np_branch=forms.text(data, "np_branch", max_len=120),
        comment=forms.text(data, "comment", max_len=300),
    )
    _redirect(f"/orders/{order['id']}", ok="saved")


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/orders", orders_list)
    app.router.add_get(r"/orders/{id:\d+}", order_card)
    app.router.add_post(r"/orders/{id:\d+}/confirm", order_confirm)
    app.router.add_post(r"/orders/{id:\d+}/ship", order_ship)
    app.router.add_post(r"/orders/{id:\d+}/done", order_finish)
    app.router.add_post(r"/orders/{id:\d+}/cancel", order_cancel)
    app.router.add_post(r"/orders/{id:\d+}/take", order_take)
    app.router.add_post(r"/orders/{id:\d+}/release", order_release)
    app.router.add_post(r"/orders/{id:\d+}/note", order_note)
    app.router.add_post(r"/orders/{id:\d+}/contacts", order_contacts)
