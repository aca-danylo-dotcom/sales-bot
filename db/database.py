"""Подключение к SQLite и инициализация схемы.

Модель простая: короткое соединение на каждую операцию через контекстный
менеджер `get_connection()`. Никакого разделяемого состояния — меньше поводов
для гонок. Там, где гонка всё же возможна (списание остатка при заказе),
используется явная транзакция `BEGIN IMMEDIATE`.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

import config

# Схема БД. IF NOT EXISTS — init_db можно звать при каждом старте безопасно.
#
# Два сквозных правила:
#  * Все временные метки — строки 'YYYY-MM-DD HH:MM:SS' в киевском времени, их
#    пишет код через config.now_str(). DEFAULT (datetime('now')) не используем
#    нигде: он отдаёт UTC, и метки разъехались бы на три часа.
#  * Наличие товара живёт только в product_variants.stock. У самого товара
#    остатка нет — «есть ли 42-й размер» это всегда вопрос про вариант.
_SCHEMA = """
-- === Каталог ===

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sku         TEXT,                           -- артикул продавца, необязателен
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    category    TEXT    NOT NULL DEFAULT '',    -- одежда / обувь / экипировка
    price       REAL    NOT NULL,
    old_price   REAL,                           -- цена до скидки, для «было/стало»
    is_active   INTEGER NOT NULL DEFAULT 1,     -- 0 — скрыт, клиенту не показывается
    sort_order  INTEGER NOT NULL DEFAULT 0,     -- ручной порядок в списках
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_products_active   ON products(is_active);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);

CREATE TABLE IF NOT EXISTS product_variants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    size        TEXT    NOT NULL DEFAULT '',    -- '' — товар без размеров
    color       TEXT    NOT NULL DEFAULT '',    -- '' — товар без выбора цвета
    stock       INTEGER NOT NULL DEFAULT 0,     -- сколько на складе прямо сейчас
    sku         TEXT,
    UNIQUE(product_id, size, color)
);

CREATE INDEX IF NOT EXISTS idx_variants_product ON product_variants(product_id);

CREATE TABLE IF NOT EXISTS product_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    tg_file_id  TEXT,                           -- мгновенная отправка в Telegram
    file_path   TEXT,                           -- файл на диске: веб-CRM и Instagram
    is_main     INTEGER NOT NULL DEFAULT 0,     -- главное фото — первое в карточке
    sort_order  INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT                           -- sha256 файла: защита от дублей
);

CREATE INDEX IF NOT EXISTS idx_photos_product ON product_photos(product_id);

-- === Клиенты ===

CREATE TABLE IF NOT EXISTS clients (
    telegram_id INTEGER PRIMARY KEY,
    ig_user_id  TEXT,                           -- id в Instagram (появится с Direct)
    name        TEXT,                           -- имя (спрашиваем при оформлении)
    phone       TEXT,                           -- телефон (кнопка «Поделиться контактом»)
    email       TEXT,                           -- почта — ТОЛЬКО по желанию клиента,
                                                -- для напоминаний; пусто у большинства
    city        TEXT,                           -- город доставки
    np_branch   TEXT,                           -- отделение Новой Почты
    outreach_at TEXT,                           -- когда последний раз писали сами
                                                -- (напоминание, промокод) — держит
                                                -- паузу между письмами
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clients_ig ON clients(ig_user_id);

-- === Корзина ===
-- Одна активная корзина на клиента: корзина не «оформляется», а очищается при
-- создании заказа. Историю покупок хранят orders/order_items.

CREATE TABLE IF NOT EXISTS carts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL UNIQUE REFERENCES clients(telegram_id) ON DELETE CASCADE,
    channel     TEXT    NOT NULL DEFAULT 'telegram',  -- откуда пришёл клиент
    updated_at  TEXT    NOT NULL,
    reminded_at TEXT                                  -- когда напомнили о брошенной
                                                      -- корзине; сбрасывается, когда
                                                      -- корзина опустела (заказ/очистка)
);

CREATE TABLE IF NOT EXISTS cart_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cart_id    INTEGER NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
    variant_id INTEGER NOT NULL REFERENCES product_variants(id) ON DELETE CASCADE,
    qty        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(cart_id, variant_id)
);

CREATE INDEX IF NOT EXISTS idx_cart_items_cart ON cart_items(cart_id);

-- === Заказы ===
-- Путь: awaiting_payment → paid_claimed («Я оплатил») → confirmed → shipped → done.
-- Из любого состояния возможен cancelled. Статус 'new' зарезервирован под заказы,
-- заведённые вручную в CRM; бот создаёт заказ сразу в awaiting_payment.
-- Остатки списываются В МОМЕНТ создания заказа и возвращаются при отмене.

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id    INTEGER NOT NULL REFERENCES clients(telegram_id) ON DELETE CASCADE,
    status       TEXT    NOT NULL DEFAULT 'awaiting_payment',
    total        REAL    NOT NULL DEFAULT 0,      -- уже СО скидкой: это сумма к оплате
    discount     REAL    NOT NULL DEFAULT 0,      -- сколько скинули по промокоду, грн
    promo_code   TEXT,                            -- какой код сработал, для разбора
    channel      TEXT    NOT NULL DEFAULT 'telegram',
    name         TEXT,                          -- получатель: копия на момент заказа,
    phone        TEXT,                          -- чтобы правка профиля не переписывала
    city         TEXT,                          -- уже отправленные заказы
    np_branch    TEXT,
    comment      TEXT,
    ttn          TEXT,                          -- номер накладной Новой Почты
    assignee     TEXT,                          -- менеджер, взявший заказ в работу
    note         TEXT,                          -- внутренняя заметка, клиент не видит
    created_at   TEXT    NOT NULL,
    paid_at      TEXT,                          -- когда клиент нажал «Я оплатил»
    confirmed_at TEXT,                          -- когда оплату подтвердили
    shipped_at   TEXT                           -- когда ввели ТТН
);

CREATE INDEX IF NOT EXISTS idx_orders_client ON orders(client_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

CREATE TABLE IF NOT EXISTS order_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id       INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    variant_id     INTEGER REFERENCES product_variants(id) ON DELETE SET NULL,
    -- Снимок на момент заказа: товар потом переименуют, подорожает или исчезнет,
    -- а в заказе должно остаться то, что человек реально покупал.
    title_snapshot TEXT    NOT NULL,
    size           TEXT    NOT NULL DEFAULT '',
    color          TEXT    NOT NULL DEFAULT '',
    price_snapshot REAL    NOT NULL,
    qty            INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);

-- === Промокоды ===
-- Код именной и одноразовый: он выписывается конкретному человеку в конкретном
-- напоминании («корзина ждёт», «давно не заходили») и работает только у него.
-- Общих кодов на всех тут нет намеренно — такой код за день расходится по
-- форумам скидок, и магазин раздаёт скидку тем, кто и так купил бы.
--
-- Жизнь кода: выписан → (клиент прислал его в чат) activated_at → (оформлен
-- заказ) used_at + order_id. Разделение на «активирован» и «использован» нужно
-- ради простого правила: скидка считается один раз, в момент создания заказа, а
-- не при каждом просмотре корзины, — иначе итог в корзине и в счёте разъезжались
-- бы, и объяснить это клиенту было бы нечем.
--
-- Размер скидки хранится в самом коде, а не берётся из настроек при оформлении:
-- владелец может поменять PROMO_PERCENT завтра, а обещание, отправленное
-- человеку сегодня, менять задним числом нельзя.

CREATE TABLE IF NOT EXISTS promo_codes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT    NOT NULL UNIQUE,
    client_id    INTEGER NOT NULL REFERENCES clients(telegram_id) ON DELETE CASCADE,
    percent      INTEGER NOT NULL,
    reason       TEXT    NOT NULL DEFAULT 'cart',  -- cart / sleeping: за что выписан
    created_at   TEXT    NOT NULL,
    expires_at   TEXT    NOT NULL,
    activated_at TEXT,                             -- клиент прислал код в чат
    used_at      TEXT,                             -- код сгорел в заказе
    order_id     INTEGER REFERENCES orders(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_promo_client ON promo_codes(client_id);

-- === История диалога ===
-- В БД, а не в памяти процесса: после рестарта бот помнит разговор, и менеджер
-- в CRM видит, о чём клиент спрашивал.

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL REFERENCES clients(telegram_id) ON DELETE CASCADE,
    channel    TEXT    NOT NULL DEFAULT 'telegram',
    role       TEXT    NOT NULL,                -- user / assistant
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_client ON conversations(client_id, id);

-- === Показанные карточки товаров ===
-- Какие карточки (фото, цена, размеры) клиент уже видел. Без этой памяти бот
-- досылал карточку на КАЖДЫЙ ответ, где назван товар, — а название он обязан
-- называть точно, иначе не уйдёт фото. Клиент писал «43», получал ту же
-- карточку по третьему разу. В БД, а не в памяти процесса: после деплоя
-- разговор продолжается, и повтор был бы ровно там, где он раздражает.

CREATE TABLE IF NOT EXISTS shown_cards (
    client_id  INTEGER NOT NULL REFERENCES clients(telegram_id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    shown_at   TEXT    NOT NULL,
    PRIMARY KEY (client_id, product_id)
);

-- === Память о клиенте ===
-- Короткие факты, которые бот записал о покупателе сам: «берёт в подарок брату»,
-- «носит 42-й», «занимается боксом». Нужны потому, что в модель уходит только
-- хвост переписки (последние реплики) — всё, что человек рассказал неделю назад,
-- для неё не существует, и разговор каждый раз начинается с нуля.
-- Заметка — это СВЕДЕНИЯ, а не указание боту: в промпт она попадает отдельным
-- блоком с оговоркой, что правила от неё не меняются.
-- Персональные данные сюда не пишутся (телефон, адрес, карта) — их отсекает
-- инструмент remember_about_client, а телефон и адрес и так есть в заказе.
-- fact_key — тот же факт без регистра и знаков: от повторной записи одного и
-- того же разными словами защищает UNIQUE, а не проверка в коде.

CREATE TABLE IF NOT EXISTS client_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL REFERENCES clients(telegram_id) ON DELETE CASCADE,
    fact       TEXT    NOT NULL,
    fact_key   TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    UNIQUE(client_id, fact_key)
);

CREATE INDEX IF NOT EXISTS idx_client_notes_client ON client_notes(client_id, id);

-- === Расход на ИИ за день ===
-- Каждое сообщение покупателя — платный запрос к модели. Счётчик нужен, чтобы
-- у одного человека был суточный потолок, а у всего бота — общий: иначе за ночь
-- можно высадить весь баланс провайдера, и утром бот замолчит для настоящих
-- клиентов. В БД, а не в памяти процесса: перезапуск бота не должен обнулять
-- лимит — иначе его обходит любой, кто дождётся деплоя.
-- День хранится строкой 'YYYY-MM-DD' по времени магазина (см. config.today_local).
-- Внешнего ключа на clients нет намеренно: счётчик пишется до ensure_client.

CREATE TABLE IF NOT EXISTS ai_usage (
    client_id INTEGER NOT NULL,
    day       TEXT    NOT NULL,
    count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (client_id, day)
);

CREATE INDEX IF NOT EXISTS idx_ai_usage_day ON ai_usage(day);

-- Таблицы пользователей здесь нет: веб-CRM работает без входа, учётные записи
-- заводить некому и незачем. Если вход когда-нибудь понадобится, таблица
-- добавляется сюда же — старые базы её подхватят при следующем запуске.
"""


# Колонки, добавленные к схеме уже после того, как бот где-то запустился.
# CREATE TABLE IF NOT EXISTS существующую таблицу молча пропускает, поэтому на
# рабочей базе новое поле появится только через ALTER TABLE. Формат записи:
# (таблица, колонка, объявление). Колонка обязана быть необязательной — данные
# в старых строках заполнить нечем.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("carts", "reminded_at", "TEXT"),
    ("product_photos", "content_hash", "TEXT"),
    ("clients", "email", "TEXT"),
    ("clients", "outreach_at", "TEXT"),
    # NOT NULL здесь поставить нельзя (см. правило выше), поэтому скидка в старых
    # заказах будет NULL — везде, где её читают, стоит COALESCE(discount, 0).
    ("orders", "discount", "REAL DEFAULT 0"),
    ("orders", "promo_code", "TEXT"),
]


# Индексы по колонкам из _ADDED_COLUMNS. В основную схему их положить нельзя:
# она выполняется раньше ALTER TABLE, и на старой базе индекс по ещё не
# добавленной колонке уронил бы запуск.
_ADDED_INDEXES: list[str] = [
    # По нему проверяется «такое фото у товара уже есть»: двойное нажатие
    # «Сохранить» и повторная отправка формы не плодят копии одной картинки.
    "CREATE INDEX IF NOT EXISTS idx_photos_hash "
    "ON product_photos(product_id, content_hash)",
    # По нему сводка «закончилось на складе» проверяет, продавался ли размер
    # раньше: без индекса каждый нулевой размер перебирал бы все позиции заказов.
    "CREATE INDEX IF NOT EXISTS idx_order_items_variant ON order_items(variant_id)",
]


async def _add_missing_columns(conn: aiosqlite.Connection) -> None:
    """Дописывает колонки из _ADDED_COLUMNS в уже существующие таблицы."""
    for table, column, declaration in _ADDED_COLUMNS:
        cursor = await conn.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in await cursor.fetchall()}
        if column not in existing:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    for statement in _ADDED_INDEXES:
        await conn.execute(statement)


async def _normalize_categories(conn: aiosqlite.Connection) -> None:
    """Приводит уже сохранённые категории к нижнему регистру.

    Новые записываются нормализованными (queries.normalize_category), но в базе
    к этому моменту лежат и «Одежда» с автозаглавной, и «одежда» из кнопки бота —
    для фильтров это две разные категории. Запрос идемпотентный: на второй раз
    он не находит ни одной строки и ничего не делает.
    """
    await conn.execute(
        "UPDATE products SET category = lower_uni(category) "
        "WHERE category IS NOT NULL AND category <> lower_uni(category)"
    )


def _lower_uni(value: str | None) -> str | None:
    """Нижний регистр средствами Python — для SQL-функции lower_uni().

    Встроенные LOWER() и LIKE в SQLite умеют менять регистр только у латиницы:
    'Перчатки' LIKE '%перчатки%' — ложь, и клиент, написавший запрос с маленькой
    буквы, ничего бы не нашёл. Поэтому регистр нормализует Python.
    """
    return value.lower() if isinstance(value, str) else value


# Размер пишут как придётся: «12 oz» и «12oz», «42» и «р.42», «размер M», а букву
# «М» набирают русской раскладкой — от латинской она отличается только кодом.
# Из-за такой мелочи бот отвечал «этого размера нет», хотя размер лежит на складе.
# Поэтому любой размер приводим к одному ключу — и в Python, и внутри SQL.
_SIZE_PREFIX_RE = re.compile(r"^(размеры?|розміри?|разм|size|рр|р)[\s.:;№-]*")
_SIZE_DROP_RE = re.compile(r"[\s.,;:_/№()-]+")
# Русские буквы, которые визуально совпадают с латинскими: «М» → «M».
_SIZE_HOMOGLYPHS = str.maketrans({
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "л": "l", "і": "i",
})
# «С» и «ХС» русскими буквами — это S и XS: после гомоглифов они стали c/xc.
_SIZE_ALIASES = {"c": "s", "xc": "xs", "xxc": "xxs"}


def size_key(value: str | None) -> str:
    """Размер в сравнимом виде: «12 oz», «12OZ» и «размер 12 oz» — один ключ."""
    if not isinstance(value, str):
        return ""
    text = _SIZE_PREFIX_RE.sub("", value.strip().lower())
    text = _SIZE_DROP_RE.sub("", text).translate(_SIZE_HOMOGLYPHS)
    return _SIZE_ALIASES.get(text, text)


@asynccontextmanager
async def get_connection() -> AsyncIterator[aiosqlite.Connection]:
    """Открывает соединение с включёнными внешними ключами и row_factory=Row."""
    conn = await aiosqlite.connect(config.DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.create_function("lower_uni", 1, _lower_uni, deterministic=True)
    await conn.create_function("size_key", 1, size_key, deterministic=True)
    await conn.execute("PRAGMA foreign_keys = ON")
    # Ждать освобождения блокировки до 5 c, а не падать с "database is locked"
    # (важно при BEGIN IMMEDIATE в создании заказа — второй писатель дожидается).
    await conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
    finally:
        await conn.close()


def _prepare_storage() -> None:
    """Готовит место под файл базы и папку фото до первого подключения.

    Два случая, оба про хостинг (на своём ПК функция только создаёт папки):
    1. База лежит на смонтированном диске (`/data/shop.db`) — каталога может не
       быть, SQLite сам его не создаёт и падает с «unable to open database file».
    2. Первый запуск на новом диске: если базы ещё нет, а в DB_SEED_GZ_B64 лежит
       старая база (gzip + base64), разворачиваем её — так переезжают уже
       существующие товары и заказы. Переменную после первого успешного запуска
       можно удалить: файл уже на диске, и повторно она не сработает.
    """
    path = Path(config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    Path(config.MEDIA_DIR).mkdir(parents=True, exist_ok=True)
    if path.exists():
        return

    seed = os.getenv("DB_SEED_GZ_B64", "").strip()
    if not seed:
        return

    try:
        data = gzip.decompress(base64.b64decode(seed, validate=True))
    except (binascii.Error, ValueError, OSError) as exc:
        # Молча стартовать с пустой базой нельзя: владелец решит, что товары и
        # заказы пропали. Лучше не подняться с внятной причиной.
        raise RuntimeError(
            "DB_SEED_GZ_B64 задана, но не читается (ожидается база SQLite, "
            f"сжатая gzip и закодированная base64): {exc}"
        ) from exc

    if not data.startswith(b"SQLite format 3\x00"):
        raise RuntimeError("DB_SEED_GZ_B64 распаковалась, но внутри не файл базы SQLite")

    path.write_bytes(data)


async def init_db() -> None:
    """Создаёт таблицы и дописывает недостающие колонки. Идемпотентна."""
    _prepare_storage()
    async with get_connection() as conn:
        # Журнал WAL: чтение перестаёт блокировать запись, и наоборот. Настройка
        # хранится в самом файле базы, поэтому достаточно включить её один раз.
        # Только для базы на диске хостинга (задан DB_PATH): рядом с кодом база
        # лежит в папке OneDrive, а тот синхронизирует файлы -wal/-shm и умеет
        # придержать их в момент записи — «database is locked» на ровном месте.
        if config.DB_ON_VOLUME:
            await conn.execute("PRAGMA journal_mode = WAL")
        await conn.executescript(_SCHEMA)
        await _add_missing_columns(conn)
        await _normalize_categories(conn)
        await conn.commit()
