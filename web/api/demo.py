"""Заказы одного гостя демо — для панели на сайте-портфолио.

Зачем это здесь. На портфолио стоит живая копия панели, и в ней показывают,
как заявка из чата сразу попадает в CRM. Но убедительнее всего это выглядит,
когда человек пишет настоящему боту у себя в телефоне и видит свой заказ на
сайте. Для этого сайту нужно спросить у бота: «покажи заказы вот этого гостя».

Почему открыто без пароля и почему это безопасно:

  * Отдаём заказы РОВНО одного человека — того, чья метка передана. Метку
    ставит сам бот в /start, когда гость приходит по ссылке с портфолио
    (см. handlers/client.py). У каждого посетителя сайта она своя, случайная и
    живёт у него в браузере.
  * Метку не подобрать: это uuid. Перебор чужих ничего не даст — ответ на
    неизвестную метку пустой, и по нему нельзя понять, существует она или нет.
  * Ничего не меняем: только чтение. Ни статусов, ни товаров, ни переписки.
  * Отдаём минимум: состав, сумму, статус и куда везём. Телефон гостя не
    возвращаем — сайту он не нужен, а лежать в чужом браузере ему незачем.

То есть человек видит свой собственный заказ и ничей больше. Общий список
заказов магазина за этой ручкой недоступен ни при каком запросе.
"""
from __future__ import annotations

from aiohttp import web

from db import queries
from services import format
from web.api.helpers import ok

# Сколько заказов гостя показываем. Демо — это одна-две покупки; всё, что
# сверху, лишний груз в ответе.
FEED_LIMIT = 10

# Длина метки. Формат — uuid из браузера, но проверяем размер, а не вид:
# ручка открытая, и на вход может прийти что угодно.
TOKEN_MAX = 64


async def _card(order: dict) -> dict:
    """Заказ в том виде, в каком его рисует панель на сайте."""
    items = await queries.demo_order_items(order["id"])
    return {
        "items": [
            {
                "product_id": item["product_id"],
                "title": item["title_snapshot"],
                "variant": " ".join(x for x in (item["size"], item["color"]) if x),
                "qty": item["qty"],
            }
            for item in items
        ],
        "id": order["id"],
        "status": order["status"],
        "name": order["name"] or "",
        "city": order["city"] or "",
        "np_branch": order["np_branch"] or "",
        "ttn": order["ttn"] or "",
        "comment": order["comment"] or "",
        "total": order["total"],
        "total_text": format.money(order["total"]),
        "units_count": order["units_count"],
        "items_text": order["items_text"],
        "created_at": order["created_at"],
        "paid_at": order["paid_at"],
        "confirmed_at": order["confirmed_at"],
        "shipped_at": order["shipped_at"],
    }


async def orders(request: web.Request) -> web.Response:
    token = (request.query.get("token") or "").strip()[:TOKEN_MAX]
    if not token:
        return ok({"orders": []})

    rows = await queries.orders_by_demo_token(token, limit=FEED_LIMIT)
    return ok({"orders": [await _card(row) for row in rows]})


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/api/demo/orders", orders)
