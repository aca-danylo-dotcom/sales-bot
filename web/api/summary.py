"""Сводка: что заработали, за что взяться, что кончилось на складе.

Экран, который открывают первым утром. Логика та же, что была на серверной
странице, и два её правила никуда не делись:

1. Каждая цифра — ссылка на список, из которого посчитана. Поэтому метрика
   отдаёт готовый `href`: число без возможности посмотреть, из чего оно
   сложилось, менеджеру бесполезно.
2. «Сегодня» — по киевскому календарю (config.today_local), а не по UTC-полуночи
   SQLite: иначе вечерние заказы уезжали бы в завтрашний день.
"""
from __future__ import annotations

from datetime import timedelta

from aiohttp import web

import config
from db import queries
from services import format
from web.api.helpers import ok
# Короткие названия статусов живут в разделе заказов: две таблицы с одними и
# теми же словами однажды разъедутся, и «Ждёт проверки» в сводке окажется
# «Ждёт оплаты» в списке.
from web.api.orders import SHORT_STATUS

# Отправленные заказы, которые никто не закрыл дольше этого срока, попадают в
# «требует внимания»: обычно посылку давно забрали, а статус остался висеть.
STALE_SHIPPED_DAYS = 7

# Сколько закончившихся вариантов показываем списком. Дальше — ссылка на склад:
# сводка должна сообщать о проблеме, а не заменять собой раздел.
ZERO_STOCK_LIMIT = 8

# Сколько последних заказов показывает лента сводки. Пять — потому что это
# сообщения «что пришло», а не список заказов: он лежит в своём разделе.
RECENT_ORDERS_LIMIT = 5

# Разобранные заказы в ленту не идут: она отвечает на вопрос «что нового».
_CLOSED_STATUSES = ("done", "cancelled", "new")


def _period(title: str, days: int, hint: str) -> dict:
    """Описание периода: с какой даты считать и что написать под цифрой."""
    today = config.today_local()
    start = today - timedelta(days=days - 1)
    return {
        "title": title,
        "hint": hint,
        "date_from": start.isoformat(),
        "date_to": today.isoformat(),
    }


async def _metrics() -> list[dict]:
    """Заказы и выручка за сегодня, неделю и месяц."""
    periods = [
        _period("Сегодня", 1, "с начала дня"),
        _period("7 дней", 7, "включая сегодня"),
        _period("30 дней", 30, "включая сегодня"),
    ]
    metrics = []
    for period in periods:
        stats = await queries.orders_summary(
            date_from=period["date_from"], date_to=period["date_to"]
        )
        live = stats["live"]
        metrics.append({
            **period,
            **stats,
            "revenue_text": format.money(stats["revenue"]),
            # Склонение считаем здесь же: «3 заказов» на видном месте читается
            # как небрежность, а правило одно на весь проект (services/format).
            "live_text": f"{live} {format.plural(live, 'заказ', 'заказа', 'заказов')}",
            # Ссылка ведёт в «Заказы» с тем же периодом: цифра и список сходятся,
            # потому что фильтр буквально один и тот же.
            "href": f"/orders?from={period['date_from']}&to={period['date_to']}",
        })
    return metrics


async def _attention() -> list[dict]:
    """Что ждёт человека. Пустые пункты не показываем — это работа, а не отчёт.

    `link_hint` появляется там, где список шире цифры: зависшие в пути считаются
    по сроку, а отбора «дольше недели» в разделе «Заказы» нет, и открывается он
    целиком. Лучше честно предупредить, чем позволить решить, что цифра врёт.
    """
    counts = await queries.count_orders_by_status()
    rows = [
        {
            "title": "Ждут проверки оплаты",
            "hint": "клиент нажал «Я оплатил»",
            "count": counts.get("paid_claimed", 0),
            "href": "/orders?status=paid_claimed",
            "link_hint": "",
            "urgent": True,
        },
        {
            "title": "К отправке",
            "hint": "оплата подтверждена, накладной ещё нет",
            "count": counts.get("confirmed", 0),
            "href": "/orders?status=confirmed",
            "link_hint": "",
            "urgent": False,
        },
        {
            "title": f"В пути дольше {STALE_SHIPPED_DAYS} дней",
            "hint": "возможно, посылку уже забрали, а заказ не закрыт",
            "count": await queries.count_stale_shipped(STALE_SHIPPED_DAYS),
            "href": "/orders?status=shipped",
            "link_hint": "откроются все отправленные",
            "urgent": False,
        },
    ]
    return [row for row in rows if row["count"]]


async def _zero_stock() -> list[dict]:
    """Закончившиеся размеры с готовой подписью варианта."""
    rows = await queries.zero_stock_variants(limit=ZERO_STOCK_LIMIT)
    return [{**row, "variant": format.variant_label(row)} for row in rows]


async def _recent_orders() -> list[dict]:
    """Последние пришедшие заказы — лента «что случилось, пока меня не было».

    Отменённые и закрытые сюда не берём: лента отвечает на вопрос «что нового»,
    а закрытый заказ — это уже разобранное дело. Заведённые вручную в CRM
    (статус `new`) тоже пропускаем — объявлять человеку его же действие незачем,
    ровно как во всплывающих карточках (см. web/api/notifications.py).
    """
    rows = await queries.list_orders(limit=RECENT_ORDERS_LIMIT * 3)
    fresh = [row for row in rows if row["status"] not in _CLOSED_STATUSES][:RECENT_ORDERS_LIMIT]
    return [
        {
            "id": row["id"],
            "name": row["name"] or "Без имени",
            "total_text": format.money(row["total"]),
            "status": row["status"],
            "status_short": SHORT_STATUS.get(row["status"], row["status"]),
            "created_at": row["created_at"],
            "units_count": row["units_count"],
        }
        for row in fresh
    ]


async def dashboard(request: web.Request) -> web.Response:  # noqa: ARG001
    return ok({
        "shop_name": config.SHOP_NAME,
        "today": config.today_local().isoformat(),
        "metrics": await _metrics(),
        "attention": await _attention(),
        "recent_orders": await _recent_orders(),
        "zero_stock": await _zero_stock(),
        "zero_stock_total": await queries.count_zero_stock(),
        "zero_stock_limit": ZERO_STOCK_LIMIT,
    })


async def meta(request: web.Request) -> web.Response:  # noqa: ARG001
    """Название магазина и валюта — то, что панель показывает на каждом экране.

    Отдельным запросом, а не вшито в собранный index.html: сборка одна на все
    установки, а магазин у каждой свой.
    """
    return ok({"shop_name": config.SHOP_NAME, "currency": config.SHOP_CURRENCY})


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/api/meta", meta)
    app.router.add_get("/api/summary", dashboard)
