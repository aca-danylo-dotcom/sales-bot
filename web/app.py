"""Веб-CRM: приложение aiohttp — JSON-API и отдача собранной панели.

Поднимается в том же процессе, что и бот (см. bot.py): одна база, один диск с
фотографиями, один планировщик — разносить это по двум процессам значило бы
делить между ними SQLite, а он такого не любит. Панель на React ничего в этом не
меняет: она собирается заранее (Vite кладёт файлы в web/dist) и отдаётся тем же
сервером, второго процесса на Node в проде нет.

ВХОДА НЕТ СОЗНАТЕЛЬНО: ни пароля, ни сессии, ни cookie. Кто открыл адрес — тот
внутри. Значит, адрес панели и есть весь доступ: на хостинге её видит любой, кто
этот адрес узнает, вместе со всеми заказами, телефонами и перепиской клиентов.
Если панель когда-нибудь выйдет наружу, ограничивать доступ придётся снаружи —
паролем на уровне хостинга, VPN или списком IP.

В приложение кладётся сам `bot` (`app["bot"]`): раздел «Заказы» не только меняет
статусы, но и пишет клиенту в его чат — подтверждение оплаты, накладную, отмену.
Без бота панель работает, но такие сообщения не уходят, и менеджер видит об этом
предупреждение.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

import config
from web import api

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
# Сюда Vite складывает собранную панель (web/frontend → npm run build). В
# репозитории папки нет: её делает сборка Docker-образа.
DIST_DIR = BASE_DIR / "dist"
INDEX_FILE = DIST_DIR / "index.html"

# Фотографии товаров приходят прямо из формы, причём пачкой: продавец выбирает
# все снимки разом. Стандартный лимит aiohttp (1 МБ) отбрасывал бы обычное фото
# с телефона целиком, поэтому поднимаем его до размера, в который влезает
# несколько снимков; отдельный файл всё равно ограничен в web/api/products.py.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Что сказать, если панель не собрана. Случай нередкий: локальный запуск бота без
# `npm run build`. Молчаливый 404 в этом месте выглядит как поломка сервера.
_NO_BUILD = (
    "Панель не собрана: нет web/dist/index.html.\n"
    "Соберите её командой «npm run build» в папке web/frontend "
    "или откройте dev-сервер Vite на порту 5173."
)


async def health(request: web.Request) -> web.Response:  # noqa: ARG001
    """Проверка живости для хостинга — без обращения к базе.

    Заодно отвечает на вопрос, ради которого иначе пришлось бы лезть в логи
    сервера: лежит база на постоянном диске или рядом с кодом, где её сотрёт
    первое же обновление. Путь внутри контейнера — не секрет, а «storage»
    видно с одного взгляда.
    """
    return web.json_response({
        "status": "ok",
        "storage": "volume" if config.DB_ON_VOLUME else "ephemeral",
        "db": str(config.DB_PATH),
        "media": str(config.MEDIA_DIR),
    })


@web.middleware
async def same_origin_only(request: web.Request, handler):
    """Не даёт чужой странице отправить форму в нашу панель.

    Обычная защита — токен в форме, привязанный к сессии, но сессий здесь нет
    (вход не предусмотрен), привязывать токен не к чему. Остаётся то, что
    браузер проставляет сам и что подделать со страницы нельзя: заголовок
    Origin. Он должен совпадать с адресом самой панели.

    Запросы без Origin пропускаем: так приходят curl и наши же проверочные
    скрипты, а межсайтовый запрос из браузера этот заголовок несёт всегда —
    в том числе fetch, которым теперь ходит панель.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.headers.get("Host"):
            logger.warning("Отклонён запрос с чужого адреса: %s", origin)
            raise web.HTTPForbidden(text="Запрос пришёл с чужой страницы.")
    return await handler(request)


async def index(request: web.Request) -> web.FileResponse:  # noqa: ARG001
    """Одна и та же страница на все адреса панели.

    Маршруты (`/orders/5`, `/products/new`) разбирает уже браузер, поэтому
    сервер обязан отдать index.html и на прямой заход, и на перезагрузку
    страницы — иначе закладка на карточку заказа открывала бы 404.

    Не кешируем: имя файла у index.html постоянное, а внутри — ссылки на
    ресурсы с новыми хешами. Закешированный index после обновления тянул бы
    файлы, которых уже нет.
    """
    if not INDEX_FILE.is_file():
        raise web.HTTPNotFound(text=_NO_BUILD)
    return web.FileResponse(INDEX_FILE, headers={"Cache-Control": "no-cache"})


def create_app(bot=None) -> web.Application:
    app = web.Application(
        client_max_size=MAX_UPLOAD_BYTES, middlewares=[same_origin_only]
    )
    app["bot"] = bot

    app.router.add_get("/health", health)
    # Данные и действия — здесь же и /media/<id> с фотографиями.
    api.setup_routes(app)

    # Файлы сборки. Имена у них с хешем содержимого (об этом заботится Vite),
    # поэтому кешировать можно надолго: изменившийся файл приедет по другому
    # адресу. Папки может не быть — при локальном запуске без сборки.
    assets = DIST_DIR / "assets"
    if assets.is_dir():
        app.router.add_static("/assets/", assets, name="assets")

    # Ловушка на всё остальное. Регистрируется последней: маршрут `{tail:.*}`
    # совпадает с чем угодно и, стоя выше, перехватил бы и /api, и /media.
    app.router.add_get("/{tail:.*}", index)
    return app


async def start_web(bot=None) -> web.AppRunner:
    """Поднимает CRM рядом с polling'ом бота."""
    runner = web.AppRunner(create_app(bot))
    await runner.setup()
    site = web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT)
    await site.start()
    logger.info("Веб-CRM слушает http://%s:%s (без входа)", config.WEB_HOST, config.WEB_PORT)
    return runner
