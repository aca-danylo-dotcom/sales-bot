"""Вход в панель из браузера: спросить пароль, выдать метку, забрать обратно.

Внутри Telegram эти адреса не нужны вовсе — там человека опознаёт мессенджер.
Они существуют ради одного случая: панель открыли на компьютере.

Почему пароль один и без логина. Панель принадлежит владельцу магазина, он же
ADMIN_ID, и он один. Логин при единственном пользователе — лишнее поле в форме
и лишняя строка в настройках, а защиты не добавляет.

Задержка после неверного пароля стоит здесь же. Панель висит на публичном
адресе, и без неё пароль подбирается перебором со скоростью сети; секунда паузы
превращает миллионы попыток в сутки в несколько тысяч.
"""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web

import config
from web.api.helpers import body, fail, ok
from web.auth import SESSION_COOKIE, browser_allowed, make_session, password_matches

logger = logging.getLogger(__name__)

WRONG_PASSWORD_DELAY = 1.0


def _set_cookie(response: web.Response, value: str, max_age: int, *, secure: bool) -> None:
    """Кладёт метку входа в cookie.

    httponly — чтобы её не достал скрипт со страницы: панель метку не читает,
    её проверяет только сервер. samesite=Lax — чтобы чужая страница не могла
    выполнить действие в панели, воспользовавшись открытой сессией.

    secure ставим по адресу самого запроса: на хостинге это https и метка не
    уйдёт по открытому каналу, а на своём ПК панель работает по http, и
    secure-cookie там просто не сохранилась бы.
    """
    response.set_cookie(
        SESSION_COOKIE, value, max_age=max_age, httponly=True, samesite="Lax",
        secure=secure,
    )


async def session(request: web.Request) -> web.Response:
    """Нужен ли пароль и введён ли он уже.

    Панель спрашивает это первым делом. `required: false` означает, что пароль
    в настройках не задан и панель открыта — фронт тогда не показывает форму
    входа вовсе, чтобы не изображать защиту, которой нет.
    """
    return ok(
        required=config.crm_password_set(),
        authorized=browser_allowed(request),
    )


async def login(request: web.Request) -> web.Response:
    data = await body(request)
    if not config.crm_password_set():
        return ok(authorized=True)

    if not password_matches(str(data.get("password", ""))):
        logger.warning("Неверный пароль от панели с адреса %s", request.remote)
        await asyncio.sleep(WRONG_PASSWORD_DELAY)
        return fail("Неверный пароль.", status=401)

    response = ok(authorized=True)
    # За обратным прокси Railway до нас доходит http, а браузер говорит с ним по
    # https — поэтому смотрим ещё и на заголовок, который прокси проставляет.
    secure = (
        request.url.scheme == "https"
        or request.headers.get("X-Forwarded-Proto", "") == "https"
    )
    _set_cookie(
        response, make_session(), config.CRM_SESSION_DAYS * 24 * 60 * 60, secure=secure
    )
    return response


async def logout(request: web.Request) -> web.Response:
    """Выход: метка стирается. Нужен на чужом компьютере — там её не оставляют."""
    response = ok(authorized=False)
    response.del_cookie(SESSION_COOKIE, samesite="Lax")
    return response


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/api/session", session)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
