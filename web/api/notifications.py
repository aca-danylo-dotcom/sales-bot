"""Уведомления о новых заказах — карточка в углу экрана.

То, ради чего панель и переезжала на React: заказ пришёл из бота, а менеджер
сидит в другой вкладке и узнаёт об этом через полчаса.

Как устроено и почему так:

  * Указатель «что уже видел» хранит браузер (localStorage), а не сервер. Входа
    в панель нет, пользователей нет — держать эту отметку негде и не для кого.
  * Указатель — номер заказа, а не время. `id` растёт монотонно и не зависит ни
    от часов на сервере, ни от часов в браузере; по времени карточки то
    задваивались бы, то пропадали.
  * Первый заход (запроса без `since`) отдаёт только отсечку и пустой список.
    Иначе на новом рабочем месте разом высыпалась бы вся история заказов.
  * Заказы со статусом `new` не показываем: их заводит менеджер прямо в CRM,
    объявлять человеку его же действие незачем.
"""
from __future__ import annotations

from aiohttp import web

from db import queries
from services import format
from web import forms
from web.api.helpers import ok

# Сколько заказов отдаём за раз. Если панель не открывали сутки, показывать
# сорок карточек подряд бессмысленно — покажем последние и подвинем отсечку.
FEED_LIMIT = 10


def _card(order: dict) -> dict:
    """Карточка уведомления: имя, город, сумма — и всё готовыми строками."""
    units = order["units_count"]
    return {
        "id": order["id"],
        "name": order["name"] or "Без имени",
        "city": order["city"] or "",
        "total_text": format.money(order["total"]),
        "units_text": f"{units} {format.plural(units, 'товар', 'товара', 'товаров')}",
        "created_at": order["created_at"],
    }


async def feed(request: web.Request) -> web.Response:
    since = forms.integer(request.query.get("since"), maximum=10_000_000)
    if since is None:
        # Отсечка «с этого момента»: договариваемся о номере и молчим.
        return ok({"last_id": await queries.max_order_id(), "orders": []})

    orders = await queries.orders_since(since, limit=FEED_LIMIT)
    # last_id считаем по самому свежему заказу вообще, а не по последнему из
    # выдачи: иначе при переполнении лимита остаток сыпался бы порциями.
    return ok({
        "last_id": await queries.max_order_id(),
        "orders": [_card(order) for order in orders],
    })


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/api/notifications", feed)
