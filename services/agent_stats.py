"""Отправка событий в Agent Stats (дашборд статистики агентства).

Копия `integrations/report_event.py` из проекта agent-stats. Править здесь не нужно:
если модуль изменится там — файл заменяется целиком. Настройка через .env:

    AGENT_STATS_URL=https://stats.example.com
    AGENT_STATS_KEY=as_xxxxxxxx

Два правила, ради которых модуль такой скучный:

1. Ничего не ждём. Вызов возвращает управление сразу, отправка уходит в фоновую задачу.
2. Ничего не роняем. Сайт статистики недоступен, ключ не тот, интернет отвалился —
   бот об этом узнаёт только строчкой в логе.

Без `AGENT_STATS_KEY` модуль превращается в заглушку: бот работает как раньше.
Переменные читаются при импорте, поэтому импортировать его нужно после `import config`
(там грузится .env) — во всех модулях бота так и есть.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL = (os.getenv("AGENT_STATS_URL") or "").rstrip("/")
API_KEY = os.getenv("AGENT_STATS_KEY") or ""
TIMEOUT = float(os.getenv("AGENT_STATS_TIMEOUT") or 5)

# ссылки на задачи, иначе сборщик мусора может убить их до отправки
_pending: set[asyncio.Task] = set()


def enabled() -> bool:
    """Настроен ли отчёт. Если нет — все функции ниже молча ничего не делают."""
    return bool(BASE_URL and API_KEY)


def _iso(value: datetime | None) -> str | None:
    """Время в UTC строкой. Наивное время считаем UTC — так его хранят боты."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


async def _send(body: dict[str, Any]) -> None:
    """Одна попытка отправки. Повторов нет: событие не настолько ценно, чтобы копить очередь."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{BASE_URL}/api/v1/events",
                json=body,
                headers={"Authorization": f"Bearer {API_KEY}"},
            )
        if response.status_code >= 400:
            log.warning("agent-stats: %s на %s", response.status_code, body.get("type"))
    except Exception as error:  # noqa: BLE001 — бот не должен падать из-за статистики
        log.warning("agent-stats: событие %s не ушло (%s)", body.get("type"), error)


def report_event(event_type: str, payload: dict[str, Any], occurred_at: datetime | None = None) -> None:
    """Отправить произвольное событие. Возвращает управление сразу."""
    if not enabled():
        return

    body: dict[str, Any] = {"type": event_type, "payload": payload}
    if occurred_at is not None:
        body["occurred_at"] = _iso(occurred_at)

    try:
        task = asyncio.get_running_loop().create_task(_send(body))
    except RuntimeError:
        # вызов из синхронного кода без запущенного цикла — отправляем на месте
        try:
            asyncio.run(_send(body))
        except Exception as error:  # noqa: BLE001
            log.warning("agent-stats: событие %s не ушло (%s)", event_type, error)
        return

    _pending.add(task)
    task.add_done_callback(_pending.discard)


def report_operation(
    external_user_id: str | int,
    started_at: datetime | None = None,
    responded_at: datetime | None = None,
    result: str = "automated",
    **extra: Any,
) -> None:
    """Операция завершена: бот ответил пользователю или передал его человеку."""
    report_event(
        "operation_completed",
        {
            "external_user_id": str(external_user_id),
            "started_at": _iso(started_at),
            "responded_at": _iso(responded_at),
            "result": result,
            **extra,
        },
        occurred_at=responded_at,
    )


def report_error(kind: str, message: str = "", **extra: Any) -> None:
    """Ошибка у бота: не дошёл запрос к модели, упал внешний сервис и т.п."""
    report_event("error", {"kind": kind, "message": message[:2000], **extra})


def report_tokens(
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: float = 0,
    provider: str | None = None,
    **extra: Any,
) -> None:
    """Расход модели за один вызов."""
    report_event(
        "tokens_used",
        {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost": cost,
            "provider": provider,
            **extra,
        },
    )


def report_goal(goal: str, external_user_id: str | int | None = None, **extra: Any) -> None:
    """Цель достигнута: клиента записали, лид закрыт, вопрос решён.

    `goal` — короткое имя, одинаковое от раза к разу (`booking`, `cancel`,
    `reschedule`): по нему цели складываются в дашборде.
    """
    report_event(
        "goal_reached",
        {
            "goal": goal,
            "external_user_id": None if external_user_id is None else str(external_user_id),
            **extra,
        },
    )


__all__ = [
    "enabled",
    "report_event",
    "report_operation",
    "report_error",
    "report_tokens",
    "report_goal",
]
