"""Веб-CRM: приложение aiohttp, вход и каркас страниц.

Поднимается в том же процессе, что и бот (см. bot.py): одна база, один диск с
фотографиями, один планировщик — разносить это по двум процессам значило бы
делить между ними SQLite, а он такого не любит.

Разделы «Товары» и «Заказы» приезжают следующими фазами; здесь — вход, выход,
защита всех страниц и главная-заглушка, на которую после логина попадает
менеджер.
"""
from __future__ import annotations

import logging
from pathlib import Path

import aiohttp_jinja2
import jinja2
from aiohttp import web

import config
from db import queries
from web import security

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Страницы, открытые без входа. Всё остальное middleware закрывает по умолчанию:
# белый список безопаснее чёрного — забыть добавить страницу сюда значит попросить
# лишний раз залогиниться, а забыть в чёрном списке — выставить раздел наружу.
PUBLIC_PATHS = frozenset({"/login", "/health"})


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Пускает дальше только с валидной cookie; остальных — на страницу входа."""
    request["user"] = None

    if request.path in PUBLIC_PATHS or request.path.startswith("/static/"):
        return await handler(request)

    user_id = security.read_session(request.cookies.get(security.COOKIE_NAME))
    user = await queries.get_user(user_id) if user_id else None
    if not user or not user["is_active"]:
        # Отключённого пользователя выкидываем на том же шаге, что и чужого:
        # иначе выключение доступа сработало бы только после истечения cookie.
        response = web.HTTPFound(f"/login?next={request.path}")
        if user_id:
            response.del_cookie(security.COOKIE_NAME, path="/")
        raise response

    request["user"] = user
    return await handler(request)


def _safe_next(raw: str | None) -> str:
    """Куда вернуть после входа. Чужие адреса не принимаем.

    Без проверки ссылка вида /login?next=https://чужой-сайт превращает нашу
    страницу входа в трамплин для фишинга: человек видит знакомый домен, вводит
    пароль и уезжает на подделку.
    """
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    return raw


@aiohttp_jinja2.template("login.html")
async def login_page(request: web.Request) -> dict:
    if security.read_session(request.cookies.get(security.COOKIE_NAME)):
        raise web.HTTPFound("/")
    return {"next": _safe_next(request.query.get("next"))}


async def login_submit(request: web.Request) -> web.Response:
    form = await request.post()
    login = str(form.get("login", "")).strip()
    password = str(form.get("password", ""))
    next_url = _safe_next(str(form.get("next", "")) or None)

    user = await queries.get_user_by_login(login) if login else None
    if user and user["is_active"] and security.verify_password(password, user["password_hash"]):
        response = web.HTTPFound(next_url)
        response.set_cookie(
            security.COOKIE_NAME,
            security.make_session(user["id"]),
            max_age=config.WEB_SESSION_DAYS * 86400,
            httponly=True,   # JavaScript до cookie не дотянется
            secure=config.WEB_SECURE_COOKIE,
            samesite="Lax",  # cookie не уедет с запросом, начатым на чужом сайте
            path="/",
        )
        logger.info("Вход в CRM: %s", user["login"])
        raise response

    # Тратим ровно столько же времени, сколько на верный пароль, и не говорим,
    # что именно не подошло: по разнице ответов подбирают список логинов.
    if not user or not user["is_active"]:
        security.waste_password_time()
    logger.warning("Неудачный вход в CRM: логин %r", login[:40])
    return aiohttp_jinja2.render_template(
        "login.html", request,
        {"error": "Неверный логин или пароль", "login": login, "next": next_url},
        status=401,
    )


async def logout(request: web.Request) -> web.Response:
    response = web.HTTPFound("/login")
    response.del_cookie(security.COOKIE_NAME, path="/")
    raise response


@aiohttp_jinja2.template("index.html")
async def index(request: web.Request) -> dict:
    return {"user": request["user"], "shop_name": config.SHOP_NAME}


async def health(request: web.Request) -> web.Response:
    """Проверка живости для хостинга — без входа и без обращения к базе."""
    return web.json_response({"status": "ok"})


def create_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        # Автоэкранирование включено везде: в шаблоны попадают имена клиентов и
        # их сообщения боту, то есть текст, который писал посторонний человек.
        autoescape=True,
    )
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login_submit)
    app.router.add_post("/logout", logout)
    app.router.add_get("/health", health)
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC_DIR, name="static")
    return app


async def start_web() -> web.AppRunner | None:
    """Поднимает CRM рядом с polling'ом бота. None, если она выключена."""
    if not config.WEB_ENABLED:
        logger.info(
            "Веб-CRM выключена: не задан WEB_SECRET. Сгенерировать ключ: "
            'py -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
        return None

    runner = web.AppRunner(create_app())
    await runner.setup()
    site = web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT)
    await site.start()
    logger.info("Веб-CRM слушает http://%s:%s", config.WEB_HOST, config.WEB_PORT)
    return runner
