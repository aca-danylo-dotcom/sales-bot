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
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_photos_product ON product_photos(product_id);

-- === Клиенты ===

CREATE TABLE IF NOT EXISTS clients (
    telegram_id INTEGER PRIMARY KEY,
    ig_user_id  TEXT,                           -- id в Instagram (появится с Direct)
    name        TEXT,                           -- имя (спрашиваем при оформлении)
    phone       TEXT,                           -- телефон (кнопка «Поделиться контактом»)
    city        TEXT,                           -- город доставки
    np_branch   TEXT,                           -- отделение Новой Почты
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clients_ig ON clients(ig_user_id);

-- === Корзина ===
-- Одна активная корзина на клиента: корзина не «оформляется», а очищается при
-- создании заказа. Историю покупок хранят orders/order_items.

CREATE TABLE IF NOT EXISTS carts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL UNIQUE REFERENCES clients(telegram_id) ON DELETE CASCADE,
    channel    TEXT    NOT NULL DEFAULT 'telegram',   -- откуда пришёл клиент
    updated_at TEXT    NOT NULL
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
    total        REAL    NOT NULL DEFAULT 0,
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

-- === Пользователи веб-CRM ===
-- Таблица заводится сразу, наполняется с Фазы 10. Ролей нет: все, кто вошёл,
-- имеют одинаковые права.

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    login         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,             -- scrypt, соль внутри строки
    display_name  TEXT    NOT NULL DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);
"""


def _lower_uni(value: str | None) -> str | None:
    """Нижний регистр средствами Python — для SQL-функции lower_uni().

    Встроенные LOWER() и LIKE в SQLite умеют менять регистр только у латиницы:
    'Перчатки' LIKE '%перчатки%' — ложь, и клиент, написавший запрос с маленькой
    буквы, ничего бы не нашёл. Поэтому регистр нормализует Python.
    """
    return value.lower() if isinstance(value, str) else value


@asynccontextmanager
async def get_connection() -> AsyncIterator[aiosqlite.Connection]:
    """Открывает соединение с включёнными внешними ключами и row_factory=Row."""
    conn = await aiosqlite.connect(config.DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.create_function("lower_uni", 1, _lower_uni, deterministic=True)
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
    """Создаёт таблицы, если их ещё нет. Идемпотентна."""
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
        await conn.commit()
