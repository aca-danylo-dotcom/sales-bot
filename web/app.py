"""Веб-CRM: приложение aiohttp и каркас страниц.

Поднимается в том же процессе, что и бот (см. bot.py): одна база, один диск с
фотографиями, один планировщик — разносить это по двум процессам значило бы
делить между ними SQLite, а он такого не любит.

ВХОДА НЕТ СОЗНАТЕЛЬНО: ни пароля, ни сессии, ни cookie. Кто открыл адрес — тот
внутри. Значит, адрес панели и есть весь доступ: на хостинге её видит любой, кто
этот адрес узнает, вместе со всеми заказами, телефонами и перепиской клиентов.
Если панель когда-нибудь выйдет наружу, ограничивать доступ придётся снаружи —
паролем на уровне хостинга, VPN или списком IP.

Раздел «Заказы» приезжает следующей фазой; сейчас работают «Товары», «Склад»
и главная-заглушка.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp_jinja2
import jinja2
from aiohttp import web

import config
from services import format
from web import forms, products

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Фотографии товаров приходят прямо из формы, причём пачкой: продавец выбирает
# все снимки разом. Стандартный лимит aiohttp (1 МБ) отбрасывал бы обычное фото
# с телефона целиком, поэтому поднимаем его до размера, в который влезает
# несколько снимков; отдельный файл всё равно ограничен в web/products.py.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@aiohttp_jinja2.template("index.html")
async def index(request: web.Request) -> dict:
    return {"shop_name": config.SHOP_NAME, "section": "index"}


async def health(request: web.Request) -> web.Response:
    """Проверка живости для хостинга — без обращения к базе."""
    return web.json_response({"status": "ok"})


@web.middleware
async def same_origin_only(request: web.Request, handler):
    """Не даёт чужой странице отправить форму в нашу панель.

    Обычная защита — токен в форме, привязанный к сессии, но сессий здесь нет
    (вход не предусмотрен), привязывать токен не к чему. Остаётся то, что
    браузер проставляет сам и что подделать со страницы нельзя: заголовок
    Origin. Он должен совпадать с адресом самой панели.

    Запросы без Origin пропускаем: так приходят curl и наши же проверочные
    скрипты, а межсайтовая форма из браузера этот заголовок несёт всегда.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.headers.get("Host"):
            logger.warning("Отклонён запрос с чужого адреса: %s", origin)
            raise web.HTTPForbidden(text="Запрос пришёл с чужой страницы.")
    return await handler(request)


def create_app() -> web.Application:
    app = web.Application(
        client_max_size=MAX_UPLOAD_BYTES, middlewares=[same_origin_only]
    )
    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        # Автоэкранирование включено везде: в шаблоны попадают имена клиентов и
        # их сообщения боту, то есть текст, который писал посторонний человек.
        autoescape=True,
        # Цены и подписи вариантов форматирует тот же код, что и сообщения бота:
        # «1 200 грн» в панели и в чате должны читаться одинаково.
        filters={
            "money": format.money,
            "variant": format.variant_label,
            "plain": forms.plain_number,
        },
    )
    app.router.add_get("/health", health)
    app.router.add_get("/", index)
    products.setup_routes(app)
    app.router.add_static("/static/", STATIC_DIR, name="static")
    return app


async def start_web() -> web.AppRunner:
    """Поднимает CRM рядом с polling'ом бота."""
    runner = web.AppRunner(create_app())
    await runner.setup()
    site = web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT)
    await site.start()
    logger.info("Веб-CRM слушает http://%s:%s (без входа)", config.WEB_HOST, config.WEB_PORT)
    return runner
