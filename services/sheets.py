"""Выгрузка данных магазина в Google Sheets (односторонняя: бот → таблица).

Два листа. «Заказы» — что и кому продали, с составом, суммой и накладной.
«Остатки» — весь склад по вариантам. Правки в таблице боту НЕ возвращаются:
каждая синхронизация перезаписывает листы данными из базы, поэтому вручную
исправленная в таблице ячейка затрётся при следующем прогоне. Менять остатки
нужно в веб-CRM (`/stock`), таблица — витрина для владельца и бухгалтерии.

Механизм — не сервис-аккаунт, а **Apps Script Web App**: внутри таблицы висит
скрипт с функцией doPost(e), опубликованный как веб-приложение. Бот шлёт ему
HTTPS POST с JSON {token, orders, stock}, скрипт проверяет токен и перезаписывает
оба листа. Ни Google Cloud, ни ключей, ни OAuth — настройка в
docs/GOOGLE_SHEETS.md. Запрос асинхронный (aiohttp), event loop не встаёт.

Модуль «мягкий»: URL/токен не заданы — пишет INFO и тихо выходит; любая ошибка
сети или скрипта логируется, но бота НЕ роняет. Магазин должен продавать и с
недоступной таблицей.
"""
from __future__ import annotations

import logging

import aiohttp

import config
from db import queries
from services.format import ORDER_STATUS_RU, variant_label

logger = logging.getLogger(__name__)

# Сколько ждём ответ веб-приложения, прежде чем считать прогон неудачным.
# Apps Script на большой таблице разгоняется секунды, отсюда и не 5.
_TIMEOUT_SECONDS = 30

ORDERS_HEADER = [
    "№", "Оформлен", "Статус", "Клиент", "Телефон", "Город", "Отделение",
    "Состав", "Позиций", "Сумма", "ТТН", "Менеджер", "Комментарий", "Заметка",
    "Оплатил", "Подтверждён", "Отправлен", "Канал", "Telegram ID",
]

STOCK_HEADER = [
    "Товар", "Категория", "Размер", "Цвет", "Остаток", "Цена",
    "На витрине", "Артикул товара", "Артикул варианта", "ID товара", "ID варианта",
]


def is_enabled() -> bool:
    """Настроена ли выгрузка — заданы ли и URL веб-приложения, и токен."""
    return bool(config.SHEETS_WEBHOOK_URL and config.SHEETS_WEBHOOK_TOKEN)


def _items_cell(items: list[dict]) -> str:
    """Состав заказа одной ячейкой: «Перчатки (12 oz / чёрный) × 2».

    Одной ячейкой, а не строкой на позицию, потому что лист «Заказы» читают
    по заказам: одна строка — один заказ, иначе сумму по столбцу не свести.
    """
    return "\n".join(
        f"{item['title_snapshot']} ({variant_label(item)}) × {item['qty']}"
        for item in items
    )


def _order_row(order: dict) -> list:
    """Строка листа «Заказы».

    Телефон отдаём строкой как есть: Sheets сам не тронет текст, а вот число
    из «+380…» сделал бы формулу. Суммы и количества — числами, чтобы по ним
    работали СУММ и сортировка.
    """
    return [
        order["id"],
        order["created_at"] or "",
        ORDER_STATUS_RU.get(order["status"], order["status"]),
        order["name"] or "",
        order["phone"] or "",
        order["city"] or "",
        order["np_branch"] or "",
        _items_cell(order["items"]),
        sum(item["qty"] for item in order["items"]),
        round(float(order["total"] or 0), 2),
        order["ttn"] or "",
        order["assignee"] or "",
        order["comment"] or "",
        order["note"] or "",
        order["paid_at"] or "",
        order["confirmed_at"] or "",
        order["shipped_at"] or "",
        order["channel"] or "",
        order["client_id"],
    ]


def _stock_row(variant: dict) -> list:
    """Строка листа «Остатки». ID оставляем: по ним ищут вариант в CRM."""
    return [
        variant["title"],
        variant["category"] or "",
        variant["size"] or "",
        variant["color"] or "",
        variant["stock"],
        round(float(variant["price"] or 0), 2),
        "да" if variant["is_active"] else "нет",
        variant["product_sku"] or "",
        variant["variant_sku"] or "",
        variant["product_id"],
        variant["id"],
    ]


def _build_payload(orders: list[dict], stock: list[dict]) -> dict:
    """Собирает JSON для скрипта: по блоку на лист, заголовок вместе с данными.

    Заголовки шлём из Python, чтобы вся логика колонок жила здесь, а скрипт
    оставался тупым «перезаписывателем»: добавить колонку — правка одного файла,
    без переразвёртывания веб-приложения (а оно ещё и меняет URL).
    """
    return {
        "token": config.SHEETS_WEBHOOK_TOKEN,
        "orders": {
            "sheet": "Заказы",
            "header": ORDERS_HEADER,
            "rows": [_order_row(order) for order in orders],
        },
        "stock": {
            "sheet": "Остатки",
            "header": STOCK_HEADER,
            "rows": [_stock_row(variant) for variant in stock],
        },
    }


async def sync_to_sheets() -> bool:
    """Собирает данные из БД и шлёт их в Apps Script Web App. Не роняет бота.

    True — таблица обновлена; False — выгрузка выключена или прогон не удался
    (причина уже в логе). Исключение наружу не выпускается никогда: вызывающая
    сторона — планировщик и старт бота, им падать не на чем.
    """
    if not is_enabled():
        logger.info(
            "Google Sheets: SHEETS_WEBHOOK_URL/TOKEN не заданы — выгрузка отключена "
            "(см. docs/GOOGLE_SHEETS.md)."
        )
        return False

    orders = await queries.get_orders_export()
    stock = await queries.get_stock_export()
    payload = _build_payload(orders, stock)

    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Apps Script на успешный doPost отвечает 302 на script.googleusercontent.com
            # с телом результата — allow_redirects (по умолчанию) даёт дойти до него.
            async with session.post(config.SHEETS_WEBHOOK_URL, json=payload) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logger.warning(
                        "Google Sheets: скрипт ответил HTTP %d. Проверьте SHEETS_WEBHOOK_URL "
                        "(должен кончаться на /exec) и публикацию веб-приложения. Ответ: %s",
                        resp.status, text[:300],
                    )
                    return False
                # Скрипт возвращает {"ok": true, ...} или {"ok": false, "error": "..."}.
                if '"ok":false' in text.replace(" ", "") or '"error"' in text:
                    logger.warning(
                        "Google Sheets: скрипт вернул ошибку. Часто это неверный "
                        "SHEETS_WEBHOOK_TOKEN. Ответ: %s", text[:300],
                    )
                    return False
                logger.info(
                    "Google Sheets: выгружено заказов — %d, строк склада — %d.",
                    len(payload["orders"]["rows"]), len(payload["stock"]["rows"]),
                )
                return True
    except aiohttp.ClientError:
        logger.warning("Google Sheets: сеть/HTTP ошибка выгрузки, пропускаем этот прогон.")
    except Exception:  # таймаут, неожиданное — не должно ронять бота
        logger.exception("Google Sheets: ошибка выгрузки, пропускаем этот прогон.")
    return False
