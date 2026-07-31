"""Мелочи, общие для всех модулей API: ответы, разбор тела запроса.

Отдельным файлом, потому что иначе каждый роутер начинался бы с одинаковых
десяти строк, а расходиться они не должны: формат ошибки и формат успеха
разбирает один и тот же клиентский код.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from aiohttp import web


def _dumps(value: Any) -> str:
    """JSON с живыми буквами: ensure_ascii раздувает русский текст вчетверо."""
    return json.dumps(value, ensure_ascii=False)


def ok(payload: Mapping[str, Any] | None = None, **extra: Any) -> web.Response:
    """Успешный ответ. Пустые поля выкидываем: `null` в JSON клиенту не нужен."""
    data: dict[str, Any] = dict(payload or {})
    data.update({k: v for k, v in extra.items() if v not in (None, "")})
    return web.json_response(data, dumps=_dumps)


def fail(message: str, status: int = 400, **extra: Any) -> web.Response:
    """Отказ с человеческим текстом. Клиент показывает его как есть."""
    return web.json_response({"error": message, **extra}, status=status, dumps=_dumps)


def not_found(message: str) -> web.HTTPNotFound:
    """404 в том же формате, что и остальные ошибки, — иначе клиент их не разберёт."""
    return web.HTTPNotFound(
        text=_dumps({"error": message}), content_type="application/json"
    )


async def body(request: web.Request) -> Mapping[str, Any]:
    """Тело запроса как словарь — хоть JSON, хоть форма.

    Действия панель шлёт как JSON, а карточку товара — как `multipart/form-data`:
    вместе с полями там едут файлы. Разбирать это по-разному в каждом хендлере
    незачем: наружу и то и другое выглядит одинаково, а `web.forms` умеет читать
    из любого Mapping.
    """
    if request.content_type == "application/json":
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    return await request.post()
