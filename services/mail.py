"""Письма клиентам — второй канал напоминаний рядом с Telegram.

Отправка нужна ровно для одного: догнать письмом того, кто оставил почту сам.
Никаких рассылок «по базе» здесь нет и быть не должно — адрес спрашивается по
желанию, а любое письмо несёт строку «не хотите писем — /email off».

Почему stdlib `smtplib`, а не асинхронная библиотека: писем мало (десятки в
день, а не тысячи), а лишняя зависимость в проекте — это ещё одна вещь, которая
однажды не соберётся на хостинге. Блокирующий вызов уводится в поток
(`asyncio.to_thread`), поэтому бот на время отправки не замирает.

Выключено по умолчанию: пустой SMTP_HOST значит «почты нет», и все функции
молча отвечают False. Так магазин работает без настроенного SMTP — просто без
писем.
"""
from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

import config

logger = logging.getLogger(__name__)

# Проверка адреса нарочно грубая: точная по RFC отвергает живые адреса, а
# единственная её задача здесь — отсечь «нет», «потом» и телефон, набранный не в
# то поле. Настоящая проверка адреса — это письмо, которое дошло.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")

# Сколько ждём сервер. Дольше держать смысла нет: напоминание не срочное, а
# зависший SMTP тормозил бы всю задачу рассылки.
_TIMEOUT = 20


def is_enabled() -> bool:
    """Настроена ли отправка писем."""
    return bool(config.SMTP_HOST and config.SMTP_FROM)


def looks_like_email(value: str) -> bool:
    """Похоже ли это на адрес почты."""
    return bool(_EMAIL_RE.match((value or "").strip()))


def normalize_email(value: str) -> str:
    """Адрес к единому виду: без пробелов, маленькими буквами."""
    return (value or "").strip().lower()


def _send_blocking(to: str, subject: str, text: str) -> None:
    """Синхронная отправка одного письма. Вызывается только из потока."""
    message = EmailMessage()
    message["From"] = formataddr((config.SMTP_FROM_NAME, config.SMTP_FROM))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)

    context = ssl.create_default_context()
    if config.SMTP_SSL:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT,
                              timeout=_TIMEOUT, context=context) as server:
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(message)
        return

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=_TIMEOUT) as server:
        server.starttls(context=context)
        if config.SMTP_USER:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(message)


async def send(to: str, subject: str, text: str) -> bool:
    """Отправляет письмо. False — не отправлено (и это не повод падать).

    Ошибку глотаем намеренно: письмо — вспомогательный канал. Если почтовый
    сервер лежит, напоминание всё равно уходит в Telegram, а задача рассылки
    должна дойти до конца списка, а не остановиться на первом отказе.
    """
    if not is_enabled() or not looks_like_email(to):
        return False

    try:
        await asyncio.to_thread(_send_blocking, normalize_email(to), subject, text)
        return True
    except Exception as exc:
        logger.warning("Письмо на %s не ушло: %s", to, exc)
        return False
