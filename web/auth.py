"""Кто открыл мини-приложение: разбор и проверка initData от Telegram.

Панель в браузере входа не знает — кто узнал адрес, тот внутри (см. web/app.py).
Внутри Telegram такой вопрос не стоит: клиент сам присылает подписанные данные о
пользователе, и подпись сделана на токене нашего бота. Токен есть только у нас и
у Telegram, поэтому совпавшая подпись — доказательство, что перед нами именно
тот telegram_id, который в данных написан. Ни паролей, ни сессий, ни cookie для
этого не нужно.

Схема подписи — из документации Telegram (Mini Apps, «Validating data»):

    secret = HMAC-SHA256(key="WebAppData", msg=<токен бота>)
    hash   = HMAC-SHA256(key=secret,       msg=<пары ключ=значение через \\n>)

Пары сортируются по имени, сам `hash` из них исключается — иначе подпись
подписывала бы саму себя.

ВАЖНО про роли. Админ — это ровно один человек: config.ADMIN_ID. Проверка идёт
по id из ПОДПИСАННЫХ данных, а не по тому, что прислал фронт: подделать id в
запросе можно, подделать подпись под ним — нет.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib.parse import parse_qsl

from aiohttp import web

import config

logger = logging.getLogger(__name__)

# Заголовок, в котором фронт присылает initData. Своё имя, а не Authorization:
# это не токен доступа в привычном смысле, а слепок данных Telegram, и путать
# его с чем-то, что выдаём мы сами, не стоит.
INIT_DATA_HEADER = "X-Telegram-Init-Data"

# Сколько живёт подпись. Telegram кладёт в данные auth_date — момент открытия
# мини-аппа. Без ограничения срока однажды перехваченный initData работал бы
# вечно; сутки — запас на то, что человек открыл магазин утром, а оформил заказ
# вечером, не перезапуская приложение.
MAX_AGE_SECONDS = 24 * 60 * 60

# Ключ, под которым разобранный пользователь кладётся в запрос. Хендлеры берут
# его отсюда и в разбор initData больше не лезут.
USER_KEY = "tg_user"


def _secret_key() -> bytes:
    """Ключ подписи — производная от токена бота, а не сам токен."""
    return hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()


def parse_init_data(raw: str) -> dict[str, Any] | None:
    """Проверяет подпись и возвращает данные пользователя. None — не приняли.

    Возвращаем именно None, а не исключение: причина отказа наружу не уходит.
    Подделка подписи, просроченный auth_date и мусор в заголовке для клиента
    выглядят одинаково — «откройте приложение заново», — а подробности пишем в
    лог, где их видит только владелец.
    """
    if not raw:
        return None

    # strict_parsing: пустая или битая строка не должна молча превращаться в
    # пустой словарь, у которого «просто нет hash».
    try:
        pairs = dict(parse_qsl(raw, strict_parsing=True))
    except ValueError:
        logger.warning("initData не разбирается как строка запроса")
        return None

    received_hash = pairs.pop("hash", "")
    if not received_hash:
        logger.warning("В initData нет подписи")
        return None

    check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    expected = hmac.new(
        _secret_key(), check_string.encode(), hashlib.sha256
    ).hexdigest()
    # compare_digest, а не ==: обычное сравнение строк выходит из цикла на первом
    # различии, и по времени ответа подпись можно подобрать по байту.
    if not hmac.compare_digest(expected, received_hash):
        logger.warning("Подпись initData не сошлась")
        return None

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        auth_date = 0
    if not auth_date or time.time() - auth_date > MAX_AGE_SECONDS:
        logger.info("initData просрочен (auth_date=%s)", auth_date)
        return None

    # user приходит вложенным JSON внутри строки запроса. Его может не быть —
    # например, у инлайн-режима, — а без id мы никого опознать не можем.
    try:
        user = json.loads(pairs.get("user", ""))
    except (json.JSONDecodeError, ValueError):
        logger.warning("В initData нет разбираемого поля user")
        return None
    if not isinstance(user, dict) or not user.get("id"):
        return None

    return user


def user_of(request: web.Request) -> dict[str, Any] | None:
    """Пользователь мини-аппа, если запрос пришёл оттуда и подпись сошлась."""
    return request.get(USER_KEY)


# ─────────────────── Вход в панель из браузера ───────────────────
#
# В Telegram человека опознаёт мессенджер, в браузере опознавать нечем — там
# нужен пароль. Хранить сессии на сервере не станем: их пришлось бы держать в
# памяти (пропадают при каждом перезапуске) или в базе (таблица ради одного
# человека). Вместо этого клиенту выдаётся подписанная метка: внутри срок
# годности, рядом — подпись на токене бота. Подделать её без токена нельзя, а
# сервер ничего не помнит между запросами.

SESSION_COOKIE = "crm_session"

# Адреса, которые охрана пропускает всегда. Здесь спрашивают пароль и здесь же
# отвечают, нужен ли он вообще, — закрыть их значило бы запереть дверь вместе с
# ключом внутри.
# Открытые ручки. Вход — потому что иначе войти было бы нечем. Заказы гостя
# демо — потому что их спрашивает сайт-портфолио, у которого пароля от панели
# нет и быть не должно; отдаётся там строго один посетитель по своей метке,
# и только на чтение (см. web/api/demo.py).
OPEN_PATHS = frozenset({
    "/api/session",
    "/api/login",
    "/api/logout",
    "/api/demo/orders",
})


def _sign(payload: str) -> str:
    return hmac.new(
        config.BOT_TOKEN.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def make_session(now: float | None = None) -> str:
    """Метка входа: до какого момента действует и подпись этого срока."""
    expires = int((now or time.time()) + config.CRM_SESSION_DAYS * 24 * 60 * 60)
    return f"{expires}.{_sign(str(expires))}"


def session_valid(cookie: str | None) -> bool:
    """Цела ли метка и не вышел ли срок."""
    if not cookie or "." not in cookie:
        return False
    expires, _, signature = cookie.partition(".")
    if not expires.isdigit():
        return False
    if not hmac.compare_digest(_sign(expires), signature):
        return False
    return int(expires) > time.time()


def password_matches(given: str) -> bool:
    """Сравнение пароля постоянным временем.

    Сравниваем не сами строки, а их хеши, и на то две причины. Первая
    практическая: compare_digest отказывается работать со строками, где есть
    что-либо кроме ASCII, — пароль с русскими буквами ронял бы вход ошибкой
    сервера. Вторая: у хешей всегда одна длина, поэтому по времени ответа
    нельзя узнать даже, насколько длинный пароль задан.
    """
    if not config.crm_password_set():
        return False
    expected = hashlib.sha256(config.CRM_PASSWORD.encode()).digest()
    received = hashlib.sha256((given or "").encode()).digest()
    return hmac.compare_digest(expected, received)


def browser_allowed(request: web.Request) -> bool:
    """Пускать ли этот браузерный запрос в панель.

    Пока пароль не задан, панель открыта всем — так она работала раньше, и
    ломать локальный запуск ради настройки, которой у человека может не быть,
    нельзя. Предупреждение об этом висит в config.py и в .env.example.
    """
    if not config.crm_password_set():
        return True
    return session_valid(request.cookies.get(SESSION_COOKIE))


def is_admin(user: dict[str, Any] | None) -> bool:
    return bool(user) and int(user.get("id", 0)) == config.ADMIN_ID


@web.middleware
async def telegram_identity(request: web.Request, handler):
    """Опознаёт пришедшего из Telegram и охраняет админскую часть API.

    Заголовок с подписью есть — мы в Telegram, и подпись обязана сойтись.
    Заголовка нет — запрос из браузера, и там пускает пароль.

    Разделение по путям:
      * /api/session, /api/login — сам вход. Открыты всегда, иначе войти было
        бы нечем: чтобы получить пароль, нужно сначала спросить пароль.
      * /api/shop/...            — магазин. Нужен опознанный пользователь: без
        него неизвестно, чью корзину показывать.
      * /api/...                 — CRM. Из Telegram пускаем только ADMIN_ID; из
        браузера — по паролю (а если пароль не задан, то всех, как раньше).

    Фотографии (/media/...) остаются открытыми, и это не упущение: витрина
    показывает их обычным <img>, а туда заголовок с подписью не поставить.
    Секрета в снимках товаров нет — в отличие от заказов и телефонов рядом.

    Формат отказа берём из web.api.helpers, но импортируем его здесь, а не
    сверху файла: пакет web.api при загрузке тянет свои модули, а те — этот.
    На уровне модуля вышло бы кольцо, разорвать которое можно только вторым
    форматом ошибок — а он должен быть один на всё API.
    """
    from web.api.helpers import fail  # noqa: PLC0415

    raw = request.headers.get(INIT_DATA_HEADER, "")
    if raw:
        user = parse_init_data(raw)
        if user is None:
            return fail(
                "Не удалось подтвердить, что запрос пришёл из Telegram. "
                "Закройте и откройте приложение заново.",
                status=401,
            )
        request[USER_KEY] = user

    path = request.path
    if path in OPEN_PATHS:
        return await handler(request)

    if path.startswith("/api/shop/"):
        if not user_of(request):
            return fail("Магазин открывается только внутри Telegram.", status=401)
    elif path.startswith("/api/"):
        if raw:
            # Мы в Telegram: админские данные (телефоны, заказы, выручка)
            # достаются только владельцу.
            if not is_admin(user_of(request)):
                return fail("Этот раздел доступен только владельцу магазина.", status=403)
        elif not browser_allowed(request):
            return fail("Введите пароль, чтобы открыть панель.", status=401)

    return await handler(request)
