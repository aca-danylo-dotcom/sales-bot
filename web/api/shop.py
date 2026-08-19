"""Магазин для покупателя: витрина, корзина, оформление, свои заказы.

Второй клиент к той же базе. Всё, что здесь есть, бот уже умеет делать в чате
(handlers/catalog.py, handlers/orders.py) — поэтому ни одного нового запроса к
базе в этом файле нет: и витрина, и корзина, и создание заказа берутся из
db/queries.py теми же функциями. Иначе покупка из мини-приложения и покупка из
чата разошлись бы в мелочах: где-то списался остаток, где-то сгорел промокод.

Кто спрашивает — известно из подписи Telegram (web/auth.py), а не из тела
запроса. Поэтому client_id сюда не передаётся: его нельзя подделать, и чужую
корзину открыть не получится.

Отличие от админского API рядом: здесь наружу не уходит ничего лишнего. Клиент
видит цену и остаток, но не видит себестоимости, чужих заказов и телефонов.
"""
from __future__ import annotations

import logging

from aiohttp import web

import config
from db import queries
from services import format, payments
from web.api.helpers import body, fail, not_found, ok
from web.auth import is_admin, user_of

logger = logging.getLogger(__name__)

# Сколько товаров отдаём за раз. Витрина листается, но в мини-приложении на
# телефоне длинный список никто не крутит — важнее, чтобы первый экран пришёл
# быстро.
PAGE_SIZE = 24

# Ответы add_to_cart/set_cart_qty человеческим языком. Тексты те же, что в чате:
# один и тот же отказ не должен звучать в витрине иначе, чем у бота.
_CART_ERRORS = {
    "not_found": "Этого товара больше нет в продаже.",
    "out_of_stock": "Столько нет на складе — остались единицы.",
}

_PROMO_ANSWERS = {
    "ok": "Промокод принят — скидка применится к заказу.",
    "active": "Этот промокод уже применён к вашему заказу.",
    "unknown": "Такого промокода нет.",
    "expired": "Срок действия промокода истёк.",
    "used": "Этот промокод уже использован.",
}


def _client_id(request: web.Request) -> int:
    """Telegram-id покупателя из подписанных данных. До сюда без него не дойти."""
    return int(user_of(request)["id"])


async def _known_client_id(request: web.Request) -> int:
    """То же, но с гарантией, что клиент заведён в базе.

    Нужно везде, где мы что-то за клиентом ЗАПИСЫВАЕМ: корзина и заказы
    ссылаются на clients внешним ключом, и для незнакомого человека запись
    падает. В чате такого не бывает — там знакомство происходит на /start, —
    а мини-приложение открывают и сразу кладут товар в корзину, ни слова боту
    не написав.
    """
    client_id = _client_id(request)
    await queries.ensure_client(client_id)
    return client_id


def _label(variant: dict) -> str:
    """Подпись варианта: «42», «12 oz / чёрный» — или пусто.

    В чате пустая подпись заменяется словами «один вариант»: там строка стоит
    посреди текста, и дырка в перечислении выглядит как потерянные данные.
    В витрине наоборот — подпись живёт отдельной строкой под названием, и
    «один вариант» под гантелями сообщает ровно ничего. Пустое значение к тому
    же говорит приложению, что выбирать не из чего, и шаг выбора размера
    пропадает сам.
    """
    return " / ".join(part for part in (variant.get("size"), variant.get("color")) if part)


def _photo_urls(photo_ids: list[int]) -> list[str]:
    """Адреса фотографий. Отдаёт сервер, тот же /media/<id>, что и панель."""
    return [f"/media/{pid}" for pid in photo_ids if pid]


def _product_brief(row: dict) -> dict:
    """Товар для списка: только то, что рисуется в плитке витрины."""
    return {
        "id": row["id"],
        "title": row["title"],
        "category": row["category"],
        "price": row["price"],
        "price_text": format.money(row["price"]),
        "old_price": row.get("old_price"),
        "old_price_text": format.money(row["old_price"]) if row.get("old_price") else None,
        "in_stock": bool(row.get("total_stock", 0)),
        "photo": f"/media/{row['main_photo_id']}" if row.get("main_photo_id") else None,
    }


async def meta(request: web.Request) -> web.Response:
    """Всё, что нужно приложению на старте: магазин, роль, профиль, корзина.

    Одним запросом, а не четырьмя: мини-приложение открывается поверх чата, и
    первый экран должен появиться сразу, а не собираться из очереди ответов.

    Роль отдаём сервером. Фронт мог бы и сам сравнить id с админским, но тогда
    достаточно было бы подправить пару строк в браузере, чтобы увидеть кнопки
    CRM. Данных они, правда, всё равно не получат — их закрывает web/auth.py, —
    но показывать чужому интерфейс владельца незачем.
    """
    user = user_of(request)
    client_id = int(user["id"])
    await queries.ensure_client(client_id)

    client = await queries.get_client(client_id) or {}
    cart = await queries.get_cart(client_id)
    promo = await queries.active_promo(client_id)

    return ok(
        shop={
            "name": config.SHOP_NAME,
            "city": config.SHOP_CITY,
            "currency": config.SHOP_CURRENCY,
            "delivery": config.DELIVERY_INFO,
            "payment": config.PAYMENT_INFO,
            "returns": config.RETURN_INFO,
        },
        role="admin" if is_admin(user) else "client",
        # Профиль подставляется в форму заказа: человек, купивший раз, не должен
        # второй раз набирать имя и отделение почты с телефона.
        profile={
            "name": client.get("name") or user.get("first_name") or "",
            "phone": client.get("phone") or "",
            "city": client.get("city") or "",
            "np_branch": client.get("np_branch") or "",
        },
        cart_count=cart["count"],
        promo={"code": promo["code"], "percent": promo["percent"]} if promo else None,
    )


async def catalog(request: web.Request) -> web.Response:
    """Витрина: страница товаров и список категорий.

    Скрытые товары (is_active = 0) сюда не попадают никогда — за это отвечает
    status='active'. Распроданные показываем: карточка честно скажет «нет в
    наличии», и это лучше, чем товар, молча исчезнувший из каталога.
    """
    params = request.query
    try:
        page = max(1, int(params.get("page", "1")))
    except ValueError:
        page = 1

    query = (params.get("q") or "").strip() or None
    category = (params.get("category") or "").strip() or None

    rows = await queries.list_products(
        query=query,
        category=category,
        status="active",
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
    )
    total = await queries.count_products(query=query, category=category, status="active")

    return ok(
        products=[_product_brief(row) for row in rows],
        categories=await queries.get_categories(),
        total=total,
        page=page,
        has_more=page * PAGE_SIZE < total,
    )


async def product_card(request: web.Request) -> web.Response:
    """Карточка товара: фото, описание, варианты с остатками."""
    product = await queries.get_product_full(int(request.match_info["id"]))
    if not product or not product["is_active"]:
        raise not_found("Такого товара нет.")

    return ok(
        product={
            **_product_brief({**product, "main_photo_id": None}),
            "description": product["description"],
            "photos": _photo_urls([photo["id"] for photo in product["photos"]]),
            # Варианты без остатка отдаём тоже: покупатель должен видеть, что
            # 43-й размер существует, но сейчас разобран, — иначе он решит, что
            # такого размера у магазина не бывает вовсе.
            "variants": [
                {
                    "id": variant["id"],
                    "label": _label(variant),
                    "size": variant["size"],
                    "color": variant["color"],
                    "stock": variant["stock"],
                }
                for variant in product["variants"]
            ],
        }
    )


async def cart_view(request: web.Request) -> web.Response:
    """Корзина с суммой и — если код принят — с посчитанной скидкой."""
    client_id = _client_id(request)
    cart = await queries.get_cart(client_id)
    promo = await queries.active_promo(client_id)

    discount = round(cart["total"] * promo["percent"] / 100, 2) if promo else 0.0
    return ok(
        items=[
            {
                "variant_id": item["variant_id"],
                "product_id": item["product_id"],
                "title": item["title"],
                "label": _label(item),
                "qty": item["qty"],
                "stock": item["stock"],
                "price": item["price"],
                "sum": item["sum"],
                "sum_text": format.money(item["sum"]),
            }
            for item in cart["items"]
        ],
        count=cart["count"],
        subtotal=cart["total"],
        subtotal_text=format.money(cart["total"]),
        discount=discount,
        discount_text=format.money(discount) if discount else None,
        total=round(cart["total"] - discount, 2),
        total_text=format.money(round(cart["total"] - discount, 2)),
        promo={"code": promo["code"], "percent": promo["percent"]} if promo else None,
    )


async def cart_add(request: web.Request) -> web.Response:
    """Добавить вариант в корзину. qty может быть отрицательным — это убавление."""
    data = await body(request)
    try:
        variant_id = int(data.get("variant_id", 0))
        qty = int(data.get("qty", 1))
    except (TypeError, ValueError):
        return fail("Не понял, какой товар добавить.")
    if not variant_id:
        return fail("Не понял, какой товар добавить.")

    result = await queries.add_to_cart(
        await _known_client_id(request), variant_id, qty, channel="telegram"
    )
    if result != "ok":
        return fail(_CART_ERRORS.get(result, "Не получилось добавить товар."))
    return await cart_view(request)


async def cart_set_qty(request: web.Request) -> web.Response:
    """Явное количество позиции. Ноль и меньше — убрать позицию совсем."""
    data = await body(request)
    try:
        variant_id = int(data.get("variant_id", 0))
        qty = int(data.get("qty", 0))
    except (TypeError, ValueError):
        return fail("Не понял, сколько штук нужно.")
    if not variant_id:
        return fail("Не понял, какой товар менять.")

    result = await queries.set_cart_qty(await _known_client_id(request), variant_id, qty)
    if result != "ok":
        return fail(_CART_ERRORS.get(result, "Не получилось изменить количество."))
    return await cart_view(request)


async def cart_clear(request: web.Request) -> web.Response:
    await queries.clear_cart(_client_id(request))
    return await cart_view(request)


async def promo_apply(request: web.Request) -> web.Response:
    """Применить промокод. Чужой и несуществующий отвечают одинаково — см. queries."""
    data = await body(request)
    code = str(data.get("code", "")).strip()
    if not code:
        return fail("Введите промокод.")

    result, _ = await queries.activate_promo(await _known_client_id(request), code)
    if result not in ("ok", "active"):
        return fail(_PROMO_ANSWERS.get(result, "Промокод не подошёл."))

    response = await cart_view(request)
    return response


# Границы полей заказа — те же, что в форме бота (handlers/orders.py): верхние,
# чтобы в накладную Новой Почты не уехала простыня, нижние — чтобы не приняли
# строку из одного символа.
_LIMITS = {"name": (2, 60), "city": (2, 60), "np_branch": (1, 100), "comment": (0, 300)}


def _checkout_fields(data) -> tuple[dict, str | None]:
    """Проверяет данные доставки. Возвращает (поля, текст ошибки).

    Проверки те же и в том же порядке, что в чате: телефон нормализуется к
    +380…, отделение обязано содержать номер, имя проверяется на отговорки.
    Имя, в отличие от чата, здесь не переспрашивается трижды — форму человек
    видит целиком и правит поле на месте.
    """
    values = {}
    for field, (low, high) in _LIMITS.items():
        value = " ".join(str(data.get(field, "") or "").split())
        if len(value) > high:
            return {}, f"Слишком длинно — уложитесь в {high} символов."
        if len(value) < low:
            return {}, {
                "name": "Напишите имя и фамилию получателя.",
                "city": "Укажите город доставки.",
                "np_branch": "Укажите отделение Новой Почты.",
            }.get(field, "Заполните поле.")
        values[field] = value

    if not format.looks_like_name(values["name"]):
        return {}, "Похоже, это не имя получателя. Напишите имя и фамилию."

    phone = format.clean_phone(str(data.get("phone", "")))
    if not phone:
        return {}, "Проверьте номер телефона — по нему свяжется курьер."
    values["phone"] = phone

    if not format.looks_like_branch(values["np_branch"]):
        return {}, ("В отделении Новой Почты должен быть номер — например «12» "
                    "или «Поштомат 4521».")

    return values, None


async def checkout(request: web.Request) -> web.Response:
    """Оформляет заказ из корзины и запускает оплату.

    Заказ создаёт та же функция, что и чат (queries.create_order): остатки
    списываются под блокировкой, промокод гасится в той же транзакции, цены
    сохраняются снимком. Мини-приложение не знает об этом ничего — и не должно.

    Дальше развилка. Подключён платёжный провайдер — шлём счёт, и человек
    платит картой прямо в Telegram. Не подключён — уходят реквизиты карты с
    кнопкой «Я оплатил», ровно как раньше. Фронт об этом узнаёт из поля `mode`:
    в первом случае он закрывает окно (счёт уже в чате), во втором — говорит
    вернуться в переписку.

    Сообщения шлёт бот, который лежит в приложении (app["bot"]). Если панель
    подняли без бота — заказ всё равно создан и виден в CRM, а клиенту про
    оплату расскажет ответ этого запроса.
    """
    client_id = await _known_client_id(request)
    data = await body(request)

    values, error = _checkout_fields(data)
    if error:
        return fail(error)

    # Профиль обновляем ДО создания заказа: если человек поправил отделение,
    # следующий заказ должен подставить новое, даже если этот сорвётся.
    await queries.update_client(
        client_id,
        name=values["name"],
        phone=values["phone"],
        city=values["city"],
        np_branch=values["np_branch"],
    )

    status, order_id = await queries.create_order(
        client_id,
        name=values["name"],
        phone=values["phone"],
        city=values["city"],
        np_branch=values["np_branch"],
        comment=values["comment"],
        channel="telegram",
    )
    if status == "empty_cart":
        return fail("Корзина пуста.")
    if status == "out_of_stock":
        return fail(
            "Пока вы оформляли, что-то из корзины разобрали. Откройте корзину — "
            "там видно, чего не хватает."
        )

    order = await queries.get_order_full(order_id)
    bot = request.app.get("bot")
    if bot is None:
        logger.error("Заказ №%s создан, но бота нет — счёт не отправлен", order_id)
        return ok(order_id=order_id, mode="none",
                  message=f"Заказ №{order_id} оформлен. Мы напишем вам в чат.")

    if await payments.send_invoice(bot, order):
        return ok(
            order_id=order_id,
            mode="invoice",
            message=f"Счёт на {format.money(order['total'])} отправлен в чат.",
        )

    # Провайдера нет (или счёт не ушёл) — старый путь с переводом на карту.
    # Тексты и кнопка берутся из чата: клиент увидит ровно то же сообщение,
    # что получил бы, оформив заказ перепиской.
    from handlers.orders import payment_text  # noqa: PLC0415
    from keyboards.orders import payment_kb  # noqa: PLC0415

    await bot.send_message(
        client_id, payment_text(order), reply_markup=payment_kb(order_id),
        parse_mode="HTML",
    )
    return ok(
        order_id=order_id,
        mode="card",
        message=f"Заказ №{order_id} оформлен — реквизиты для оплаты в чате.",
    )


async def my_orders(request: web.Request) -> web.Response:
    """Свои заказы: статус, сумма, состав, номер накладной."""
    orders = await queries.get_client_orders(_client_id(request), limit=20)
    result = []
    for order in orders:
        full = await queries.get_order_full(order["id"])
        result.append({
            "id": order["id"],
            "status": order["status"],
            "status_text": format.ORDER_STATUS_RU.get(order["status"], order["status"]),
            "total": order["total"],
            "total_text": format.money(order["total"]),
            "created_at": order["created_at"],
            "ttn": order["ttn"],
            "items": [
                {
                    "title": item["title_snapshot"],
                    "label": _label(item),
                    "qty": item["qty"],
                    "sum_text": format.money(item["price_snapshot"] * item["qty"]),
                }
                for item in (full or {}).get("items", [])
            ],
        })
    return ok(orders=result)


def setup_routes(app: web.Application) -> None:
    """Роуты магазина. Все под /api/shop/ — их охраняет web/auth.py."""
    app.router.add_get("/api/shop/meta", meta)
    app.router.add_get("/api/shop/catalog", catalog)
    app.router.add_get(r"/api/shop/products/{id:\d+}", product_card)
    app.router.add_get("/api/shop/cart", cart_view)
    app.router.add_post("/api/shop/cart/add", cart_add)
    app.router.add_post("/api/shop/cart/qty", cart_set_qty)
    app.router.add_post("/api/shop/cart/clear", cart_clear)
    app.router.add_post("/api/shop/promo", promo_apply)
    app.router.add_post("/api/shop/checkout", checkout)
    app.router.add_get("/api/shop/orders", my_orders)
