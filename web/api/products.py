"""Раздел «Товары»: список, карточка, склад, фото — в виде JSON-API.

Зачем он рядом с админкой в Telegram, а не вместо неё: телефон удобен, чтобы
сфотографировать товар и завести его на месте, браузер — чтобы поправить три
десятка цен и остатков за один заход. База у них одна, поэтому товар, созданный
в боте, здесь правится, а изменённый здесь остаток бот отдаёт клиенту сразу же:
кеша каталога нет, каждый запрос идёт в SQLite.

Карточка и создание товара приходят `multipart/form-data`, а не JSON: вместе с
полями едут файлы. Имена полей остались прежние (`title`, `price`, `stock_12`,
`new_size_0`, `photo`), поэтому разбор формы, склейка вариантов и отсев дублей
фото по хешу перенесены сюда буквально — это код, отлаженный работой, и
переписывать его ради красивого JSON незачем.

Замечания по форме возвращаются списком (`problems`) с кодом 400: раньше сервер
перерисовывал страницу с тем, что человек набрал, — теперь набранное и так живёт
в браузере, а серверу остаётся сказать, что именно не так.
"""
from __future__ import annotations

import hashlib
import logging
from uuid import uuid4

from aiohttp import web

from db import queries
from services import format, media
from web import forms
from web.api.helpers import body, fail, not_found, ok

logger = logging.getLogger(__name__)

# Категории-подсказки те же, что кнопками в боте: список не жёсткий, продавец
# может вписать свою, но расходиться эти два места не должны.
from keyboards.admin import CATEGORIES  # noqa: E402  (после логгера — как и остальные импорты проекта)

PAGE_SIZE = 20

MESSAGES = {
    "saved": "Изменения сохранены.",
    # Скрытый товар после сохранения выглядит точно так же, как выставленный, —
    # поэтому прямо говорим, что осталось сделать, и называем кнопку её словами.
    "saved_hidden": "Изменения сохранены. Перепроверьте карточку и нажмите внизу "
                    "«Выставить на продажу» — пока товар скрыт, клиенты его не видят.",
    "published": "Сохранил и выставил товар на продажу — клиенты его видят.",
    "published_empty": "Товар на витрине, но купить его нельзя: нет ни одного размера. "
                       "Добавьте вариант с остатком.",
    "unpublished": "Сохранил и скрыл товар с витрины — клиентам он больше не показывается.",
    "created": "Товар создан. Добавьте варианты и фото — без вариантов его нельзя купить.",
    "created_ready": "Товар создан со всем, что вы заполнили. Перепроверьте карточку "
                     "и нажмите внизу «Выставить на продажу» — пока он скрыт.",
    "deleted": "Товар удалён.",
    "shown": "Товар вернулся в продажу.",
    "hidden": "Товар скрыт — клиентам он больше не показывается.",
    "stock": "Остатки сохранены.",
    "stock_none": "Менять было нечего — остатки и так такие.",
    "variant_added": "Вариант добавлен.",
    "variant_deleted": "Вариант удалён.",
    "photo_added": "Фото загружено.",
    "photo_dupe": "Это фото у товара уже есть — второй раз добавлять не стал.",
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
# создания при первом заходе. Строк может стать больше: кнопка «Ещё размер»
# добавляет их прямо на странице, и сервер читает столько, сколько прислали.
NEW_VARIANT_ROWS = 3

# Предел на число строк в одной отправке. Не про удобство, а про защиту: без
# него подделанная форма с полями new_size_999999 заставила бы сервер крутить
# пустой цикл. Тридцать размеров за один заход — с запасом.
MAX_VARIANT_ROWS = 30

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


def _filters(request: web.Request) -> dict:
    """Поисковый запрос и фильтры списка — в том виде, в каком их ждёт БД."""
    query = request.query
    return {
        "query": forms.text(query, "q", max_len=100) or None,
        "category": forms.text(query, "category", max_len=50) or None,
        "status": forms.status_filter(query.get("status")),
        "in_stock": forms.stock_filter(query.get("stock")),
    }


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


async def _categories() -> list[str]:
    """Подсказки для поля категории: наши постоянные плюс всё, что уже завели."""
    return sorted(set(CATEGORIES) | set(await queries.get_all_categories()))


def _files(data, key: str = "photo") -> list:
    """Выбранные файлы из формы. Пустое поле браузер тоже присылает — отсеиваем."""
    getall = getattr(data, "getall", None)
    if getall is None:  # JSON-тело: файлов там не бывает
        return []
    return [f for f in getall(key, []) if getattr(f, "filename", "")]


def _done(key: str, **extra) -> web.Response:
    return ok(message=MESSAGES[key], **extra)


# ─────────────────────────── Список ───────────────────────────


def _row(product: dict) -> dict:
    return {
        **product,
        "price_text": format.money(product["price"]),
        "old_price_text": format.money(product["old_price"]) if product["old_price"] else "",
    }


async def products_list(request: web.Request) -> web.Response:
    filters = _filters(request)
    page = forms.page(request.query.get("page"))

    total = await queries.count_products(**filters)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages - 1)
    products = await queries.list_products(
        **filters, limit=PAGE_SIZE, offset=page * PAGE_SIZE
    )

    return ok({
        "products": [_row(product) for product in products],
        # Для фильтра — только те категории, что реально есть в каталоге:
        # предлагать отбор, который заведомо ничего не найдёт, незачем.
        "categories": await queries.get_all_categories(),
        "total": total,
        "page": page + 1,
        "pages": pages,
    })


async def categories(request: web.Request) -> web.Response:  # noqa: ARG001
    """Подсказки категорий для карточки и создания товара."""
    return ok({"categories": await _categories()})


# ─────────────────────────── Карточка ───────────────────────────


async def _product_or_404(request: web.Request) -> dict:
    product = await queries.get_product_full(int(request.match_info["id"]))
    if not product:
        raise not_found(ERRORS["not_found"])
    return product


async def product_card(request: web.Request) -> web.Response:
    product = await _product_or_404(request)
    return ok({
        "product": {
            **product,
            # Цена в поле ввода должна выглядеть так, как её набирали: «1200»,
            # а не «1200.0» и не «1 200 грн» — иначе обратно приедет мусор.
            "price_input": forms.plain_number(product["price"]),
            "old_price_input": forms.plain_number(product["old_price"]),
            "price_text": format.money(product["price"]),
        },
        "categories": await _categories(),
    })


async def product_save(request: web.Request) -> web.Response:
    """Сохраняет карточку целиком: основное, остатки, новый вариант и фото.

    Раньше блоков было три, у каждого своя кнопка, и заполнивший всё подряд
    продавец сохранял только тот блок, чью кнопку нажал, — остальное молча
    терялось. Поэтому поля собраны в одну форму: сохраняются разом.
    """
    product = await _product_or_404(request)
    product_id = product["id"]

    data = await request.post()
    title = forms.text(data, "title", max_len=forms.MAX_TITLE)
    price = forms.price(data.get("price"))
    old_price, old_price_ok = forms.optional_price(data.get("old_price"))
    pairs, bad_stock = _stock_pairs(data, prefix="stock_")

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
        # Ничего не пишем: набранное осталось в браузере, а сохранять карточку
        # с непонятной ценой — хуже, чем попросить проверить поле.
        return fail("Проверьте поля карточки.", problems=problems)

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
    warning = ""
    size = forms.text(data, "new_size")
    color = forms.text(data, "new_color")
    if size or color or forms.text(data, "new_stock", max_len=10):
        existing = {(v["size"], v["color"]) for v in await queries.get_variants(product_id)}
        if (size, color) in existing:
            warning = ERRORS["part_variant"]
        else:
            await queries.add_variant(
                product_id, size=size, color=color,
                stock=forms.integer(data.get("new_stock")) or 0,
            )

    photos = _files(data)
    if photos:
        # Дубли — не ошибка: так выглядит второе нажатие «Сохранить всё» с тем
        # же выбранным фото. Ругаемся, только если не приняли вообще ничего.
        saved, dupes = await _store_photos(product_id, photos)
        if not saved and not dupes:
            warning = warning or ERRORS["part_photo"]

    # Витрина — той же кнопкой, что и сохранение: «Выставить на продажу» и
    # «Скрыть с витрины» отправляют ту же форму, поэтому поля уже записаны выше,
    # и правки не теряются.
    publish = bool(forms.text(data, "publish", max_len=4))
    hide = bool(forms.text(data, "hide", max_len=4))
    if publish and not product["is_active"]:
        await queries.set_product_active(product_id, True)
    elif hide and product["is_active"]:
        await queries.set_product_active(product_id, False)

    if publish:
        # Товар на витрине без вариантов клиент увидит, но не купит — молчать об
        # этом нельзя, иначе продавец узнаёт о промахе от покупателя.
        key = "published" if await queries.get_variants(product_id) else "published_empty"
    elif hide:
        key = "unpublished"
    else:
        key = "saved" if product["is_active"] else "saved_hidden"
    return _done(key, warning=warning)


def _draft_rows(data) -> list[dict[str, str]]:
    """Строки «размер / цвет / остаток» страницы создания — как их набрали.

    Число строк не фиксировано: кнопка «Ещё размер» дописывает их на странице,
    поэтому читаем столько, сколько пришло, а не заранее известные три. Считаем
    по номерам в именах полей (new_size_0, new_size_1, …): браузер присылает их
    подряд, но полагаться на это незачем — берём наибольший номер и ограничиваем
    его MAX_VARIANT_ROWS.
    """
    numbers = set()
    for key in data.keys():
        for prefix in ("new_size_", "new_color_", "new_stock_"):
            if key.startswith(prefix) and key[len(prefix):].isdigit():
                numbers.add(int(key[len(prefix):]))
    count = min(max(numbers) + 1, MAX_VARIANT_ROWS) if numbers else NEW_VARIANT_ROWS
    return [
        {
            "size": forms.text(data, f"new_size_{i}"),
            "color": forms.text(data, f"new_color_{i}"),
            "stock": forms.text(data, f"new_stock_{i}", max_len=10),
        }
        for i in range(count)
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


async def product_create(request: web.Request) -> web.Response:
    """Заводит товар целиком: основное, размеры с остатками и фото за один раз.

    Раньше страница спрашивала только название с ценой, а варианты и снимки
    приходилось дозаполнять уже в карточке — то есть заведение товара всегда
    было в два захода.
    """
    data = await request.post()
    title = forms.text(data, "title", max_len=forms.MAX_TITLE)
    price = forms.price(data.get("price"))
    old_price, old_price_ok = forms.optional_price(data.get("old_price"))
    variants, problems = _new_variants(_draft_rows(data))

    if not title:
        problems.insert(0, "Без названия товар не создать.")
    if price is None:
        problems.insert(0, "Цена — число больше нуля, например 1200 или 1200,50.")
    if not old_price_ok:
        problems.append("Старую цену либо оставьте пустой, либо введите числом.")

    photos = _files(data)

    if problems:
        return fail("Товар не создан.", problems=problems)

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
    warning = ""
    if photos and not any(await _store_photos(product_id, photos)):
        warning = ERRORS["part_photo_new"]
    return _done("created_ready" if variants else "created", id=product_id, warning=warning)


async def product_toggle(request: web.Request) -> web.Response:
    product_id = int(request.match_info["id"])
    product = await queries.get_product(product_id)
    if not product:
        raise not_found(ERRORS["not_found"])

    new_state = not product["is_active"]
    await queries.set_product_active(product_id, new_state)
    return _done("shown" if new_state else "hidden", is_active=new_state)


async def product_delete(request: web.Request) -> web.Response:
    product_id = int(request.match_info["id"])
    # Записи о фото уходят каскадом, файлы на диске — руками.
    for photo in await queries.get_photos(product_id):
        media.remove_photo_file(photo["file_path"])
    await queries.delete_product(product_id)
    return _done("deleted")


# ─────────────────────── Варианты и остатки ───────────────────────


async def variants_save(request: web.Request) -> web.Response:
    """Таблица вариантов одной пачкой: остатки, добавление, удаление."""
    product_id = int(request.match_info["id"])
    data = await body(request)

    # Удаление обрабатываем первым: если человек нажал «Удалить», правки в
    # остальных строках он не подтверждал.
    delete_id = forms.integer(data.get("delete_variant"))
    if delete_id:
        await queries.delete_variant(delete_id)
        return _done("variant_deleted")

    # Остатки сохраняем в любом случае, даже если нажата кнопка «Добавить»:
    # правки в таблице сделаны, и терять их из-за соседнего действия нечестно.
    pairs, bad = _stock_pairs(data, prefix="stock_")
    if bad:
        return fail(ERRORS["stock_bad"])
    changed = await queries.set_variants_stock(pairs)

    size = forms.text(data, "new_size")
    color = forms.text(data, "new_color")
    if size or color or data.get("new_stock"):
        existing = {(v["size"], v["color"]) for v in await queries.get_variants(product_id)}
        if (size, color) in existing:
            return fail(ERRORS["variant_exists"])
        await queries.add_variant(
            product_id, size=size, color=color, stock=forms.integer(data.get("new_stock")) or 0
        )
        return _done("variant_added")

    return _done("stock" if changed else "stock_none")


async def stock_page(request: web.Request) -> web.Response:
    """Все варианты подходящих товаров одной таблицей — правка остатков разом.

    Живёт внутри раздела «Товары» второй вкладкой: это те же товары, только
    вид другой — не карточки, а остатки по размерам.
    """
    rows = await queries.list_stock_rows(**_filters(request))
    return ok({
        "rows": [
            {**row, "variant": format.variant_label(row), "price_text": format.money(row["price"])}
            for row in rows
        ],
        "categories": await queries.get_all_categories(),
    })


async def stock_save(request: web.Request) -> web.Response:
    pairs, bad = _stock_pairs(await body(request), prefix="stock_")
    if bad:
        return fail(ERRORS["stock_bad"])
    changed = await queries.set_variants_stock(pairs)
    return _done("stock" if changed else "stock_none")


# ─────────────────────────── Фото ───────────────────────────


async def _store_photos(product_id: int, fields) -> tuple[int, int]:
    """Кладёт файлы на диск и в базу. Возвращает (сколько приняли, сколько дублей).

    Дубли считаются отдельно от брака: «ноль принятых» после повторной отправки
    формы — это нормально, а не «файл не того формата», и говорить про него надо
    другими словами.

    Одна и та же картинка второй раз не сохраняется. Владелец жаловался, что
    фото двоятся: браузер отправляет форму повторно от двойного нажатия
    «Сохранить», от кнопки «Назад» и при обрыве сети — а имя файла каждый раз
    новое, поэтому по имени дубль не поймать. Сверяем sha256 содержимого.
    """
    saved = 0
    dupes = 0
    seen: set[str] = set()  # дубли внутри одной отправки — файл выбран дважды
    for field in fields:
        content = field.file.read(MAX_PHOTO_BYTES + 1)
        suffix = _image_suffix(content)
        if not suffix or len(content) > MAX_PHOTO_BYTES:
            continue

        content_hash = hashlib.sha256(content).hexdigest()
        if content_hash in seen or await queries.photo_hash_exists(product_id, content_hash):
            dupes += 1
            continue
        seen.add(content_hash)

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
            product_id, file_path=file_name, is_main=not existing,
            sort_order=len(existing), content_hash=content_hash,
        )
        saved += 1
    return saved, dupes


async def photo_upload(request: web.Request) -> web.Response:
    """Загрузка фото отдельным запросом — карточка шлёт их и вместе с остальным."""
    product_id = int(request.match_info["id"])
    if not await queries.get_product(product_id):
        raise not_found(ERRORS["not_found"])

    fields = _files(await request.post())
    if not fields:
        return fail(ERRORS["photo_empty"])

    saved, dupes = await _store_photos(product_id, fields)
    if saved:
        return _done("photo_added")
    if dupes:
        return _done("photo_dupe")
    return fail(ERRORS["photo_type"])


async def photo_main(request: web.Request) -> web.Response:
    product_id = await queries.set_photo_main(int(request.match_info["id"]))
    if not product_id:
        raise not_found("Фото не найдено")
    return _done("photo_main")


async def photo_delete(request: web.Request) -> web.Response:
    photo = await queries.delete_photo(int(request.match_info["id"]))
    if not photo:
        raise not_found("Фото не найдено")

    media.remove_photo_file(photo["file_path"])
    # Удалили главное — главным становится первое из оставшихся, иначе товар
    # окажется без картинки в карточке у клиента.
    remaining = await queries.get_photos(photo["product_id"])
    if photo["is_main"] and remaining:
        await queries.set_photo_main(remaining[0]["id"])
    return _done("photo_deleted")


async def photo_file(request: web.Request) -> web.FileResponse:
    """Отдаёт файл фотографии по /media/<photo_id>.

    Адрес остался прежним и живёт вне /api: по этой же ссылке картинку заберёт
    Instagram Direct, которому telegram file_id ни о чём не говорит, — и старые
    ссылки из переписки не должны протухнуть от переезда панели на React.
    """
    photo = await queries.get_photo(int(request.match_info["id"]))
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
    app.router.add_get("/api/products", products_list)
    # Именованные адреса — выше маршрутов с {id:\d+}: они не цифровые и с
    # номерами товаров не спорят, но порядок держим явным.
    app.router.add_get("/api/products/categories", categories)
    app.router.add_get("/api/products/stock", stock_page)
    app.router.add_post("/api/products/stock", stock_save)
    app.router.add_post("/api/products/new", product_create)
    app.router.add_get(r"/api/products/{id:\d+}", product_card)
    app.router.add_post(r"/api/products/{id:\d+}", product_save)
    app.router.add_post(r"/api/products/{id:\d+}/variants", variants_save)
    app.router.add_post(r"/api/products/{id:\d+}/toggle", product_toggle)
    app.router.add_post(r"/api/products/{id:\d+}/delete", product_delete)
    app.router.add_post(r"/api/products/{id:\d+}/photos", photo_upload)
    app.router.add_post(r"/api/photos/{id:\d+}/main", photo_main)
    app.router.add_post(r"/api/photos/{id:\d+}/delete", photo_delete)
    app.router.add_get(r"/media/{id:\d+}", photo_file)
