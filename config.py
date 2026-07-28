"""Чтение настроек из .env. Все секреты и меняющиеся значения — здесь.

Правило проекта: ничто из этого файла не попадает в контекст ИИ, кроме явно
помеченного блока «Витрина магазина» — его текст уходит в системный промпт.
Токены, ключи и реквизиты карты подставляет только детерминированный код.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Загружаем .env, лежащий рядом с этим файлом
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


# --- Время ---
# Магазин работает в Киеве, поэтому всё «стенное» время бота (заказы, таймауты
# неоплаты, напоминания) должно быть киевским НЕЗАВИСИМО от часового пояса ПК
# или хостинга. Все места в коде берут «сейчас»/«сегодня» только через
# now_local()/today_local(), а не через datetime.now()/date.today().
TIMEZONE_NAME: str = os.getenv("TIMEZONE", "Europe/Kiev")
TIMEZONE = ZoneInfo(TIMEZONE_NAME)


def now_local() -> datetime:
    """Текущее время в часовом поясе магазина как наивный datetime (стенное время).

    Наивный (без tzinfo) — потому что в БД время хранится строками без зоны,
    и всё сравнение идёт в одном киевском стенном времени.
    """
    return datetime.now(TIMEZONE).replace(tzinfo=None)


def today_local() -> date:
    """Сегодняшняя дата в часовом поясе магазина."""
    return now_local().date()


def now_str() -> str:
    """«Сейчас» в том виде, в каком время лежит в базе: 'YYYY-MM-DD HH:MM:SS'.

    Все временные метки пишутся ТОЛЬКО через неё. SQLite-функция datetime('now')
    отдаёт UTC, и если часть колонок заполнять ею, а часть — киевским временем,
    то таймаут оплаты и «заказы за сегодня» разъедутся на три часа.
    """
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Не задана переменная {name}. Скопируйте .env.example в .env "
            f"и заполните значения (см. README)."
        )
    return value


# --- Секреты (обязательны для запуска) ---
BOT_TOKEN: str = _require("BOT_TOKEN")
ADMIN_ID: int = int(_require("ADMIN_ID"))

# --- ИИ-консультант (провайдер kie.ai, формат OpenAI Responses API) ---
AI_API_KEY: str = os.getenv("AI_API_KEY", "")
AI_BASE_URL: str = os.getenv("AI_BASE_URL", "https://api.kie.ai/codex/v1")
AI_MODEL: str = os.getenv("AI_MODEL", "gpt-5-5")
AI_REASONING_EFFORT: str = os.getenv("AI_REASONING_EFFORT", "low")  # low/medium/high/xhigh

# Цены модели за миллион токенов — из них считается себестоимость одного ответа
# для дашборда статистики. Валюта любая, лишь бы одна во всех агентах агентства.
# Ноль (по умолчанию) — расход в статистике будет, себестоимость нулевая.
AI_PRICE_IN: float = float(os.getenv("AI_PRICE_IN", "0"))    # за 1M входных токенов
AI_PRICE_OUT: float = float(os.getenv("AI_PRICE_OUT", "0"))  # за 1M выходных токенов

# --- Отчёт в Agent Stats (дашборд агентства) ---
# Сами адрес и ключ читает services/agent_stats.py из тех же переменных окружения:
#   AGENT_STATS_URL, AGENT_STATS_KEY, AGENT_STATS_TIMEOUT.
# Если ключ не задан — отчёт выключен и бот работает как раньше.

# --- Витрина магазина (единственное, что уходит в промпт ИИ) ---
SHOP_NAME: str = os.getenv("SHOP_NAME", "Sport Store")
SHOP_CITY: str = os.getenv("SHOP_CITY", "Киев")
SHOP_CURRENCY: str = os.getenv("SHOP_CURRENCY", "грн")
# Условия доставки и оплаты — текстом, чтобы правились без изменения кода.
DELIVERY_INFO: str = os.getenv(
    "DELIVERY_INFO",
    "Новой Почтой по всей Украине, отправка на следующий рабочий день после оплаты.",
)
PAYMENT_INFO: str = os.getenv(
    "PAYMENT_INFO",
    "Предоплата на карту — реквизиты бот присылает при оформлении заказа.",
)
RETURN_INFO: str = os.getenv(
    "RETURN_INFO",
    "Обмен и возврат в течение 14 дней, если товар не был в использовании.",
)

# --- Оплата (реквизиты; в промпт ИИ НЕ передаются) ---
# Значения по умолчанию выдуманные — при реальном использовании задать в .env.
PAYMENT_CARD: str = os.getenv("PAYMENT_CARD", "5375 4141 0000 1234")
PAYMENT_CARD_HOLDER: str = os.getenv("PAYMENT_CARD_HOLDER", "IVAN PETRENKO")

# Сколько часов заказ ждёт оплаты. По истечении — отменяется, остатки возвращаются
# на склад (иначе неоплаченные заказы навсегда заморозили бы товар).
ORDER_PAYMENT_TIMEOUT_HOURS: int = int(os.getenv("ORDER_PAYMENT_TIMEOUT_HOURS", "24"))

# Через сколько часов простоя напомнить о брошенной корзине. Напоминание уходит
# РОВНО одно на корзину (см. services/jobs.py): второе — это уже спам, от
# которого клиент блокирует бота.
CART_REMINDER_HOURS: int = int(os.getenv("CART_REMINDER_HOURS", "6"))

# --- Веб-CRM ---
# Панель работает БЕЗ входа: ни логина, ни пароля. Кто открыл адрес — тот внутри.
# WEB_HOST по умолчанию слушает все интерфейсы, потому что хостингу иначе не
# достучаться; если бот крутится на своём ПК, поставьте 127.0.0.1 — тогда панель
# будет доступна только с этого компьютера, а не всей домашней сети.
# Хостинг сам сообщает порт в PORT — на нём и слушаем, иначе локальные 8080.
WEB_PORT: int = int(os.getenv("PORT") or os.getenv("WEB_PORT", "8080"))
WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")

# --- Пути ---
# По умолчанию база лежит рядом с кодом — так удобно на своём ПК.
# На хостинге так нельзя: при каждом обновлении бота папка с кодом создаётся
# заново, и база вместе с заказами клиентов пропала бы. Поэтому там задаётся
# DB_PATH — абсолютный путь на постоянный диск, например /data/shop.db.
_DB_PATH_ENV: str = os.getenv("DB_PATH", "").strip()
DB_PATH: Path = Path(_DB_PATH_ENV) if _DB_PATH_ENV else BASE_DIR / os.getenv("DB_FILE", "shop.db")
# True — база на отдельном постоянном диске (хостинг), False — рядом с кодом (свой ПК).
DB_ON_VOLUME: bool = bool(_DB_PATH_ENV)

# Фото товаров: сам файл на диске (нужен вебу и Instagram, где telegram file_id
# не работает). Кладём рядом с базой — на хостинге это тот же постоянный диск,
# иначе картинки исчезали бы при каждом деплое.
_MEDIA_DIR_ENV: str = os.getenv("MEDIA_DIR", "").strip()
MEDIA_DIR: Path = Path(_MEDIA_DIR_ENV) if _MEDIA_DIR_ENV else DB_PATH.parent / "media"

# --- Google Sheets через Apps Script Web App ---
# Бот шлёт JSON POST'ом на URL веб-приложения, скрипт перезаписывает листы.
# Google Cloud/сервис-аккаунт не нужны. Оба значения задаются в .env; если хотя бы
# одно пустое — выгрузка выключена (см. docs/GOOGLE_SHEETS.md).
SHEETS_WEBHOOK_URL: str = os.getenv("SHEETS_WEBHOOK_URL", "")
SHEETS_WEBHOOK_TOKEN: str = os.getenv("SHEETS_WEBHOOK_TOKEN", "")
