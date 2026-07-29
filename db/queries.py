"""Слой запросов к базе. Весь SQL живёт здесь, хендлеры его не видят.

Соглашения:
  * Возвращаем dict / list[dict], а не aiosqlite.Row — так результат легко
    передать в шаблон веб-CRM или сериализовать для инструмента ИИ.
  * Операции, где важна атомарность (создание заказа), берут BEGIN IMMEDIATE.
  * Время пишем только через config.now_str() — киевское, строкой.
"""
from __future__ import annotations

import re
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


def _catalog_filters(
    *,
    query: str | None,
    category: str | None,
    status: str | None,
    in_stock: bool | None,
) -> tuple[list[str], list[Any]]:
    """Условия отбора товаров для списка в CRM. Таблица products под алиасом p.

    Поиск здесь намеренно строже клиентского (search_products): продавец знает,
    что ищет, и ждёт от «куртка зимняя» именно зимние куртки, а не всё, где
    встретилось хоть одно слово. Поэтому AND по словам и никаких ослаблений.
    """
    where = ["1 = 1"]
    params: list[Any] = []

    for word in (query or "").lower().split():
        # lower_uni() — потому что встроенный LOWER() кириллицу не трогает.
        where.append(
            "(lower_uni(p.title) LIKE ? OR lower_uni(p.description) LIKE ? "
            "OR lower_uni(p.sku) LIKE ? OR CAST(p.id AS TEXT) = ?)"
        )
        params += [f"%{word}%"] * 3 + [word]

    if category:
        where.append("p.category = ?")
        params.append(category)
    if status == "active":
        where.append("p.is_active = 1")
    elif status == "hidden":
        where.append("p.is_active = 0")

    # «Нет в наличии» — это и товар с нулями по вариантам, и товар, у которого
    # вариантов вовсе нет: купить нельзя ни тот, ни другой.
    if in_stock is True:
        where.append("EXISTS (SELECT 1 FROM product_variants v "
                     "WHERE v.product_id = p.id AND v.stock > 0)")
    elif in_stock is False:
        where.append("NOT EXISTS (SELECT 1 FROM product_variants v "
                     "WHERE v.product_id = p.id AND v.stock > 0)")

    return where, params


async def list_products(
    *,
    query: str | None = None,
    category: str | None = None,
    status: str | None = None,
    in_stock: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Список товаров с суммарным остатком и главным фото — для админки и CRM.

    status: 'active' — только в продаже, 'hidden' — только скрытые, None — все.
    in_stock: True — есть хоть один вариант в наличии, False — нет ни одного.
    """
    where, params = _catalog_filters(
        query=query, category=category, status=status, in_stock=in_stock
    )
    async with get_connection() as conn:
        cursor = await conn.execute(
            f"""SELECT p.*,
                       COALESCE((SELECT SUM(stock) FROM product_variants v
                                 WHERE v.product_id = p.id), 0) AS total_stock,
                       (SELECT COUNT(*) FROM product_variants v
                        WHERE v.product_id = p.id) AS variants_count,
                       (SELECT id FROM product_photos ph
                        WHERE ph.product_id = p.id
                        ORDER BY ph.is_main DESC, ph.sort_order, ph.id
                        LIMIT 1) AS main_photo_id
                FROM products p
                WHERE {' AND '.join(where)}
                ORDER BY p.sort_order, p.id DESC
                LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        )
        return _rows(await cursor.fetchall())


async def count_products(
    *,
    query: str | None = None,
    category: str | None = None,
    status: str | None = None,
    in_stock: bool | None = None,
) -> int:
    """Сколько всего товаров подходит под фильтр — для пагинации."""
    where, params = _catalog_filters(
        query=query, category=category, status=status, in_stock=in_stock
    )
    async with get_connection() as conn:
        cursor = await conn.execute(
            f"SELECT COUNT(*) AS cnt FROM products p WHERE {' AND '.join(where)}", params
        )
        return (await cursor.fetchone())["cnt"]


# Слова, которые клиент пишет для связки, а не для поиска. Если их не выбросить,
# запрос «что есть для бокса» ищет товар со словом «что» в названии и не находит
# ничего — а бот на пустой результат честно отвечает «такого у нас нет».
_STOP_WORDS = frozenset({
    "для", "или", "что", "это", "как", "мне", "меня", "себе", "тут", "там",
    "нужен", "нужна", "нужно", "нужны", "хочу", "куплю", "купить", "ищу",
    "есть", "цена", "цену", "стоит", "сколько", "штук", "пару", "под", "без",
    "при", "про", "все", "весь", "вся", "чем", "чём", "размер", "размера",
    "размеры", "цвет", "цвета", "модель", "товар", "подскажите", "посоветуйте",
})


def _stem(word: str) -> str:
    """Грубая основа слова: отрезаем окончание, чтобы падежи совпадали.

    Морфологии в SQLite нет, а клиент пишет «нужны перчатки» и «что к перчаткам»
    — совпасть эти формы должны с одним и тем же товаром. Режем щедро: лишний
    товар в выдаче модель отбросит сама, а вот пустая выдача превращается в
    «у нас этого нет» и теряет покупателя.
    """
    if len(word) >= 7:
        return word[:-3]
    if len(word) >= 5:
        return word[:-2]
    if len(word) >= 4:
        return word[:-1]
    return word


def _search_terms(query: str) -> list[str]:
    """Разбирает фразу клиента на основы слов, по которым имеет смысл искать."""
    terms: list[str] = []
    for raw in re.split(r"[^\w]+", query.lower(), flags=re.UNICODE):
        if not raw or raw in _STOP_WORDS:
            continue
        stem = _stem(raw)
        if len(stem) >= 3 and stem not in terms:
            terms.append(stem)
    return terms


# Одно слово запроса ищется по всему, чем товар себя описывает, включая размеры и
# цвета вариантов: «перчатки Start 12 oz чёрные» — это название, название, размер
# и цвет одной строкой, и ни по одному полю отдельно такой запрос не находится.
_TERM_CLAUSE = (
    "(lower_uni(p.title) LIKE ? OR lower_uni(p.description) LIKE ? "
    "OR lower_uni(p.category) LIKE ? OR EXISTS (SELECT 1 FROM product_variants tv "
    "WHERE tv.product_id = p.id "
    "AND (lower_uni(tv.size) LIKE ? OR lower_uni(tv.color) LIKE ?)))"
)


async def _search_attempt(
    terms: list[str],
    join: str,
    *,
    category: str | None,
    size: str | None,
    color: str | None,
    in_stock_only: bool,
    limit: int,
) -> list[dict]:
    """Один заход поиска с готовыми условиями. Пустой список — ничего не подошло."""
    where = ["p.is_active = 1"]
    params: list[Any] = []

    # lower_uni() — своя функция вместо LIKE «как есть»: встроенный LIKE не
    # приводит регистр кириллицы, и запрос «перчатки» не нашёл бы «Перчатки».
    if terms:
        where.append("(" + f" {join} ".join(_TERM_CLAUSE for _ in terms) + ")")
        for term in terms:
            params += [f"%{term}%"] * 5
    if category:
        where.append("lower_uni(p.category) LIKE ?")
        params.append(f"%{category.lower()}%")

    # Условия на варианты: применяются и к «есть ли подходящий вариант», и к
    # тому, какой остаток мы покажем. Иначе получилось бы «42-й размер есть»,
    # когда в наличии только 40-й.
    variant_conditions = ["v.product_id = p.id"]
    variant_params: list[Any] = []
    if size:
        # Пробелы убираем с обеих сторон: «12oz» и «12 oz» — один размер, а вот
        # вхождением сравнивать нельзя (42 не должен совпасть с 42.5 и 142).
        variant_conditions.append("replace(lower_uni(v.size), ' ', '') = ?")
        variant_params.append(size.lower().replace(" ", ""))
    if color:
        variant_conditions.append("lower_uni(v.color) LIKE ?")
        variant_params.append(f"%{color.lower().strip()}%")
    if in_stock_only:
        variant_conditions.append("v.stock > 0")

    variant_where = " AND ".join(variant_conditions)
    where.append(f"EXISTS (SELECT 1 FROM product_variants v WHERE {variant_where})")

    # Совпадение в названии весит больше, чем в описании: на «нужны перчатки»
    # первыми должны идти перчатки, а не бинты, у которых в описании «под
    # перчаткой». Модель читает выдачу сверху и говорит про первое.
    title_score = " + ".join(
        "(CASE WHEN lower_uni(p.title) LIKE ? THEN 1 ELSE 0 END)" for _ in terms
    ) or "0"
    score_params = [f"%{term}%" for term in terms]

    sql = f"""
        SELECT p.*,
               {title_score} AS title_hits,
               COALESCE((SELECT SUM(v.stock) FROM product_variants v
                         WHERE {variant_where}), 0) AS matched_stock
        FROM products p
        WHERE {' AND '.join(where)}
        ORDER BY title_hits DESC, p.sort_order, p.id DESC
        LIMIT ?
    """
    # Параметры идут в том же порядке, в каком плейсхолдеры встречаются в SQL:
    # сначала оба выражения в SELECT, потом WHERE и его EXISTS.
    sql_params = [*score_params, *variant_params, *params, *variant_params, limit]

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

    Ищем не фразой целиком, а по словам: клиент пишет «перчатки Start 12 oz», и
    подстроки такой длины в базе нет ни у одного товара. Сначала пробуем самое
    строгое условие, потом ослабляем — пустая выдача для продавца хуже, чем
    лишний товар рядом с нужным, потому что на пустую бот отвечает «такого нет».

    Скрытые товары не возвращаются никогда: если продавец убрал товар с витрины,
    бот не должен его предлагать. in_stock_only=True отсекает товары, у которых
    не осталось ни одного варианта в наличии (с учётом фильтров размера/цвета).
    """
    terms = _search_terms(query) if query else []

    # Ослабляем условия по одному, от точного запроса к общему.
    # Категория уходит первой: её ИИ обычно не спрашивает у клиента, а угадывает
    # («одежда»), и такой категории в каталоге может не быть вовсе — тогда
    # фильтр обнуляет выдачу, а бот отвечает «у нас этого нет».
    # Размер и цвет снимаются следом: товар вернётся со всеми вариантами, и бот
    # сможет сказать «размера L нет, есть M» вместо «такого товара у нас нет».
    # Поиск по любому из слов (OR) — последняя попытка: лучше показать соседнее,
    # чем ничего.
    relaxations = [
        (category, size, color, "AND"),
        (None, size, color, "AND"),
        (category, None, None, "AND"),
        (None, None, None, "AND"),
        (None, None, None, "OR"),
    ]

    tried: set[tuple] = set()
    for attempt_category, attempt_size, attempt_color, join in relaxations:
        if join == "OR" and len(terms) < 2:
            continue  # по одному слову OR — это тот же самый запрос
        key = (attempt_category, attempt_size, attempt_color, join)
        if key in tried:
            continue  # фильтра и так не было — повторять нечего
        tried.add(key)

        products = await _search_attempt(
            terms,
            join,
            category=attempt_category,
            size=attempt_size,
            color=attempt_color,
            in_stock_only=in_stock_only,
            limit=limit,
        )
        if products:
            return products
    return []


async def get_categories() -> list[str]:
    """Категории, в которых есть хотя бы один видимый товар."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT DISTINCT category FROM products "
            "WHERE is_active = 1 AND category <> '' ORDER BY category"
        )
        return [row["category"] for row in await cursor.fetchall()]


async def get_all_categories() -> list[str]:
    """Все категории каталога, включая те, где все товары скрыты.

    Для фильтра в CRM: продавец ищет в том числе спрятанные товары, и категория
    не должна пропадать из выпадающего списка оттого, что товар снят с витрины.
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            "SELECT DISTINCT category FROM products WHERE category <> '' ORDER BY category"
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


async def set_variants_stock(pairs: list[tuple[int, int]]) -> int:
    """Массовая правка остатков: [(variant_id, stock), ...] за одну транзакцию.

    Одной транзакцией, потому что менеджер правит склад целиком: если половина
    строк сохранится, а половина нет, он этого не заметит и будет торговать по
    несуществующим остаткам. Возвращает число реально изменённых строк — по нему
    страница говорит «сохранено N», а не «сохранено» на пустом действии.
    """
    if not pairs:
        return 0

    changed = 0
    async with get_connection() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            for variant_id, stock in pairs:
                cursor = await conn.execute(
                    "UPDATE product_variants SET stock = ? WHERE id = ? AND stock <> ?",
                    (max(0, stock), variant_id, max(0, stock)),
                )
                changed += cursor.rowcount
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
    return changed


async def list_stock_rows(
    *,
    query: str | None = None,
    category: str | None = None,
    status: str | None = None,
    in_stock: bool | None = None,
    limit: int = 200,
) -> list[dict]:
    """Варианты всех подходящих товаров одним списком — для правки склада разом.

    Лимит намеренно большой: смысл страницы в том, чтобы поправить три десятка
    остатков одной формой, а не листать их по восемь штук.
    """
    where, params = _catalog_filters(
        query=query, category=category, status=status, in_stock=in_stock
    )
    async with get_connection() as conn:
        cursor = await conn.execute(
            f"""SELECT v.id, v.size, v.color, v.stock,
                       p.id AS product_id, p.title, p.category, p.is_active, p.price
                FROM product_variants v
                JOIN products p ON p.id = v.product_id
                WHERE {' AND '.join(where)}
                ORDER BY p.sort_order, p.id DESC, v.size, v.color
                LIMIT ?""",
            (*params, limit),
        )
        return _rows(await cursor.fetchall())


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


async def set_photo_tg_file_id(photo_id: int, tg_file_id: str) -> None:
    """Запоминает telegram file_id для фото, загруженного в веб-CRM.

    Такое фото сначала лежит только на диске, и бот отправляет его файлом —
    медленно и заново при каждом показе. После первой отправки Telegram выдаёт
    file_id, и дальше карточка уходит мгновенно.
    """
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE product_photos SET tg_file_id = ? WHERE id = ?", (tg_file_id, photo_id)
        )
        await conn.commit()


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
        # Корзина опустела — счётчик напоминаний обнуляем: то, что человек
        # соберёт заново, это уже другая корзина, и о ней можно напомнить снова.
        await conn.execute(
            "UPDATE carts SET reminded_at = NULL WHERE client_id = ?", (client_id,)
        )
        await conn.commit()


async def get_abandoned_carts(hours: int) -> list[dict]:
    """Непустые корзины, которые не трогали дольше N часов и по которым нет
    незавершённого заказа. Нужно для одного напоминания о брошенной корзине.

    Корзины с проставленным reminded_at не возвращаются вовсе: напоминание по
    плану ровно одно, а корзина остаётся «брошенной» и на следующий час, и
    через сутки — без этого условия задача слала бы его снова и снова.
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            """SELECT c.client_id, c.channel, c.updated_at,
                      COUNT(ci.id) AS items
               FROM carts c
               JOIN cart_items ci ON ci.cart_id = c.id
               WHERE c.reminded_at IS NULL
                 AND datetime(c.updated_at) < datetime(?, ?)
                 AND NOT EXISTS (
                     SELECT 1 FROM orders o
                     WHERE o.client_id = c.client_id
                       AND o.status NOT IN ('done', 'cancelled')
                 )
               GROUP BY c.id""",
            (config.now_str(), f"-{hours} hours"),
        )
        return _rows(await cursor.fetchall())


async def mark_cart_reminded(client_id: int) -> None:
    """Отмечает, что о корзине уже напомнили.

    Ставится и тогда, когда сообщение не доставлено (бот заблокирован): иначе
    задача будет ломиться в тот же чат каждый час.
    """
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE carts SET reminded_at = ? WHERE client_id = ?",
            (config.now_str(), client_id),
        )
        await conn.commit()


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
            # Корзина уехала в заказ — метку напоминания снимаем, чтобы следующая
            # покупка начиналась с чистого листа (см. clear_cart).
            await conn.execute(
                "UPDATE carts SET reminded_at = NULL WHERE id = ?", (cart["id"],)
            )
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


async def get_order_full(order_id: int) -> dict | None:
    """Заказ целиком для карточки менеджера: позиции, фото, склад, клиент.

    Отличие от get_order — то, что нужно человеку перед отправкой посылки:
    к каждой позиции подтягиваются главное фото и сегодняшний остаток
    варианта (его видно, когда заказ отменяют: товар вернётся именно туда),
    а к заказу — телефон и город из профиля клиента.

    Название, размер и цена берутся ИЗ ЗАКАЗА, а не из каталога: товар мог
    подорожать или быть переименован после покупки, а в накладной должно
    остаться то, что человек заказывал.
    """
    async with get_connection() as conn:
        cursor = await conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        order = dict(row)

        cursor = await conn.execute(
            """SELECT oi.*,
                      v.product_id,
                      v.stock            AS stock_now,
                      (SELECT id FROM product_photos ph
                       WHERE ph.product_id = v.product_id
                       ORDER BY ph.is_main DESC, ph.sort_order, ph.id
                       LIMIT 1)          AS photo_id
               FROM order_items oi
               LEFT JOIN product_variants v ON v.id = oi.variant_id
               WHERE oi.order_id = ?
               ORDER BY oi.id""",
            (order_id,),
        )
        order["items"] = _rows(await cursor.fetchall())
        for item in order["items"]:
            item["sum"] = round(item["price_snapshot"] * item["qty"], 2)

        cursor = await conn.execute(
            "SELECT * FROM clients WHERE telegram_id = ?", (order["client_id"],)
        )
        client = await cursor.fetchone()
        order["client"] = dict(client) if client else None
        return order


def _order_filters(
    *,
    status: str | None,
    search: str | None,
    date_from: str | None,
    date_to: str | None,
) -> tuple[list[str], list[Any]]:
    """Условия отбора заказов. Одни и те же для списка и для счётчика страниц.

    Даты сравниваем по date(created_at): в базе лежит момент с точностью до
    секунды, и «заказы за сегодня» иначе обрывались бы на полуночи в обе стороны.
    """
    where = ["1 = 1"]
    params: list[Any] = []

    if status:
        where.append("status = ?")
        params.append(status)
    if search:
        # Менеджер ищет по тому, что у него перед глазами: номер заказа из
        # переписки, фамилия на посылке, телефон или номер накладной.
        where.append("(lower_uni(name) LIKE ? OR phone LIKE ? OR ttn LIKE ? "
                     "OR CAST(id AS TEXT) = ?)")
        like = f"%{search.lower()}%"
        params += [like, like, like, search]
    if date_from:
        where.append("date(created_at) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(created_at) <= date(?)")
        params.append(date_to)

    return where, params


async def list_orders(
    *,
    status: str | None = None,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Список заказов для CRM: статус, период, поиск по имени/телефону/ТТН/номеру."""
    where, params = _order_filters(
        status=status, search=search, date_from=date_from, date_to=date_to
    )
    async with get_connection() as conn:
        cursor = await conn.execute(
            f"""SELECT orders.*,
                       (SELECT COUNT(*) FROM order_items oi
                        WHERE oi.order_id = orders.id)             AS items_count,
                       COALESCE((SELECT SUM(qty) FROM order_items oi
                                 WHERE oi.order_id = orders.id), 0) AS units_count
                FROM orders
                WHERE {' AND '.join(where)}
                ORDER BY orders.id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        )
        return _rows(await cursor.fetchall())


async def count_orders(
    *,
    status: str | None = None,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    """Сколько заказов подходит под фильтр — для пагинации."""
    where, params = _order_filters(
        status=status, search=search, date_from=date_from, date_to=date_to
    )
    async with get_connection() as conn:
        cursor = await conn.execute(
            f"SELECT COUNT(*) AS cnt FROM orders WHERE {' AND '.join(where)}", params
        )
        return (await cursor.fetchone())["cnt"]


async def count_orders_by_status(
    *,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, int]:
    """Сколько заказов в каждом статусе — цифры на вкладках списка.

    Считаем одним запросом с теми же фильтрами, кроме самого статуса: вкладка
    должна показывать, сколько там окажется заказов при текущем поиске и периоде.
    """
    where, params = _order_filters(
        status=None, search=search, date_from=date_from, date_to=date_to
    )
    async with get_connection() as conn:
        cursor = await conn.execute(
            f"""SELECT status, COUNT(*) AS cnt FROM orders
                WHERE {' AND '.join(where)} GROUP BY status""",
            params,
        )
        return {row["status"]: row["cnt"] for row in await cursor.fetchall()}


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


async def advance_order_status(
    order_id: int, status: str, *, allowed_from: tuple[str, ...]
) -> bool:
    """Меняет статус, только если заказ сейчас в одном из ожидаемых состояний.

    True — переход состоялся, False — заказ уже не там, где думал нажимавший.
    Проверка живёт внутри UPDATE, а не отдельным SELECT: панель открыта у
    нескольких человек сразу, и между «прочитали статус» и «записали новый»
    заказ успел бы уехать. Иначе второй менеджер отправил бы клиенту второе
    «оплата получена» по уже отгруженному заказу.

    Отмена сюда не входит: она возвращает товар на склад, для неё cancel_order.
    """
    stamp_column = {
        "paid_claimed": "paid_at",
        "confirmed": "confirmed_at",
        "shipped": "shipped_at",
    }.get(status)
    placeholders = ", ".join("?" for _ in allowed_from)

    async with get_connection() as conn:
        stamp = f", {stamp_column} = ?" if stamp_column else ""
        params: list[Any] = [status]
        if stamp_column:
            params.append(config.now_str())
        params += [order_id, *allowed_from]
        cursor = await conn.execute(
            f"UPDATE orders SET status = ?{stamp} "
            f"WHERE id = ? AND status IN ({placeholders})",
            params,
        )
        await conn.commit()
        return cursor.rowcount > 0


async def take_order(order_id: int, assignee: str) -> tuple[bool, str | None]:
    """Менеджер берёт заказ в работу. Возвращает (получилось ли, кто ведёт сейчас).

    Захват идёт одним UPDATE со свободным assignee: если заказ уже ведёт
    коллега, второй менеджер получит False и его имя — вместо того чтобы
    молча перебить подпись и вдвоём звонить одному клиенту.
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            "UPDATE orders SET assignee = ? "
            "WHERE id = ? AND (assignee IS NULL OR assignee = '' OR assignee = ?)",
            (assignee, order_id, assignee),
        )
        await conn.commit()
        if cursor.rowcount > 0:
            return True, assignee

        cursor = await conn.execute(
            "SELECT assignee FROM orders WHERE id = ?", (order_id,)
        )
        row = await cursor.fetchone()
        return False, row["assignee"] if row else None


async def release_order(order_id: int) -> None:
    """Снимает ответственного — заказ снова свободен для любого менеджера."""
    async with get_connection() as conn:
        await conn.execute(
            "UPDATE orders SET assignee = NULL WHERE id = ?", (order_id,)
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


# Из этих состояний посылку осмысленно отправлять. 'shipped' в списке нарочно:
# накладную часто вводят с опечаткой и исправляют следующим действием.
SHIPPABLE_STATUSES = ("awaiting_payment", "paid_claimed", "confirmed", "shipped")


async def set_order_ttn(
    order_id: int, ttn: str, *, allowed_from: tuple[str, ...] = SHIPPABLE_STATUSES
) -> bool:
    """Записывает накладную и переводит заказ в 'shipped'. False — заказ не в том статусе.

    Отменённый заказ накладную не получит: условие статуса стоит в самом UPDATE,
    иначе отменённый и уже вернувшийся на склад товар «уехал» бы клиенту.
    """
    placeholders = ", ".join("?" for _ in allowed_from)
    async with get_connection() as conn:
        cursor = await conn.execute(
            f"UPDATE orders SET ttn = ?, status = 'shipped', shipped_at = ? "
            f"WHERE id = ? AND status IN ({placeholders})",
            (ttn, config.now_str(), order_id, *allowed_from),
        )
        await conn.commit()
        return cursor.rowcount > 0


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


# ─────────────────────────── Сводка ───────────────────────────
# Цифры для главной страницы CRM. Считаются теми же условиями, что и списки:
# нажав на цифру, менеджер должен увидеть ровно те заказы, которые в ней учтены.


async def orders_summary(
    *, date_from: str | None = None, date_to: str | None = None
) -> dict:
    """Сколько заказов за период и на какую сумму.

    Отменённые считаются отдельно и в выручку не идут: товар вернулся на склад,
    денег не было. Показывать их в сумме — значит рисовать день лучше, чем он был.
    """
    where, params = _order_filters(
        status=None, search=None, date_from=date_from, date_to=date_to
    )
    async with get_connection() as conn:
        cursor = await conn.execute(
            f"""SELECT
                    COUNT(*)                                                   AS total,
                    COALESCE(SUM(status <> 'cancelled'), 0)                    AS live,
                    COALESCE(SUM(status = 'cancelled'), 0)                     AS cancelled,
                    COALESCE(SUM(CASE WHEN status <> 'cancelled'
                                      THEN total ELSE 0 END), 0)               AS revenue,
                    COALESCE(SUM(CASE WHEN status IN ('confirmed', 'shipped', 'done')
                                      THEN total ELSE 0 END), 0)               AS paid_revenue
                FROM orders
                WHERE {' AND '.join(where)}""",
            params,
        )
        return dict(await cursor.fetchone())


async def count_stale_shipped(days: int) -> int:
    """Отправленные заказы, которые никто не закрыл дольше указанного срока.

    Обычно это значит, что посылку уже забрали, а статус остался: заказ висит в
    «отправлен» и мешает видеть настоящую работу.
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            """SELECT COUNT(*) AS cnt FROM orders
               WHERE status = 'shipped'
                 AND shipped_at IS NOT NULL
                 AND datetime(shipped_at) < datetime(?, ?)""",
            (config.now_str(), f"-{days} days"),
        )
        return (await cursor.fetchone())["cnt"]


async def zero_stock_variants(*, limit: int = 10) -> list[dict]:
    """Что закончилось на складе: варианты с нулём у товаров, которые видит клиент.

    Скрытые товары не берём — их всё равно никто не купит, и в сводке они были бы
    просто шумом.
    """
    async with get_connection() as conn:
        cursor = await conn.execute(
            """SELECT v.id, v.size, v.color, p.id AS product_id, p.title, p.category
               FROM product_variants v
               JOIN products p ON p.id = v.product_id
               WHERE v.stock <= 0 AND p.is_active = 1
               ORDER BY p.sort_order, p.id DESC, v.size, v.color
               LIMIT ?""",
            (limit,),
        )
        return _rows(await cursor.fetchall())


async def count_zero_stock() -> int:
    """Сколько всего вариантов кончилось — чтобы понять, влез ли список целиком."""
    async with get_connection() as conn:
        cursor = await conn.execute(
            """SELECT COUNT(*) AS cnt FROM product_variants v
               JOIN products p ON p.id = v.product_id
               WHERE v.stock <= 0 AND p.is_active = 1"""
        )
        return (await cursor.fetchone())["cnt"]


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
