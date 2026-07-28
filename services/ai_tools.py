"""Инструменты, которые ИИ-продавец вызывает для работы с каталогом и корзиной.

Здесь: схемы инструментов (формат Responses API) и фабрика исполнителя,
привязанного к конкретному покупателю (`client_id`). Исполнитель ходит в БД
через `db.queries` и общается с хендлером через `ClientContext`.

Ключевое правило (то же, что в gym-bot): инструменты НИЧЕГО не отправляют
клиенту сами. Фотографии товаров нельзя вернуть модели — она их не увидит и не
перешлёт. Поэтому инструмент лишь помечает в `ClientContext.show_products`, о
каком товаре шла речь, а карточки с фото отправляет хендлер после ответа модели.
Так же решается «фото не дублируются»: список хранит каждый товар один раз.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from db import queries

# Сколько карточек с фото отправляем за один ответ. Больше — это уже спам в чат
# и лишние мегабайты у клиента, к тому же Telegram шлёт их заметно дольше.
MAX_CARDS = 3

# Ограничители размера ответа инструмента: каждый символ здесь — токены в платный
# ИИ, а модели для консультации хватает короткой выжимки.
_MAX_PRODUCTS = 5
_MAX_VARIANTS = 20
_MAX_DESCRIPTION = 300

# Человеческие названия статусов заказа — чтобы модель не переводила их сама
# и не выдумывала «в пути» там, где заказ ещё ждёт оплаты.
ORDER_STATUS_RU = {
    "new": "новый",
    "awaiting_payment": "ждёт оплаты",
    "paid_claimed": "клиент оплатил, ждём подтверждения",
    "confirmed": "оплата подтверждена, готовим к отправке",
    "shipped": "отправлен",
    "done": "выполнен",
    "cancelled": "отменён",
}


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _variant_brief(variant: dict) -> dict:
    """Вариант в том виде, в каком он нужен модели: чем брать, что есть, сколько."""
    return {
        "variant_id": variant["id"],
        "size": variant.get("size") or "",
        "color": variant.get("color") or "",
        "stock": variant.get("stock", 0),
    }


def _product_brief(product: dict, *, with_description: bool = False) -> dict:
    """Короткая выжимка товара для модели (без фото — их отправляет хендлер)."""
    variants = product.get("variants", [])
    brief = {
        "product_id": product["id"],
        "title": product["title"],
        "price": product["price"],
        "category": product.get("category") or "",
        "in_stock": [_variant_brief(v) for v in variants if v.get("stock", 0) > 0][:_MAX_VARIANTS],
        "out_of_stock": [
            {"size": v.get("size") or "", "color": v.get("color") or ""}
            for v in variants if not v.get("stock", 0)
        ][:_MAX_VARIANTS],
    }
    if with_description and product.get("description"):
        brief["description"] = product["description"][:_MAX_DESCRIPTION]
    return brief


def _matches(variant: dict, size: str | None, color: str | None) -> bool:
    """Подходит ли вариант под запрошенные размер и цвет.

    Размер сравниваем целиком (42 — это не 42.5), цвет — вхождением: клиент пишет
    «чёрные», а в базе лежит «чёрный».
    """
    if size and (variant.get("size") or "").strip().lower() != size.strip().lower():
        return False
    if color and color.strip().lower() not in (variant.get("color") or "").strip().lower():
        return False
    return True


# --- Схемы инструментов (Responses API) ---

TOOLS: list[dict] = [
    {
        "type": "function",
        "name": "search_products",
        "description": (
            "Найти товары в каталоге по запросу клиента. Вызывай ВСЕГДА, прежде чем "
            "называть товар, цену или наличие. Возвращает только товары, которые "
            "реально есть на витрине; пустой список означает, что такого у нас нет."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Слова клиента о товаре: 'перчатки', 'кроссовки для зала'",
                },
                "category": {
                    "type": "string",
                    "description": "Категория, если клиент её назвал: одежда, обувь, экипировка",
                },
                "size": {"type": "string", "description": "Размер, если клиент его назвал"},
                "color": {"type": "string", "description": "Цвет, если клиент его назвал"},
                "in_stock_only": {
                    "type": "boolean",
                    "description": "true (по умолчанию) — только то, что есть в наличии",
                },
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "get_product_details",
        "description": (
            "Подробности одного товара: описание, цена, все размеры и цвета с остатками. "
            "product_id берётся из search_products."
        ),
        "parameters": {
            "type": "object",
            "properties": {"product_id": {"type": "integer"}},
            "required": ["product_id"],
        },
    },
    {
        "type": "function",
        "name": "check_availability",
        "description": (
            "Проверить конкретный размер и цвет товара. Возвращает остаток по точному "
            "варианту и — если его нет — что есть у этого товара взамен."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
                "size": {"type": "string"},
                "color": {"type": "string"},
            },
            "required": ["product_id"],
        },
    },
    {
        "type": "function",
        "name": "add_to_cart",
        "description": (
            "Положить выбранный вариант товара в корзину клиента. variant_id берётся "
            "из search_products / get_product_details / check_availability. Вызывай "
            "только когда размер и цвет однозначно выбраны клиентом."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "variant_id": {"type": "integer"},
                "qty": {"type": "integer", "description": "Сколько штук, по умолчанию 1"},
            },
            "required": ["variant_id"],
        },
    },
    {
        "type": "function",
        "name": "view_cart",
        "description": "Показать корзину клиента: позиции, количество и сумму.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "remove_from_cart",
        "description": "Убрать позицию из корзины по variant_id (его отдаёт view_cart).",
        "parameters": {
            "type": "object",
            "properties": {"variant_id": {"type": "integer"}},
            "required": ["variant_id"],
        },
    },
    {
        "type": "function",
        "name": "save_profile",
        "description": (
            "Сохранить данные клиента для доставки: имя, город, отделение Новой Почты. "
            "Вызывай, как только клиент их назвал. Телефон здесь не сохраняется — его "
            "запрашивает оформление заказа отдельной кнопкой."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "city": {"type": "string"},
                "np_branch": {"type": "string", "description": "Отделение Новой Почты"},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "get_my_orders",
        "description": "Показать последние заказы этого клиента и их статусы.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


@dataclass
class ClientContext:
    """Состояние обработки одного сообщения, общее для исполнителя и хендлера."""

    client_id: int
    # Товары, о которых зашла речь: хендлер отправит по ним карточки с фото.
    show_products: list[int] = field(default_factory=list)
    cart_changed: bool = False

    def show(self, product_id: int | None) -> None:
        """Помечает товар к показу. Повторы игнорируются — фото не дублируются."""
        if product_id is None:
            return
        product_id = int(product_id)
        if product_id not in self.show_products and len(self.show_products) < MAX_CARDS:
            self.show_products.append(product_id)


def build_executor(ctx: ClientContext):
    """Возвращает async-исполнитель инструментов для покупателя ctx.client_id."""
    client_id = ctx.client_id

    async def execute(name: str, args: dict) -> str:
        if name == "search_products":
            return await _search(args)
        if name == "get_product_details":
            return await _details(args.get("product_id"))
        if name == "check_availability":
            return await _availability(args)
        if name == "add_to_cart":
            return await _add_to_cart(args)
        if name == "view_cart":
            return await _view_cart()
        if name == "remove_from_cart":
            return await _remove_from_cart(args.get("variant_id"))
        if name == "save_profile":
            return await _save_profile(args)
        if name == "get_my_orders":
            return await _my_orders()
        return _dump({"error": "unknown_tool"})

    async def _search(args: dict) -> str:
        products = await queries.search_products(
            (args.get("query") or "").strip() or None,
            category=(args.get("category") or "").strip() or None,
            size=(args.get("size") or "").strip() or None,
            color=(args.get("color") or "").strip() or None,
            in_stock_only=args.get("in_stock_only", True) is not False,
            limit=_MAX_PRODUCTS,
        )
        for product in products:
            ctx.show(product["id"])

        result = {"products": [_product_brief(p) for p in products]}
        if not products:
            # Пустой результат — самый рискованный момент: без подсказки модель
            # склонна «вспомнить» товар. Отдаём ей то, что есть на витрине,
            # чтобы было чем честно ответить вместо выдумки.
            result["found"] = 0
            result["available_categories"] = await queries.get_categories()
            result["note"] = ("Ничего не найдено. Такого товара в каталоге нет — "
                              "скажи об этом честно и предложи то, что есть.")
        return _dump(result)

    async def _details(product_id) -> str:
        if product_id is None:
            return _dump({"status": "error", "reason": "no_product_id"})

        product = await queries.get_product_full(int(product_id))
        if not product or not product["is_active"]:
            return _dump({"status": "not_found"})

        ctx.show(product["id"])
        brief = _product_brief(product, with_description=True)
        brief["status"] = "ok"
        brief["total_stock"] = product["total_stock"]
        brief["has_photos"] = bool(product["photos"])
        return _dump(brief)

    async def _availability(args: dict) -> str:
        product_id = args.get("product_id")
        if product_id is None:
            return _dump({"status": "error", "reason": "no_product_id"})

        product = await queries.get_product_full(int(product_id))
        if not product or not product["is_active"]:
            return _dump({"status": "not_found"})

        size = (args.get("size") or "").strip() or None
        color = (args.get("color") or "").strip() or None
        matched = [v for v in product["variants"] if _matches(v, size, color)]
        in_stock = [v for v in matched if v["stock"] > 0]
        in_stock_ids = {v["id"] for v in in_stock}

        ctx.show(product["id"])
        payload = {
            "status": "in_stock" if in_stock else ("out_of_stock" if matched else "no_such_variant"),
            "product_id": product["id"],
            "title": product["title"],
            "price": product["price"],
            "asked": {"size": size or "", "color": color or ""},
            "matched": [_variant_brief(v) for v in in_stock][:_MAX_VARIANTS],
            # Что есть у этого товара взамен — чтобы предложить альтернативу
            # из реального каталога, а не из головы.
            "alternatives": [
                _variant_brief(v) for v in product["variants"]
                if v["stock"] > 0 and v["id"] not in in_stock_ids
            ][:_MAX_VARIANTS],
        }
        return _dump(payload)

    async def _add_to_cart(args: dict) -> str:
        variant_id = args.get("variant_id")
        if variant_id is None:
            return _dump({"status": "error", "reason": "no_variant_id"})

        try:
            qty = int(args.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 1
        qty = max(1, min(qty, 20))

        variant = await queries.get_variant(int(variant_id))
        if not variant or not variant["is_active"]:
            return _dump({"status": "not_found"})

        status = await queries.add_to_cart(client_id, int(variant_id), qty)
        if status != "ok":
            # out_of_stock — просили больше, чем есть; отдаём остаток, чтобы
            # модель назвала клиенту точную цифру, а не «мало».
            return _dump({"status": status, "stock": variant["stock"],
                          "title": variant["title"], "size": variant["size"],
                          "color": variant["color"]})

        ctx.cart_changed = True
        cart = await queries.get_cart(client_id)
        return _dump({
            "status": "ok",
            "added": {"title": variant["title"], "size": variant["size"],
                      "color": variant["color"], "qty": qty},
            "cart_count": cart["count"],
            "cart_total": cart["total"],
        })

    async def _view_cart() -> str:
        cart = await queries.get_cart(client_id)
        return _dump({
            "items": [
                {
                    "variant_id": i["variant_id"], "title": i["title"],
                    "size": i["size"] or "", "color": i["color"] or "",
                    "qty": i["qty"], "price": i["price"], "sum": i["sum"],
                    # stock показывает, не разобрали ли товар, пока корзина лежала
                    "stock": i["stock"],
                }
                for i in cart["items"]
            ],
            "count": cart["count"],
            "total": cart["total"],
        })

    async def _remove_from_cart(variant_id) -> str:
        if variant_id is None:
            return _dump({"status": "error", "reason": "no_variant_id"})
        await queries.remove_from_cart(client_id, int(variant_id))
        ctx.cart_changed = True
        cart = await queries.get_cart(client_id)
        return _dump({"status": "ok", "cart_count": cart["count"], "cart_total": cart["total"]})

    async def _save_profile(args: dict) -> str:
        fields = {
            key: (args.get(key) or "").strip()[:100]
            for key in ("name", "city", "np_branch")
        }
        saved = {k: v for k, v in fields.items() if v}
        if not saved:
            return _dump({"status": "nothing_to_save"})

        await queries.update_client(client_id, **saved)
        return _dump({"status": "saved", "saved": list(saved)})

    async def _my_orders() -> str:
        orders = await queries.get_client_orders(client_id, limit=5)
        return _dump({"orders": [
            {
                "order_id": o["id"],
                "status": ORDER_STATUS_RU.get(o["status"], o["status"]),
                "total": o["total"],
                "created_at": o["created_at"],
                "ttn": o["ttn"] or "",
            }
            for o in orders
        ]})

    return execute
