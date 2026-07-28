"""Веб-CRM: приложение aiohttp и каркас страниц.

Поднимается в том же процессе, что и бот (см. bot.py): одна база, один диск с
фотографиями, один планировщик — разносить это по двум процессам значило бы
делить между ними SQLite, а он такого не любит.

ВХОДА НЕТ СОЗНАТЕЛЬНО: ни пароля, ни сессии, ни cookie. Кто открыл адрес — тот
внутри. Значит, адрес панели и есть весь доступ: на хостинге её видит любой, кто
этот адрес узнает, вместе со всеми заказами, телефонами и перепиской клиентов.
Если панель когда-нибудь выйдет наружу, ограничивать доступ придётся снаружи —
паролем на уровне хостинга, VPN или списком IP.

Разделы «Товары» и «Заказы» приезжают следующими фазами; здесь — каркас и
главная-заглушка.
"""
from __future__ import annotations

import logging
from pathlib import Path

import aiohttp_jinja2
import jinja2
from aiohttp import web

import config

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


@aiohttp_jinja2.template("index.html")
async def index(request: web.Request) -> dict:
    return {"shop_name": config.SHOP_NAME}


async def health(request: web.Request) -> web.Response:
    """Проверка живости для хостинга — без обращения к базе."""
    return web.json_response({"status": "ok"})


def create_app() -> web.Application:
    app = web.Application()
    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        # Автоэкранирование включено везде: в шаблоны попадают имена клиентов и
        # их сообщения боту, то есть текст, который писал посторонний человек.
        autoescape=True,
    )
    app.router.add_get("/health", health)
    app.router.add_get("/", index)
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
