"""Файлы фотографий товаров на диске.

Зачем файл, если Telegram и так хранит картинку по file_id: file_id понимает
только Telegram. Веб-CRM и Instagram Direct получают ту же фотографию ссылкой
`/media/<photo_id>`, а отдавать её можно лишь с диска. Поэтому при загрузке
сохраняем оба представления — file_id в базу и файл сюда.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Sequence
from uuid import uuid4

from aiogram.types import FSInputFile, Message

import config
from db import queries

logger = logging.getLogger(__name__)


def media_dir() -> Path:
    """Папка с фото; создаётся при первом обращении."""
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    return config.MEDIA_DIR


def photo_path(file_name: str) -> Path:
    """Полный путь к файлу. В базе хранится только имя — папку задаёт .env."""
    return config.MEDIA_DIR / file_name


async def save_telegram_photo(bot, file_id: str, product_id: int) -> str | None:
    """Скачивает фото из Telegram на диск и возвращает имя файла.

    Ошибка скачивания не должна ронять загрузку товара: file_id уже сохранён,
    в Telegram картинка будет показываться и без файла — не будет только в вебе.
    """
    file_name = f"{product_id}_{uuid4().hex[:8]}.jpg"
    try:
        await bot.download(file_id, destination=media_dir() / file_name)
        return file_name
    except Exception:
        logger.exception("Не удалось сохранить фото товара %s на диск", product_id)
        return None


def remove_photo_file(file_name: str | None) -> None:
    """Убирает файл с диска. Отсутствующий файл — не ошибка."""
    if not file_name:
        return
    try:
        photo_path(file_name).unlink(missing_ok=True)
    except OSError:
        logger.exception("Не удалось удалить файл фото %s", file_name)


def sendable(photos: Iterable[dict]) -> list[tuple[dict, str | FSInputFile]]:
    """Пары «запись о фото → что передать в Telegram», в исходном порядке.

    Фото приходят из двух мест и выглядят по-разному: снятое в админке бота
    имеет tg_file_id (его достаточно), а загруженное в веб-CRM лежит только
    файлом на диске. Без этой развилки веб-фото просто не показывалось бы
    клиенту — товар, заведённый в браузере, уходил бы в чат голым текстом.

    Записи без файла и без file_id пропускаем: отправлять нечего.
    """
    result: list[tuple[dict, str | FSInputFile]] = []
    for photo in photos:
        if photo.get("tg_file_id"):
            result.append((photo, photo["tg_file_id"]))
            continue
        name = photo.get("file_path")
        if name and photo_path(name).is_file():
            result.append((photo, FSInputFile(photo_path(name))))
    return result


async def remember_file_ids(
    photos: Sequence[dict], sent: Message | Sequence[Message] | None
) -> None:
    """Запоминает file_id, который Telegram выдал на только что отправленный файл.

    Ленивый кеш: файл с диска заливается один раз, дальше карточка уходит по
    file_id — быстро и без трафика. Порядок сообщений в альбоме совпадает с
    порядком отправленных фото, поэтому сопоставляем по индексу.

    Ошибка записи не должна ломать ответ клиенту: он фото уже получил, а
    неудачный кеш означает лишь повторную заливку в следующий раз.
    """
    if sent is None:
        return
    messages = [sent] if isinstance(sent, Message) else list(sent)
    for photo, message in zip(photos, messages):
        if photo.get("tg_file_id") or not message.photo:
            continue
        try:
            await queries.set_photo_tg_file_id(photo["id"], message.photo[-1].file_id)
        except Exception:
            logger.exception("Не удалось запомнить file_id фото %s", photo.get("id"))
