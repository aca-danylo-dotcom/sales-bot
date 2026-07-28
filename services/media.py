"""Файлы фотографий товаров на диске.

Зачем файл, если Telegram и так хранит картинку по file_id: file_id понимает
только Telegram. Веб-CRM и Instagram Direct получают ту же фотографию ссылкой
`/media/<photo_id>`, а отдавать её можно лишь с диска. Поэтому при загрузке
сохраняем оба представления — file_id в базу и файл сюда.
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

import config

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
