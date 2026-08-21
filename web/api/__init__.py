"""JSON-API веб-CRM: всё, что раньше отдавали Jinja-страницы.

Панель стала одностраничным приложением на React, поэтому сервер больше не
собирает HTML — он отдаёт те же данные в JSON, а рисует их браузер. Разделение
простое: `/api/...` — данные и действия, всё остальное — файлы сборки.

Правила, общие для всего пакета:

  * Форматирование остаётся на сервере (`services.format`). «2 400 грн» в панели
    и в сообщении клиенту должны читаться одинаково, а склонения по-русски
    незачем писать второй раз на TypeScript.
  * Результат действия — не редирект с кодом в адресе, а поле `message`
    (или `warning`) прямо в ответе. Тексты берутся из тех же словарей, что и
    раньше: их формулировки проверены работой.
  * Отказ — HTTP-код и `{"error": "текст"}`. Клиент показывает текст как есть,
    поэтому сочинять его на фронте не приходится.
"""
from __future__ import annotations

from aiohttp import web

from web.api import (
    demo,
    notifications,
    orders,
    products,
    session,
    shop,
    stats,
    summary,
)


def setup_routes(app: web.Application) -> None:
    """Все роуты API. Порядок важен только внутри модулей (см. products)."""
    # Вход — первым: остальные разделы за ним и открываются.
    session.setup_routes(app)
    summary.setup_routes(app)
    orders.setup_routes(app)
    products.setup_routes(app)
    notifications.setup_routes(app)
    stats.setup_routes(app)
    # Заказы гостя демо для сайта-портфолио. Открыт без пароля, но отдаёт
    # заказы ровно одного человека — того, чья метка передана (см. demo.py).
    demo.setup_routes(app)
    # Магазин для покупателя. Свой префикс /api/shop/ — по нему web/auth.py и
    # отличает витрину от панели: у них разные правила доступа.
    shop.setup_routes(app)
