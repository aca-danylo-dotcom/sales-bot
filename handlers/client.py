"""Клиентский контур: всё, что видит покупатель.

На этапе каркаса — только /start. Дальше сюда приедет ИИ-консультант, который
будет отвечать на свободный текст; детерминированные потоки (корзина, оформление,
оплата) живут в отдельных роутерах и подключаются РАНЬШЕ этого, иначе ИИ
перехватит их кнопки.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

import config
from db import queries
from keyboards.menus import main_menu

router = Router(name="client")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Знакомство: заводим клиента и показываем главное меню."""
    await queries.ensure_client(message.from_user.id)
    await message.answer(
        f"Привет! Это {config.SHOP_NAME}, {config.SHOP_CITY}.\n\n"
        "Спрашивайте что угодно: подберу размер, покажу фото, скажу что есть в наличии.",
        reply_markup=main_menu(),
    )
