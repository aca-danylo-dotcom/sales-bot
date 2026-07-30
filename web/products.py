"""Раздел «Товары» веб-CRM: список, карточка, склад, фото.

Зачем он рядом с админкой в Telegram, а не вместо неё: телефон удобен, чтобы
сфотографировать товар и завести его на месте, браузер — чтобы поправить три
десятка цен и остатков за один заход. База у них одна, поэтому товар, созданный
в боте, здесь правится, а изменённый здесь остаток бот отдаёт клиенту сразу же:
кеша каталога нет, каждый запрос идёт в SQLite.

Страницы работают без JavaScript: обычные формы и схема «POST → редирект → GET»
(редирект нужен, чтобы обновление страницы не повторяло сохранение). Результат
действия переезжает в адрес кодом (`?ok=saved`), а не текстом — так в адресную
строку нельзя подсунуть произвольное сообщение якобы от панели.
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urlencode
from uuid import uuid4

import aiohttp_jinja2
from aiohttp import web

import config
from db import queries
from services import media
from web import forms

logger = logging.getLogger(__name__)

# Категории-подсказки те же, что кнопками в боте: список не жёсткий, продавец
# может вписать свою, но расходиться эти два места не должны.
from keyboards.admin import CATEGORIES  # noqa: E402  (после логгера — как и остальные импорты проекта)

PAGE_SIZE = 20

# Что показать после действия. Текст держим здесь, в адресе — только ключ.
MESSAGES = {
    "saved": "Изменения сохранены.",
    "created": "Товар создан. Добавьте варианты и фото — без вариантов его нельзя купить.",
    "created_ready": "Товар создан со всем, что вы заполнили. Проверьте карточку "
                     "и верните его в продажу — пока он скрыт.",
    "deleted": "Товар удалён.",
    "shown": "Товар вернулся в продажу.",
    "hidden": "Товар скрыт — клиентам он больше не показывается.",
    "stock": "Остатки сохранены.",
    "stock_none": "Менять было нечего — остатки и так такие.",
    "variant_added": "Вариант добавлен.",
    "variant_deleted": "Вариант удалён.",
    "photo_added": "Фото загружено.",
    "photo_main": "Главное фото изменено.",
    "photo_deleted": "Фото удалено.",
}

ERRORS = {
    "photo_type": "Нужен файл-картинка: JPG, PNG или WebP.",
    "photo_empty": "Файл не выбран.",
    "variant_exists": "Такой вариант уже есть — измените остаток в таблице.",
    "stock_bad": "Остаток — целое число от 0. Ничего не сохранил.",
    "not_found": "Товар не найден — возможно, его уже удалили.",
    # Карточка сохраняется целиком, поэтому осечка в одном блоке не отменяет
    # остальное — об этом честно говорим, чтобы продавец не сохранял второй раз.
    "part_variant": "Сохранил, кроме нового варианта: такой уже есть — измените остаток в таблице.",
    "part_photo": "Сохранил, кроме фото: нужен файл-картинка JPG, PNG или WebP.",
    "part_photo_new": "Товар создал, а фото не принял: нужен файл-картинка JPG, PNG или WebP. "
                      "Выберите файлы здесь, в карточке.",
}

# Сколько пустых строк «размер / цвет / остаток» показывать на странице
# создания. Три — обычная вилка обуви или перчаток на первый заход; не хватило —
# остальные добавляются в карточке по одной.
NEW_VARIANT_ROWS = 3

# Что переносим со страницы списка обратно в неё же после сохранения: только
# известные фильтры, чтобы через скрытое поле формы нельзя было подставить в
# адрес что угодно.
BACK_KEYS = ("q", "category", "status", "stock", "page")

# Картинки принимаем по содержимому, а не по имени файла: расширение легко
# переименовать, а первые байты подделать сложнее. Заодно так узнаём, с каким
# расширением класть файл на диск, чтобы веб отдавал его с верным типом.
_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
]
MAX_PHOTO_BYTES = 12 * 1024 * 1024


def _image_suffix(data: bytes) -> str | None:
    """Расширение по сигнатуре файла. None — это не картинка."""
    for signature, suffix in _SIGNATURES:
        if data.startswith(signature):
            return suffix
    # WebP: 'RIFF' + 4 байта длины + 'WEBP'
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _redirect(location: str, **params: str) -> web.HTTPFound:
    """Редирект после действия, с кодом результата в адресе."""
    query = urlencode({k: v for k, v in params.items() if v})
    raise web.HTTPFound(f"{location}?{query}" if query else location)


def _filters(request: web.Request) -> dict:
    """Поисковый запрос и фильтры списка — в том виде, в каком их ждёт БД."""
    query = request.query
    return {
        "query": forms.text(query, "q", max_len=100) or None,
        "category": forms.text(query, "category", max_len=50) or None,
        "status": forms.status_filter(query.get("status")),
        "in_stock": forms.stock_filter(query.get("stock")),
    }


def _filters_qs(request: web.Request) -> str:
    """Те же фильтры строкой для ссылок — чтобы пагинация их не теряла."""
    keep = {k: v for k, v in request.query.items()
            if k in ("q", "category", "status", "stock") and v}
    return urlencode(keep)


def _back_params(value: object) -> dict[str, str]:
    """Фильтры списка, приехавшие скрытым полем формы, — только известные ключи."""
    if not isinstance(value, str) or not value:
        return {}
    return {k: v for k, v in parse_qsl(value) if k in BACK_KEYS and v}


def _page_context(request: web.Request) -> dict:
    """Общее для всех страниц раздела: сообщение о результате и активный пункт меню."""
    return {
        "shop_name": config.SHOP_NAME,
        "currency": config.SHOP_CURRENCY,
        "message": MESSAGES.get(request.query.get("ok", "")),
        "error": ERRORS.get(request.query.get("err", "")),
    }


# ─────────────────────────── Список ───────────────────────────


@aiohttp_jinja2.template("products.html")
async def products_list(request: web.Request) -> dict:
    filters = _filters(request)
    page = forms.page(request.query.get("page"))

    total = await queries.count_products(**filters)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages - 1)
    products = await queries.list_products(
        **filters, limit=PAGE_SIZE, offset=page * PAGE_SIZE
    )

    return {
        **_page_context(request),
        "section": "products",
        "products": products,
        "categories": await queries.get_all_categories(),
        "total": total,
        "page": page + 1,
        "pages": pages,
        "filters_qs": _filters_qs(request),
        "q": request.query.get("q", ""),
        "category": request.query.get("category", ""),
        "status": request.query.get("status", ""),
        "stock": request.query.get("stock", ""),
    }


# ─────────────────────────── Карточка ───────────────────────────


async def _card_context(request: web.Request, product: dict, **extra) -> dict:
    return {
        **_page_context(request),
        "section": "products",
        "product": product,
        "categories": sorted(set(CATEGORIES) | set(await queries.get_all_categories())),
        "back_qs": _filters_qs(request),
        **extra,
    }


@aiohttp_jinja2.template("product.html")
async def product_card(request: web.Request) -> dict:
    product_id = int(request.match_info["id"])
    product = await queries.get_product_full(product_id)
    if not product:
        raise web.HTTPNotFound(text="Товар не найден")
    return await _card_context(request, product)


async def product_save(request: web.Request) -> web.Response:
    """Сохраняет карточку целиком: основное, остатки, новый вариант и фото.

    Раньше блоков было три, у каждого своя кнопка, и заполнивший всё подряд
    продавец сохранял только тот блок, чью кнопку нажал, — остальное молча
    терялось. Поэтому поля собраны в одну форму: «Сохранить всё» записывает
    их разом, «Готово» делает то же и возвращает в список товаров.

    Ошибку показываем прямо в форме — редирект на этом шаге стёр бы набранное.
    """
    product_id = int(request.match_info["id"])
    product = await queries.get_product_full(product_id)
    if not product:
        raise web.HTTPNotFound(text="Товар не найден")

    data = await request.post()
    title = forms.text(data, "title", max_len=forms.MAX_TITLE)
    price = forms.price(data.get("price"))
    old_price, old_price_ok = forms.optional_price(data.get("old_price"))
    pairs, bad_stock = _stock_pairs(data, prefix="stock_")
    back = _back_params(data.get("back"))

    problems = []
    if not title:
        problems.append("Название не может быть пустым.")
    if price is None:
        problems.append("Цена — число больше нуля, например 1200 или 1200,50.")
    if not old_price_ok:
        problems.append("Старую цену либо оставьте пустой, либо введите числом.")
    if bad_stock:
        problems.append("Остаток — целое число от 0.")

    if problems:
        # Не редиректим: человек только что набрал текст, и терять его нельзя.
        # Показываем форму с тем, что он ввёл, и списком замечаний.
        product.update({
            "title": title or product["title"],
            "description": forms.text(data, "description", max_len=forms.MAX_DESCRIPTION),
            "category": forms.text(data, "category"),
            "sku": forms.text(data, "sku"),
        })
        context = await _card_context(
            request, product,
            problems=problems,
            raw_price=data.get("price", ""),
            raw_old_price=data.get("old_price", ""),
            raw_stock={k[len("stock_"):]: v for k, v in data.items()
                       if k.startswith("stock_") and isinstance(v, str)},
            raw_new={
                "size": forms.text(data, "new_size"),
                "color": forms.text(data, "new_color"),
                "stock": data.get("new_stock", "") if isinstance(data.get("new_stock"), str) else "",
            },
            back_qs=urlencode(back),
        )
        return aiohttp_jinja2.render_template("product.html", request, context)

    await queries.update_product(
        product_id,
        title=title,
        description=forms.text(data, "description", max_len=forms.MAX_DESCRIPTION),
        category=forms.text(data, "category"),
        sku=forms.text(data, "sku") or None,
        price=price,
        old_price=old_price,
        sort_order=forms.integer(data.get("sort_order"), minimum=-9999, maximum=9999) or 0,
    )
    await queries.set_variants_stock(pairs)

    # Осечка в новом варианте или в фото не отменяет уже записанное выше:
    # переспрашивать «сохранить ли остальное» после нажатой кнопки — хуже.
    err = ""
    size = forms.text(data, "new_size")
    color = forms.text(data, "new_color")
    if size or color or forms.text(data, "new_stock", max_len=10):
        existing = {(v["size"], v["color"]) for v in await queries.get_variants(product_id)}
        if (size, color) in existing:
            err = "part_variant"
        else:
            await queries.add_variant(
                product_id, size=size, color=color,
                stock=forms.integer(data.get("new_stock")) or 0,
            )

    photos = [f for f in data.getall("photo", []) if getattr(f, "filename", "")]
    if photos and not await _store_photos(product_id, photos):
        err = err or "part_photo"

    # С «Готово» уходим в список — но только если всё прошло гладко: замечание,
    # показанное на странице, которую человек уже покинул, он не прочитает.
    if err:
        _redirect(f"/products/{product_id}", err=err)
    if forms.text(data, "finish", max_len=4):
        raise web.HTTPFound(f"/products?{urlencode({**back, 'ok': 'saved'})}")
    _redirect(f"/products/{product_id}", ok="saved")


def _draft_rows(data=None) -> list[dict[str, str]]:
    """Строки «размер / цвет / остаток» страницы создания — как их набрали.

    Без data — пустой бланк для первого показа формы.
    """
    if data is None:
        return [{"size": "", "color": "", "stock": ""} for _ in range(NEW_VARIANT_ROWS)]
    return [
        {
            "size": forms.text(data, f"new_size_{i}"),
            "color": forms.text(data, f"new_color_{i}"),
            "stock": forms.text(data, f"new_stock_{i}", max_len=10),
        }
        for i in range(NEW_VARIANT_ROWS)
    ]


def _new_variants(rows: list[dict[str, str]]) -> tuple[list[dict], list[str]]:
    """Заполненные строки — в варианты для базы. Второе значение — замечания.

    Пустая строка не ошибка: продавец заводит столько размеров, сколько у него
    есть, остальные поля просто не трогает.
    """
    variants: list[dict] = []
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not (row["size"] or row["color"] or row["stock"]):
            continue
        stock = forms.integer(row["stock"]) if row["stock"] else 0
        if stock is None:
            if "Остаток — целое число от 0." not in problems:
                problems.append("Остаток — целое число от 0.")
            continue
        key = (row["size"], row["color"])
        if key in seen:
            if "Одинаковые варианты в строках — оставьте один." not in problems:
                problems.append("Одинаковые варианты в строках — оставьте один.")
            continue
        seen.add(key)
        variants.append({"size": row["size"], "color": row["color"], "stock": stock})
    return variants, problems


async def _new_context(request: web.Request, **extra) -> dict:
    return {
        **_page_context(request),
        "section": "products",
        "categories": sorted(set(CATEGORIES) | set(await queries.get_all_categories())),
        "draft": {},
        "rows": _draft_rows(),
        "problems": [],
        "had_photos": False,
        **extra,
    }


@aiohttp_jinja2.template("product_new.html")
async def product_new_form(request: web.Request) -> dict:
    return await _new_context(request)


async def product_create(request: web.Request) -> web.Response:
    """Заводит товар целиком: основное, размеры с остатками и фото за один раз.

    Раньше страница спрашивала только название с ценой, а варианты и снимки
    приходилось дозаполнять уже в карточке — то есть заведение товара всегда
    было в два захода. Поля те же, что в карточке, поэтому и обработка похожа:
    ошибку показываем прямо в форме, не редиректом, чтобы набранное не пропало.
    """
    data = await request.post()
    title = forms.text(data, "title", max_len=forms.MAX_TITLE)
    price = forms.price(data.get("price"))
    old_price, old_price_ok = forms.optional_price(data.get("old_price"))
    rows = _draft_rows(data)
    variants, problems = _new_variants(rows)

    if not title:
        problems.insert(0, "Без названия товар не создать.")
    if price is None:
        problems.insert(0, "Цена — число больше нуля, например 1200 или 1200,50.")
    if not old_price_ok:
        problems.append("Старую цену либо оставьте пустой, либо введите числом.")

    photos = [f for f in data.getall("photo", []) if getattr(f, "filename", "")]

    if problems:
        context = await _new_context(
            request,
            draft={
                "title": title,
                "price": data.get("price", ""),
                "old_price": data.get("old_price", ""),
                "category": forms.text(data, "category"),
                "sku": forms.text(data, "sku"),
                "sort_order": forms.text(data, "sort_order", max_len=10),
                "description": forms.text(data, "description", max_len=forms.MAX_DESCRIPTION),
            },
            rows=rows,
            problems=problems,
            # Выбранные файлы браузер при перерисовке не возвращает — честнее
            # сказать об этом сразу, чем оставить продавца гадать, загрузились ли.
            had_photos=bool(photos),
        )
        return aiohttp_jinja2.render_template("product_new.html", request, context)

    # Новый товар заводится скрытым — ровно как в мастере бота. Даже с фото и
    # размерами его стоит посмотреть глазами, прежде чем показывать клиентам.
    product_id = await queries.create_product(
        title,
        price,
        description=forms.text(data, "description", max_len=forms.MAX_DESCRIPTION),
        category=forms.text(data, "category"),
        old_price=old_price,
        sku=forms.text(data, "sku") or None,
        sort_order=forms.integer(data.get("sort_order"), minimum=-9999, maximum=9999) or 0,
    )
    await queries.set_product_active(product_id, False)
    for variant in variants:
        await queries.add_variant(
            product_id, size=variant["size"], color=variant["color"], stock=variant["stock"]
        )

    # Товар уже создан, поэтому непринятое фото — не повод отменять всё
    # остальное: говорим об этом в карточке, там же его и выбирают заново.
    if photos and not await _store_photos(product_id, photos):
        _redirect(f"/products/{product_id}", err="part_photo_new")
    _redirect(f"/products/{product_id}", ok="created_ready" if variants else "created")


async def product_toggle(request: web.Request) -> web.Response:
    product_id = int(request.match_info["id"])
    product = await queries.get_product(product_id)
    if not product:
        raise web.HTTPNotFound(text="Товар не найден")

    new_state = not product["is_active"]
    await queries.set_product_active(product_id, new_state)
    _redirect(f"/products/{product_id}", ok="shown" if new_state else "hidden")


async def product_delete(request: web.Request) -> web.Response:
    product_id = int(request.match_info["id"])
    # Записи о фото уходят каскадом, файлы на диске — руками.
    for photo in await queries.get_photos(product_id):
        media.remove_photo_file(photo["file_path"])
    await queries.delete_product(product_id)
    _redirect("/products", ok="deleted")


# ─────────────────────── Варианты и остатки ───────────────────────


async def variants_save(request: web.Request) -> web.Response:
    """Одна форма на всю таблицу вариантов: остатки, добавление, удаление."""
    product_id = int(request.match_info["id"])
    data = await request.post()

    # Удаление приходит кнопкой внутри той же формы. Обрабатываем первым: если
    # человек нажал «Удалить», правки в остальных строках он не подтверждал.
    delete_id = forms.integer(data.get("delete_variant"))
    if delete_id:
        await queries.delete_variant(delete_id)
        _redirect(f"/products/{product_id}", ok="variant_deleted")

    # Остатки сохраняем в любом случае, даже если нажата кнопка «Добавить»:
    # правки в таблице сделаны, и терять их из-за соседнего действия нечестно.
    pairs, bad = _stock_pairs(data, prefix="stock_")
    if bad:
        _redirect(f"/products/{product_id}", err="stock_bad")
    changed = await queries.set_variants_stock(pairs)

    size = forms.text(data, "new_size")
    color = forms.text(data, "new_color")
    if size or color or data.get("new_stock"):
        existing = {(v["size"], v["color"]) for v in await queries.get_variants(product_id)}
        if (size, color) in existing:
            _redirect(f"/products/{product_id}", err="variant_exists")
        await queries.add_variant(
            product_id, size=size, color=color, stock=forms.integer(data.get("new_stock")) or 0
        )
        _redirect(f"/products/{product_id}", ok="variant_added")

    _redirect(f"/products/{product_id}", ok="stock" if changed else "stock_none")


def _stock_pairs(data, *, prefix: str) -> tuple[list[tuple[int, int]], bool]:
    """Собирает (variant_id, stock) из полей формы. Второе значение — были ли ошибки.

    Ошибка хотя бы в одном поле отменяет всё сохранение: наполовину сохранённый
    склад хуже, чем несохранённый, — по нему продолжат торговать, не заметив.
    """
    pairs: list[tuple[int, int]] = []
    for key, value in data.items():
        if not key.startswith(prefix):
            continue
        variant_id = forms.integer(key[len(prefix):])
        stock = forms.integer(value)
        if variant_id is None or stock is None:
            return [], True
        pairs.append((variant_id, stock))
    return pairs, False


@aiohttp_jinja2.template("stock.html")
async def stock_page(request: web.Request) -> dict:
    """Все варианты подходящих товаров одной таблицей — правка остатков разом.

    Живёт внутри раздела «Товары» второй вкладкой: это те же товары, только
    вид другой — не карточки, а остатки по размерам.
    """
    filters = _filters(request)
    rows = await queries.list_stock_rows(**filters)
    return {
        **_page_context(request),
        "section": "products",
        "rows": rows,
        "categories": await queries.get_all_categories(),
        "filters_qs": _filters_qs(request),
        "q": request.query.get("q", ""),
        "category": request.query.get("category", ""),
        "status": request.query.get("status", ""),
        "stock": request.query.get("stock", ""),
    }


async def stock_save(request: web.Request) -> web.Response:
    data = await request.post()
    pairs, bad = _stock_pairs(data, prefix="stock_")
    qs = _filters_qs(request)
    tail = f"&{qs}" if qs else ""
    if bad:
        raise web.HTTPFound(f"/products/stock?err=stock_bad{tail}")
    changed = await queries.set_variants_stock(pairs)
    raise web.HTTPFound(f"/products/stock?ok={'stock' if changed else 'stock_none'}{tail}")


async def stock_moved(request: web.Request) -> web.Response:
    """Старый адрес /stock: раздел переехал в «Товары», а закладки остались."""
    tail = f"?{request.query_string}" if request.query_string else ""
    raise web.HTTPFound("/products/stock" + tail)


# ─────────────────────────── Фото ───────────────────────────


async def _store_photos(product_id: int, fields) -> int:
    """Кладёт выбранные файлы на диск и в базу. Возвращает, сколько приняли."""
    saved = 0
    for field in fields:
        content = field.file.read(MAX_PHOTO_BYTES + 1)
        suffix = _image_suffix(content)
        if not suffix or len(content) > MAX_PHOTO_BYTES:
            continue

        file_name = f"{product_id}_{uuid4().hex[:8]}{suffix}"
        try:
            (media.media_dir() / file_name).write_bytes(content)
        except OSError:
            logger.exception("Не удалось сохранить фото товара %s", product_id)
            continue

        # tg_file_id нет: файл в Telegram не загружался. Бот отправит его с диска
        # и запомнит выданный file_id при первом показе (см. services/media.py).
        existing = await queries.get_photos(product_id)
        await queries.add_photo(
            product_id, file_path=file_name, is_main=not existing, sort_order=len(existing)
        )
        saved += 1
    return saved


async def photo_upload(request: web.Request) -> web.Response:
    """Загрузка фото отдельным запросом — карточка шлёт их вместе с остальным."""
    product_id = int(request.match_info["id"])
    if not await queries.get_product(product_id):
        raise web.HTTPNotFound(text="Товар не найден")

    data = await request.post()
    fields = [f for f in data.getall("photo", []) if getattr(f, "filename", "")]
    if not fields:
        _redirect(f"/products/{product_id}", err="photo_empty")

    saved = await _store_photos(product_id, fields)
    _redirect(f"/products/{product_id}", **({"ok": "photo_added"} if saved
                                            else {"err": "photo_type"}))


async def photo_main(request: web.Request) -> web.Response:
    photo_id = int(request.match_info["id"])
    product_id = await queries.set_photo_main(photo_id)
    if not product_id:
        raise web.HTTPNotFound(text="Фото не найдено")
    _redirect(f"/products/{product_id}", ok="photo_main")


async def photo_delete(request: web.Request) -> web.Response:
    photo_id = int(request.match_info["id"])
    photo = await queries.delete_photo(photo_id)
    if not photo:
        raise web.HTTPNotFound(text="Фото не найдено")

    media.remove_photo_file(photo["file_path"])
    # Удалили главное — главным становится первое из оставшихся, иначе товар
    # окажется без картинки в карточке у клиента.
    remaining = await queries.get_photos(photo["product_id"])
    if photo["is_main"] and remaining:
        await queries.set_photo_main(remaining[0]["id"])
    _redirect(f"/products/{photo['product_id']}", ok="photo_deleted")


async def photo_file(request: web.Request) -> web.FileResponse:
    """Отдаёт файл фотографии по /media/<photo_id>.

    Прямая ссылка нужна не только панели: по этому же адресу картинку заберёт
    Instagram Direct, которому telegram file_id ни о чём не говорит.
    """
    photo_id = int(request.match_info["id"])
    photo = await queries.get_photo(photo_id)
    if not photo or not photo["file_path"]:
        raise web.HTTPNotFound(text="Файл не найден")

    # В базе лежит только имя файла. Разделители пути означали бы, что запись
    # подменили, — наружу из папки медиа не выходим.
    name = photo["file_path"]
    if "/" in name or "\\" in name or name.startswith("."):
        raise web.HTTPNotFound(text="Файл не найден")

    path = media.photo_path(name)
    if not path.is_file():
        raise web.HTTPNotFound(text="Файл не найден")
    # Имя файла случайное и меняется при перезагрузке фото, поэтому кешировать
    # надолго безопасно: другая картинка приедет по другому адресу.
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/products", products_list)
    app.router.add_get("/products/new", product_new_form)
    app.router.add_post("/products/new", product_create)
    # Остатки — вторая вкладка «Товаров». Стоит выше маршрутов с {id:\d+},
    # так что с номерами товаров не спорит.
    app.router.add_get("/products/stock", stock_page)
    app.router.add_post("/products/stock", stock_save)
    app.router.add_get(r"/products/{id:\d+}", product_card)
    app.router.add_post(r"/products/{id:\d+}", product_save)
    app.router.add_post(r"/products/{id:\d+}/variants", variants_save)
    app.router.add_post(r"/products/{id:\d+}/toggle", product_toggle)
    app.router.add_post(r"/products/{id:\d+}/delete", product_delete)
    app.router.add_post(r"/products/{id:\d+}/photos", photo_upload)
    app.router.add_post(r"/photos/{id:\d+}/main", photo_main)
    app.router.add_post(r"/photos/{id:\d+}/delete", photo_delete)
    app.router.add_get("/stock", stock_moved)
    app.router.add_get(r"/media/{id:\d+}", photo_file)
