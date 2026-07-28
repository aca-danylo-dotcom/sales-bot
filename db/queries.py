"""Слой запросов к базе. Весь SQL живёт здесь, хендлеры его не видят.

Соглашения:
  * Возвращаем dict / list[dict], а не aiosqlite.Row — так результат легко
    передать в шаблон веб-CRM или сериализовать для инструмента ИИ.
  * Операции, где важна атомарность (создание заказа), берут BEGIN IMMEDIATE.
  * Время пишем только через config.now_str() — киевское, строкой.
"""
from __future__ import annotations

from typing import Any, Iterable

import config
from db.database import get_connection


def _rows(rows: Iterable[Any]) -> list[dict]:
    return [dict(r) for r in rows]


# ─────────────────────────── Клиенты ───────────────────────────


async def ensure_client(telegram_id: int) -> None:
    """Заводит клиента, если его ещё нет. Существующего не трогает."""
    async with get_connection() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO clients (telegram_id, created_at) VALUES (?, ?)",
            (telegram_id, config.now_str()),
        )
        await conn.commit()


async def get_client(telegram_id: int) -> dict | None:
    """Клиент по telegram_id или None."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM clients WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_client(
    telegram_id: int,
    *,
    name: str | None = None,
    phone: str | None = None,
    city: str | None = None,
    np_branch: str | None = None,
    ig_user_id: str | None = None,
) -> None:
    """Обновляет профиль клиента. None означает «не трогать это поле»."""
    fields = {
        "name": name,
        "phone": phone,
        "city": city,
        "np_branch": np_branch,
        "ig_user_id": ig_user_id,
    }
    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return

    sets = ", ".join(f"{k} = ?" for k in updates)
    async with get_connection() as conn:
        await conn.execute(
            f"UPDATE clients SET {sets} WHERE telegram_id = ?",
            (*updates.values(), telegram_id),
        )
        await conn.commit()


# ─────────────────────────── Товары ───────────────────────────


async def create_product(
    title: str,
    price: float,
    *,
    description: str = "",
    category: str = "",
    old_price: float | None = None,
    sku: str | None = None,
    sort_order: int = 0,
) -> int:
    """Создаёт товар и возвращает его id. Варианты и фото добавляются отдельно."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            """INSERT INTO products
                   (sku, title, description, category, price, old_price, sort_order, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sku, title, description, category, price, old_price, sort_order,
             config.now_str()),
        )
        await conn.commit()
        return cursor.lastrowid


async def update_product(product_id: int, **fields: Any) -> None:
    """Точечная правка товара: update_product(5, price=1200, title='...')."""
    allowed = {"sku", "title", "description", "category", "price", "old_price",
               "is_active", "sort_order"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return

    sets = ", ".join(f"{k} = ?" for k in updates)
    async with get_connection() as conn:
        await conn.execute(
            f"UPDATE products SET {sets} WHERE id = ?",
            (*updates.values(), product_id),
        )
        await conn.commit()


async def set_product_active(product_id: int, is_active: bool) -> None:
    """Скрывает или возвращает товар в продажу. Скрытый клиенту не показывается."""
    await update_product(product_id, is_active=1 if is_active else 0)


async def delete_product(product_id: int) -> None:
    """Удаляет товар вместе с вариантами и фото (ON DELETE CASCADE).

    В уже оформленных заказах позиция останется: order_items хранит снимок
    названия и цены, а variant_id обнулится (ON DELETE SET NULL).
    """
    async with get_connection() as conn:
        await conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        await conn.commit()


async def get_product(product_id: int) -> dict | None:
    async with get_connection() as conn:
        cursor = await conn.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_product_full(product_id: int) -> dict | None:
    """Товар вместе с вариантами и фото — всё, что нужно для карточки."""
    async with get_connection() as conn:
        cursor = await conn.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        product = dict(row)

        cursor = await conn.execute(
            "SELECT * FROM product_variants WHERE product_id = ? ORDER BY size, color",
            (product_id,),
        )
        product["variants"] = _rows(await cursor.fetchall())

        cursor = await conn.execute(
            "SELECT * FROM product_photos WHERE product_id = ? "
            "ORDER BY is_main DESC, sort_order, id",
            (product_id,),
        )
        product["photos"] = _rows(await cursor.fetchall())

        product["total_stock"] = sum(v["stock"] for v in product["variants"])
        return product


async def list_products(
    *,
    category: str | None = None,
    active_only: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Список товаров с суммарным остатком — для админки и веб-CRM."""
    where = ["1 = 1"]
    params: list[Any] = []
    if category:
        where.append("p.category = ?")
        params.append(category)
    if active_only:
        where.append("p.is_active = 1")

    async with get_connection() as conn:
        cursor = await conn.execute(
            f"""SELECT p.*,
                       COALESCE((SELECT SUM(stock) FROM product_variants v
                                 WHERE v.product_id = p.id), 0) AS total_stock
                FROM products p
                WHERE {' AND '.join(where)}
                ORDER BY p.sort_order, p.id DESC
                LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        )
        return _rows(await cursor.fetchall())


async def count_products(*, category: str | None = None, active_only: bool = False) -> int:
    """Сколько всего товаров подходит под фильтр — для пагинации."""
    where = ["1 = 1"]
    params: list[Any] = []
    if category:
        where.append("category = ?")
        params.append(category)
    if active_only:
        where.append("is_active = 1")

    async with get_connection() as conn:
        cursor = await conn.execute(
            f"SELECT COUNT(*) AS cnt FROM products WHERE {' AND '.join(where)}", params
        )
        return (await cursor.fetchone())["cnt"]


async def search_products(
    query: str | None = None,
    *,
    category: str | None = None,
    size: str | None = None,
    color: str | None = None,
    in_stock_only: bool = True,
    limit: int = 10,
) -> list[dict]:
    """Поиск по каталогу — этим пользуется ИИ-консультант.

    Скрытые товары не возвращаются никогда: если продавец убрал товар с витрины,
    бот не должен его предлагать. in_stock_only=True отсекает товары, у которых
    не осталось ни одного варианта в наличии (с учётом фильтров размера/цвета).
    """
    where = ["p.is_active = 1"]
    params: list[Any] = []

    # lower_uni() — своя функция вместо LIKE «как есть»: встроенный LIKE не
    # приводит регистр кириллицы, и запрос «перчатки» не нашёл бы «Перчатки».
    if query:
        where.append("(lower_uni(p.title) LIKE ? OR lower_uni(p.description) LIKE ? "
                     "OR lower_uni(p.category) LIKE ?)")
        like = f"%{query.lower()}%"
        params += [like, like, like]
    if category:
        where.append("lower_uni(p.category) LIKE ?")
        params.append(f"%{category.lower()}%")

    # Условия на варианты: применяются и к «есть ли подходящий вариант», и к
    # тому, какой остаток мы покажем. Иначе получилось бы «42-й размер есть»,
    # когда в наличии только 40-й.
    variant_conditions = ["v.product_id = p.id"]
    variant_params: list[Any] = []
    if size:
        variant_conditions.append("lower_uni(v.size) = ?")
        variant_params.append(size.lower().strip())
    if color:
        variant_conditions.append("lower_uni(v.color) LIKE ?")
        variant_params.append(f"%{color.lower().strip()}%")
    if in_stock_only:
        variant_conditions.append("v.stock > 0")

    variant_where = " AND ".join(variant_conditions)
    where.append(f"EXISTS (SELECT 1 FROM product_variants v WHERE {variant_where})")

    sql = f"""
        SELECT p.*,
               COALESCE((SELECT SUM(v.stock) FROM product_variants v
                         WHERE {variant_where}), 0) AS matched_stock
        FROM products p
        WHERE {' AND '.join(where)}
        ORDER BY p.sort_order, p.id DESC
        LIMIT ?
    """
    # Параметры идут в том же порядке, в каком плейсхолдеры встречаются в SQL:
    # сначала подзапрос в SELECT, потом WHERE и его EXISTS.
    sql_params = [*variant_params, *params, *variant_params, limit]

    async with get_connection() as conn:
        cursor = await conn.execute(sql, sql_params)
        products = _rows(await cursor.fetchall())

        for product in products:
            cursor = await conn.execute(
                "SELECT id, size, color, stock FROM product_variants "
                "WHERE product_id = ? ORDER BY size, color",
                (product["id"],),
            )
            product["variants"] = _rows(await cursor.fetchall())
        return products


async def get_categories() -> list[str]:
    """Категории, в которых есть хотя бы один видимый товар."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT DISTINCT category FROM products "
            "WHERE is_active = 1 AND category <> '' ORDER BY category"
        )
        return [row["category"] for row in await cursor.fetchall()]


# ─────────────────────── Варианты (размер × цвет) ───────────────────────


async def add_variant(
    product_id: int, size: str = "", color: str = "", stock: int = 0,
    sku: str | None = None,
) -> int:
    """Добавляет вариант. Если такой (размер, цвет) уже есть — обновляет остаток."""
    async with get_connection() as conn:
        await conn.execute(
            """INSERT INTO product_variants (product_id, size, color, stock, sku)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(product_id, size, color)
               DO UPDATE SET stock = excluded.stock""",
            (product_id, size, color, stock, sku),
        )
        await conn.commit()
        cursor = await conn.execute(
            "SELECT id FROM product_variants WHERE product_id = ? AND size = ? AND color = ?",
            (product_id, size, color),
        )
        return (await cursor.fetchone())["id"]


async def set_variant_stock(variant_id: int, stock: int) -> None:
    """Ставит остаток вручную (правка склада). Отрицательные значения не пишем."""
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE product_variants SET stock = ? WHERE id = ?",
            (max(0, stock), variant_id),
        )
        await conn.commit()


async def delete_variant(variant_id: int) -> None:
    async with get_connection() as conn:
        await conn.execute("DELETE FROM product_variants WHERE id = ?", (variant_id,))
        await conn.commit()


async def get_variant(variant_id: int) -> dict | None:
    """Вариант вместе с данными товара — то, что нужно корзине и заказу."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            """SELECT v.*, p.title, p.price, p.is_active, p.category
               FROM product_variants v
               JOIN products p ON p.id = v.product_id
               WHERE v.id = ?""",
            (variant_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_variants(product_id: int) -> list[dict]:
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM product_variants WHERE product_id = ? ORDER BY size, color",
            (product_id,),
        )
        return _rows(await cursor.fetchall())


# ─────────────────────────── Фото ───────────────────────────


async def add_photo(
    product_id: int,
    tg_file_id: str | None = None,
    file_path: str | None = None,
    *,
    is_main: bool = False,
    sort_order: int = 0,
) -> int:
    """Добавляет фото. Главное фото у товара всегда одно — прежнее снимается."""
    async with get_connection() as conn:
        if is_main:
            await conn.execute(
                "UPDATE product_photos SET is_main = 0 WHERE product_id = ?", (product_id,)
            )
        cursor = await conn.execute(
            """INSERT INTO product_photos (product_id, tg_file_id, file_path, is_main, sort_order)
               VALUES (?, ?, ?, ?, ?)""",
            (product_id, tg_file_id, file_path, 1 if is_main else 0, sort_order),
        )
        await conn.commit()
        return cursor.lastrowid


async def get_photos(product_id: int) -> list[dict]:
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM product_photos WHERE product_id = ? "
            "ORDER BY is_main DESC, sort_order, id",
            (product_id,),
        )
        return _rows(await cursor.fetchall())


async def get_photo(photo_id: int) -> dict | None:
    """Одно фото по id — по нему веб отдаёт файл на /media/<id>."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM product_photos WHERE id = ?", (photo_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def set_photo_main(photo_id: int) -> int | None:
    """Делает фото главным (прежнее главное снимается). Возвращает id товара."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT product_id FROM product_photos WHERE id = ?", (photo_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        product_id = row["product_id"]
        await conn.execute(
            "UPDATE product_photos SET is_main = 0 WHERE product_id = ?", (product_id,)
        )
        await conn.execute(
            "UPDATE product_photos SET is_main = 1 WHERE id = ?", (photo_id,)
        )
        await conn.commit()
        return product_id


async def delete_photo(photo_id: int) -> dict | None:
    """Удаляет запись о фото и возвращает её — чтобы вызывающий убрал файл с диска."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM product_photos WHERE id = ?", (photo_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        await conn.execute("DELETE FROM product_photos WHERE id = ?", (photo_id,))
        await conn.commit()
        return dict(row)


# ─────────────────────────── Корзина ───────────────────────────


async def _get_or_create_cart_id(conn, client_id: int, channel: str = "telegram") -> int:
    """Возвращает id корзины клиента, создавая её при первом обращении.

    Принимает соединение, а не открывает своё: вызывается изнутри операций,
    которым нужна одна транзакция на всё (например, создание заказа).
    """
    cursor = await conn.execute("SELECT id FROM carts WHERE client_id = ?", (client_id,))
    row = await cursor.fetchone()
    if row:
        return row["id"]

    cursor = await conn.execute(
        "INSERT INTO carts (client_id, channel, updated_at) VALUES (?, ?, ?)",
        (client_id, channel, config.now_str()),
    )
    return cursor.lastrowid


async def add_to_cart(
    client_id: int, variant_id: int, qty: int = 1, channel: str = "telegram"
) -> str:
    """Кладёт вариант в корзину.

    Возвращает 'ok' / 'not_found' (вариант удалён или товар скрыт) /
    'out_of_stock' (запрошено больше, чем есть на складе).

    Остаток здесь только проверяется, но НЕ списывается: товар считается
    занятым лишь при оформлении заказа. Иначе брошенные корзины заморозили бы
    склад. Из-за этого корзина может «протухнуть» — финальную проверку делает
    create_order.
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            """SELECT v.stock, p.is_active FROM product_variants v
               JOIN products p ON p.id = v.product_id
               WHERE v.id = ?""",
            (variant_id,),
        )
        variant = await cursor.fetchone()
        if not variant or not variant["is_active"]:
            return "not_found"

        cart_id = await _get_or_create_cart_id(conn, client_id, channel)
        cursor = await conn.execute(
            "SELECT qty FROM cart_items WHERE cart_id = ? AND variant_id = ?",
            (cart_id, variant_id),
        )
        existing = await cursor.fetchone()
        new_qty = (existing["qty"] if existing else 0) + qty
        if new_qty < 1:
            return "not_found"
        if new_qty > variant["stock"]:
            return "out_of_stock"

        await conn.execute(
            """INSERT INTO cart_items (cart_id, variant_id, qty) VALUES (?, ?, ?)
               ON CONFLICT(cart_id, variant_id) DO UPDATE SET qty = excluded.qty""",
            (cart_id, variant_id, new_qty),
        )
        await conn.execute(
            "UPDATE carts SET updated_at = ? WHERE id = ?", (config.now_str(), cart_id)
        )
        await conn.commit()
        return "ok"


async def set_cart_qty(client_id: int, variant_id: int, qty: int) -> str:
    """Ставит количество позиции явно. qty <= 0 — убрать позицию из корзины."""
    if qty <= 0:
        await remove_from_cart(client_id, variant_id)
        return "ok"

    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT stock FROM product_variants WHERE id = ?", (variant_id,)
        )
        variant = await cursor.fetchone()
        if not variant:
            return "not_found"
        if qty > variant["stock"]:
            return "out_of_stock"

        cart_id = await _get_or_create_cart_id(conn, client_id)
        await conn.execute(
            """INSERT INTO cart_items (cart_id, variant_id, qty) VALUES (?, ?, ?)
               ON CONFLICT(cart_id, variant_id) DO UPDATE SET qty = excluded.qty""",
            (cart_id, variant_id, qty),
        )
        await conn.execute(
            "UPDATE carts SET updated_at = ? WHERE id = ?", (config.now_str(), cart_id)
        )
        await conn.commit()
        return "ok"


async def get_cart(client_id: int) -> dict:
    """Корзина клиента: позиции с ценой, размером, остатком и суммой.

    Всегда возвращает словарь — у клиента без корзины он просто пустой.
    В каждой позиции есть stock: интерфейс может сразу показать, что товар
    разобрали, пока корзина лежала.
    """
    async with get_connection() as conn:
        cursor = await conn.execute("SELECT * FROM carts WHERE client_id = ?", (client_id,))
        cart = await cursor.fetchone()
        if not cart:
            return {"items": [], "total": 0.0, "count": 0}

        cursor = await conn.execute(
            """SELECT ci.variant_id, ci.qty,
                      v.size, v.color, v.stock, v.product_id,
                      p.title, p.price, p.is_active
               FROM cart_items ci
               JOIN product_variants v ON v.id = ci.variant_id
               JOIN products p ON p.id = v.product_id
               WHERE ci.cart_id = ?
               ORDER BY ci.id""",
            (cart["id"],),
        )
        items = _rows(await cursor.fetchall())
        for item in items:
            item["sum"] = round(item["price"] * item["qty"], 2)

        return {
            "items": items,
            "total": round(sum(i["sum"] for i in items), 2),
            "count": sum(i["qty"] for i in items),
            "updated_at": cart["updated_at"],
        }


async def remove_from_cart(client_id: int, variant_id: int) -> None:
    async with get_connection() as conn:
        await conn.execute(
            """DELETE FROM cart_items
               WHERE variant_id = ?
                 AND cart_id = (SELECT id FROM carts WHERE client_id = ?)""",
            (variant_id, client_id),
        )
        await conn.commit()


async def clear_cart(client_id: int) -> None:
    async with get_connection() as conn:
        await conn.execute(
            "DELETE FROM cart_items WHERE cart_id = (SELECT id FROM carts WHERE client_id = ?)",
            (client_id,),
        )
        await conn.commit()


async def get_abandoned_carts(hours: int) -> list[dict]:
    """Непустые корзины, которые не трогали дольше N часов и по которым нет
    незавершённого заказа. Нужно для одного напоминания о брошенной корзине."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            """SELECT c.client_id, c.channel, c.updated_at,
                      COUNT(ci.id) AS items
               FROM carts c
               JOIN cart_items ci ON ci.cart_id = c.id
               WHERE datetime(c.updated_at) < datetime(?, ?)
                 AND NOT EXISTS (
                     SELECT 1 FROM orders o
                     WHERE o.client_id = c.client_id
                       AND o.status NOT IN ('done', 'cancelled')
                 )
               GROUP BY c.id""",
            (config.now_str(), f"-{hours} hours"),
        )
        return _rows(await cursor.fetchall())


# ─────────────────────────── Заказы ───────────────────────────


async def create_order(
    client_id: int,
    *,
    name: str,
    phone: str,
    city: str,
    np_branch: str,
    comment: str = "",
    channel: str = "telegram",
) -> tuple[str, int | None]:
    """Создаёт заказ из корзины, списывая остатки. Возвращает (статус, id заказа).

    Статусы: 'ok' / 'empty_cart' / ('out_of_stock', None) — во втором случае
    заказ не создаётся вовсе, ничего не списано, корзина остаётся как была.

    Списание идёт под BEGIN IMMEDIATE: блокировка на запись берётся сразу, и
    второй клиент, оформляющий последнюю пару 42-го размера, дождётся своей
    очереди (busy_timeout) и увидит уже уменьшенный остаток. Без этого двое
    купили бы одну и ту же единицу товара.
    """
    now = config.now_str()
    async with get_connection() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                "SELECT id FROM carts WHERE client_id = ?", (client_id,)
            )
            cart = await cursor.fetchone()
            if not cart:
                await conn.rollback()
                return "empty_cart", None

            cursor = await conn.execute(
                """SELECT ci.variant_id, ci.qty, v.size, v.color, v.stock,
                          p.title, p.price, p.is_active
                   FROM cart_items ci
                   JOIN product_variants v ON v.id = ci.variant_id
                   JOIN products p ON p.id = v.product_id
                   WHERE ci.cart_id = ?
                   ORDER BY ci.id""",
                (cart["id"],),
            )
            items = _rows(await cursor.fetchall())
            if not items:
                await conn.rollback()
                return "empty_cart", None

            # Проверяем ВСЕ позиции до единой записи: заказ либо собирается
            # целиком, либо не создаётся. Частично собранный заказ пришлось бы
            # объяснять клиенту и разбирать вручную.
            for item in items:
                if not item["is_active"] or item["qty"] > item["stock"]:
                    await conn.rollback()
                    return "out_of_stock", None

            total = round(sum(i["price"] * i["qty"] for i in items), 2)
            cursor = await conn.execute(
                """INSERT INTO orders
                       (client_id, status, total, channel, name, phone, city,
                        np_branch, comment, created_at)
                   VALUES (?, 'awaiting_payment', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (client_id, total, channel, name, phone, city, np_branch, comment, now),
            )
            order_id = cursor.lastrowid

            for item in items:
                await conn.execute(
                    """INSERT INTO order_items
                           (order_id, variant_id, title_snapshot, size, color,
                            price_snapshot, qty)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (order_id, item["variant_id"], item["title"], item["size"],
                     item["color"], item["price"], item["qty"]),
                )
                await conn.execute(
                    "UPDATE product_variants SET stock = stock - ? WHERE id = ?",
                    (item["qty"], item["variant_id"]),
                )

            await conn.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart["id"],))
            await conn.commit()
            return "ok", order_id
        except BaseException:
            await conn.rollback()
            raise


async def get_order(order_id: int) -> dict | None:
    """Заказ вместе с позициями и контактами клиента — карточка для менеджера."""
    async with get_connection() as conn:
        cursor = await conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        order = dict(row)

        cursor = await conn.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)
        )
        order["items"] = _rows(await cursor.fetchall())
        for item in order["items"]:
            item["sum"] = round(item["price_snapshot"] * item["qty"], 2)
        return order


async def get_client_orders(client_id: int, limit: int = 10) -> list[dict]:
    """Заказы клиента, свежие сверху — для «Мои заказы»."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT * FROM orders WHERE client_id = ? ORDER BY id DESC LIMIT ?",
            (client_id, limit),
        )
        return _rows(await cursor.fetchall())


async def list_orders(
    *,
    status: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Список заказов для CRM: фильтр по статусу и поиск по имени/телефону/ТТН."""
    where = ["1 = 1"]
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if search:
        where.append("(lower_uni(name) LIKE ? OR phone LIKE ? OR ttn LIKE ? "
                     "OR CAST(id AS TEXT) = ?)")
        like = f"%{search.lower()}%"
        params += [like, like, like, search]

    async with get_connection() as conn:
        cursor = await conn.execute(
            f"""SELECT * FROM orders WHERE {' AND '.join(where)}
                ORDER BY id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        )
        return _rows(await cursor.fetchall())


async def set_order_status(order_id: int, status: str) -> None:
    """Меняет статус и заодно проставляет соответствующую временную метку.

    Отмену через эту функцию не делаем — для неё есть cancel_order, которая
    возвращает товар на склад.
    """
    stamp_column = {
        "paid_claimed": "paid_at",
        "confirmed": "confirmed_at",
        "shipped": "shipped_at",
    }.get(status)

    async with get_connection() as conn:
        if stamp_column:
            await conn.execute(
                f"UPDATE orders SET status = ?, {stamp_column} = ? WHERE id = ?",
                (status, config.now_str(), order_id),
            )
        else:
            await conn.execute(
                "UPDATE orders SET status = ? WHERE id = ?", (status, order_id)
            )
        await conn.commit()


async def mark_paid_claimed(order_id: int) -> bool:
    """Клиент нажал «Я оплатил». True — статус сменился, False — заказ уже не ждёт оплаты.

    Условие в самом UPDATE, а не в отдельной проверке: повторное нажатие кнопки
    (или два нажатия подряд) не должно создавать вторую заявку админу.
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            "UPDATE orders SET status = 'paid_claimed', paid_at = ? "
            "WHERE id = ? AND status = 'awaiting_payment'",
            (config.now_str(), order_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def cancel_order(order_id: int, *, note: str | None = None) -> bool:
    """Отменяет заказ и возвращает товар на склад. True — заказ действительно отменён.

    Возврат остатков и смена статуса — в одной транзакции, иначе при сбое между
    ними товар остался бы списанным навсегда. Уже отменённый заказ второй раз
    остатки не вернёт: условие status <> 'cancelled' стоит в UPDATE.
    """
    async with get_connection() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                "SELECT status FROM orders WHERE id = ?", (order_id,)
            )
            row = await cursor.fetchone()
            if not row or row["status"] == "cancelled":
                await conn.rollback()
                return False

            cursor = await conn.execute(
                "SELECT variant_id, qty FROM order_items WHERE order_id = ?", (order_id,)
            )
            for item in await cursor.fetchall():
                if item["variant_id"] is None:  # вариант удалён из каталога
                    continue
                await conn.execute(
                    "UPDATE product_variants SET stock = stock + ? WHERE id = ?",
                    (item["qty"], item["variant_id"]),
                )

            if note:
                await conn.execute(
                    "UPDATE orders SET status = 'cancelled', note = ? WHERE id = ?",
                    (note, order_id),
                )
            else:
                await conn.execute(
                    "UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,)
                )
            await conn.commit()
            return True
        except BaseException:
            await conn.rollback()
            raise


async def set_order_ttn(order_id: int, ttn: str) -> None:
    """Записывает накладную и переводит заказ в 'shipped'."""
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE orders SET ttn = ?, status = 'shipped', shipped_at = ? WHERE id = ?",
            (ttn, config.now_str(), order_id),
        )
        await conn.commit()


async def set_order_fields(order_id: int, **fields: Any) -> None:
    """Правка заказа из CRM: адрес, комментарий, ответственный, заметка."""
    allowed = {"name", "phone", "city", "np_branch", "comment", "assignee", "note", "ttn"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return

    sets = ", ".join(f"{k} = ?" for k in updates)
    async with get_connection() as conn:
        await conn.execute(
            f"UPDATE orders SET {sets} WHERE id = ?", (*updates.values(), order_id)
        )
        await conn.commit()


async def get_expired_unpaid_orders(hours: int) -> list[dict]:
    """Заказы, которые ждут оплаты дольше отведённого времени.

    Только awaiting_payment: если клиент уже нажал «Я оплатил», заказ ждёт
    человека, и автоматически отменять его нельзя — деньги могли прийти.
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            """SELECT * FROM orders
               WHERE status = 'awaiting_payment'
                 AND datetime(created_at) < datetime(?, ?)
               ORDER BY id""",
            (config.now_str(), f"-{hours} hours"),
        )
        return _rows(await cursor.fetchall())


# ─────────────────────── История диалога ───────────────────────


async def add_message(client_id: int, role: str, content: str, channel: str = "telegram") -> None:
    """Пишет реплику в историю. role: 'user' или 'assistant'."""
    async with get_connection() as conn:
        await conn.execute(
            "INSERT INTO conversations (client_id, channel, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (client_id, channel, role, content, config.now_str()),
        )
        await conn.commit()


async def get_history(client_id: int, limit: int = 20) -> list[dict]:
    """Последние N реплик в хронологическом порядке — как их ждёт модель."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT role, content, created_at FROM conversations "
            "WHERE client_id = ? ORDER BY id DESC LIMIT ?",
            (client_id, limit),
        )
        return list(reversed(_rows(await cursor.fetchall())))


async def clear_history(client_id: int) -> None:
    async with get_connection() as conn:
        await conn.execute("DELETE FROM conversations WHERE client_id = ?", (client_id,))
        await conn.commit()


async def trim_history(client_id: int, keep: int = 200) -> None:
    """Оставляет последние `keep` реплик клиента, старые удаляет.

    Без этого таблица растёт бесконечно: диалог нужен модели на несколько
    последних сообщений, а менеджеру в CRM — за последние дни.
    """
    async with get_connection() as conn:
        await conn.execute(
            """DELETE FROM conversations
               WHERE client_id = ? AND id NOT IN (
                   SELECT id FROM conversations WHERE client_id = ?
                   ORDER BY id DESC LIMIT ?
               )""",
            (client_id, client_id, keep),
        )
        await conn.commit()
