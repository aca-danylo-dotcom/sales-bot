"""Раздел «Статистика»: что происходит с магазином за выбранный период.

Отдельно от «Сводки» намеренно. Сводка отвечает на вопрос «за что взяться прямо
сейчас» и живёт сегодняшним днём; здесь — другой вопрос: растём или падаем, что
покупают, где теряются заказы, окупается ли бот. Поэтому и период тут выбирают
руками, а не берут за сегодня.

Весь раздел уезжает одним ответом. Дробить на пять запросов незачем: считается
всё по одной базе и за доли секунды, а панели проще показать экран целиком, чем
собирать его из кусков, приезжающих вразнобой.

Деньги и склонения форматирует сервер — как и везде в проекте: «2 400 грн» в
панели, в отчёте и в сообщении бота должны быть написаны одинаково.
"""
from __future__ import annotations

from datetime import date, timedelta

from aiohttp import web

import config
from db import queries
from services import format
from web import forms
from web.api.helpers import ok

# Сколько дней показывает раздел, если период не выбирали. Месяц — то, чем
# меряют торговлю: неделя слишком дёргается, квартал прячет свежие перемены.
DEFAULT_DAYS = 30

# Дальше этого в прошлое не пускаем: запрос за десять лет по SQLite посчитается,
# но график из трёх тысяч столбиков читать невозможно.
MAX_DAYS = 366

TOP_LIMIT = 8
IDLE_LIMIT = 8
CANCELLED_LIMIT = 5


def _period(request: web.Request) -> tuple[str, str, int]:
    """Период из адреса. Что не разобралось — последние 30 дней.

    Перевёрнутый период (`from` позже `to`) не ошибка ввода, а обычная опечатка
    в адресе: молча меняем местами вместо отказа — человек хотел посмотреть эти
    две даты, порядок он не имел в виду.
    """
    today = config.today_local()
    start = forms.date_value(request.query.get("from"))
    end = forms.date_value(request.query.get("to"))

    date_to = date.fromisoformat(end) if end else today
    date_from = (
        date.fromisoformat(start) if start else date_to - timedelta(days=DEFAULT_DAYS - 1)
    )
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    if (date_to - date_from).days > MAX_DAYS:
        date_from = date_to - timedelta(days=MAX_DAYS)

    days = (date_to - date_from).days + 1
    return date_from.isoformat(), date_to.isoformat(), days


def _share(part: float, whole: float) -> int:
    """Доля в процентах, целым числом. Ноль от нуля — ноль, а не деление на ноль."""
    return round(part / whole * 100) if whole else 0


def _fill_days(rows: list[dict], date_from: str, date_to: str) -> list[dict]:
    """Достраивает дни без заказов нулями.

    База отдаёт только дни, в которые что-то происходило. Если оставить как
    есть, неделя с двумя заказами нарисуется двумя столбиками вплотную и будет
    выглядеть плотнее, чем была: пустой вторник — это ноль, а не отсутствие дня.
    """
    known = {row["day"]: row for row in rows}
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)

    days = []
    current = start
    while current <= end:
        key = current.isoformat()
        row = known.get(key, {"day": key, "orders": 0, "revenue": 0})
        days.append({
            "day": key,
            # Подпись столбика: «29.07». Год в графике за месяц — лишний шум.
            "label": f"{current.day:02d}.{current.month:02d}",
            "orders": row["orders"],
            "revenue": round(row["revenue"], 2),
            "revenue_text": format.money(row["revenue"]),
        })
        current += timedelta(days=1)
    return days


async def _sales(date_from: str, date_to: str) -> dict:
    """Четыре числа про деньги — и все они разные.

    `live` — сколько людей дошло до оформления, `paid_revenue` — сколько денег
    подтвердили. Расхождение между ними не ошибка, а неоплаченные заказы, и
    панель подписывает это словами: иначе первое же сравнение с банковской
    выпиской заканчивается вопросом «почему цифры врут».
    """
    stats = await queries.orders_summary(date_from=date_from, date_to=date_to)
    live = stats["live"]
    return {
        "placed": live,
        "placed_text": f"{live} {format.plural(live, 'заказ', 'заказа', 'заказов')}",
        "revenue": stats["revenue"],
        "revenue_text": format.money(stats["revenue"]),
        "paid_revenue": stats["paid_revenue"],
        "paid_revenue_text": format.money(stats["paid_revenue"]),
        "cancelled": stats["cancelled"],
        "cancelled_share": _share(stats["cancelled"], stats["total"]),
        # Средний чек считаем по подтверждённым: делить неоплаченное на людей
        # значит рисовать выручку, которой не было.
        "average_text": format.money(stats["paid_revenue"] / live) if live else format.money(0),
    }


def _funnel(raw: dict) -> dict:
    """Ступени пути заказа, подписанные так, как их называют в панели."""
    steps = [
        ("Оформлен", raw["placed"]),
        ("Клиент оплатил", raw["paid_claimed"]),
        ("Оплата подтверждена", raw["confirmed"]),
        ("Отправлен", raw["shipped"]),
        ("Выполнен", raw["done"]),
    ]
    first = steps[0][1]
    return {
        "steps": [
            {
                "label": label,
                "value": value,
                "share": _share(value, first),
                # Свой текст значения: воронка иначе печатает «1,200» на
                # английский манер.
                "display": str(value),
            }
            for label, value in steps
        ],
        "cancelled": raw["cancelled"],
        "cancelled_text": format.money(raw["cancelled_sum"]),
    }


def _hours_text(hours: float | None) -> str:
    """«3 часа» или «2 дня» — то, как об этом говорят вслух."""
    if hours is None:
        return "—"
    if hours < 24:
        whole = max(1, round(hours))
        return f"{whole} {format.plural(whole, 'час', 'часа', 'часов')}"
    days = round(hours / 24)
    return f"{days} {format.plural(days, 'день', 'дня', 'дней')}"


async def _products(date_from: str, date_to: str) -> dict:
    """Что покупали и что лежало без движения.

    Доли считаем от всей выручки периода, а не от суммы восьми показанных:
    иначе круг всегда полный, и по нему не видно, что половину денег принесли
    товары, которые в топ не попали. Поэтому же — ломтик «остальные».
    """
    rows = await queries.top_products(date_from=date_from, date_to=date_to, limit=TOP_LIMIT)
    total = await queries.top_products_total(date_from=date_from, date_to=date_to)
    shown = sum(row["revenue"] for row in rows)

    top = [
        {
            "title": row["title"],
            "product_id": row["product_id"],
            "units": row["units"],
            "orders": row["orders"],
            "revenue": round(row["revenue"], 2),
            "revenue_text": format.money(row["revenue"]),
            "share": _share(row["revenue"], total["revenue"]),
        }
        for row in rows
    ]
    rest = round(total["revenue"] - shown, 2)
    if rest > 0.01:
        top.append({
            "title": "Остальные товары",
            "product_id": None,
            "units": total["units"] - sum(row["units"] for row in rows),
            "orders": 0,
            "revenue": rest,
            "revenue_text": format.money(rest),
            "share": _share(rest, total["revenue"]),
        })

    idle = await queries.idle_products(date_from=date_from, date_to=date_to, limit=IDLE_LIMIT)
    return {
        "top": top,
        "total_revenue": round(total["revenue"], 2),
        "total_revenue_text": format.money(total["revenue"]),
        "total_units": total["units"],
        "titles": total["titles"],
        "idle": [
            {**row, "price_text": format.money(row["price"])}
            for row in idle
        ],
        "zero_stock": await queries.count_zero_stock(),
    }


async def stats(request: web.Request) -> web.Response:
    date_from, date_to, days = _period(request)

    funnel_raw = await queries.order_funnel(date_from=date_from, date_to=date_to)
    delivery = await queries.delivery_stats(date_from=date_from, date_to=date_to)
    clients = await queries.clients_stats(date_from=date_from, date_to=date_to)
    cancelled = await queries.recent_cancelled(
        date_from=date_from, date_to=date_to, limit=CANCELLED_LIMIT
    )

    return ok({
        "date_from": date_from,
        "date_to": date_to,
        "days": days,
        "today": config.today_local().isoformat(),
        "sales": await _sales(date_from, date_to),
        "by_day": _fill_days(
            await queries.revenue_by_day(date_from=date_from, date_to=date_to),
            date_from, date_to,
        ),
        "funnel": _funnel(funnel_raw),
        "delivery": {
            "shipped": delivery["shipped"],
            "done": delivery["done"],
            "avg_hours_text": _hours_text(delivery["avg_hours"]),
        },
        "cancelled_orders": [
            {
                "id": row["id"],
                "name": row["name"] or "Без имени",
                "total_text": format.money(row["total"]),
                "note": row["note"] or "",
                "created_at": row["created_at"],
            }
            for row in cancelled
        ],
        "clients": {
            **clients,
            # Из скольких разговоров вышли заказы. Считается по людям, а не по
            # заказам: один покупатель с тремя заказами иначе дал бы конверсию
            # больше ста процентов.
            "conversion": _share(clients["talked_and_bought"], clients["talked"]),
        },
        "products": await _products(date_from, date_to),
    })


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/api/stats", stats)
