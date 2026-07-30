"""Карточка товара в чате: подпись, фото и кнопки под ними.

Отдельный модуль, потому что карточку показывают два разных пути — ИИ-продавец
(handlers/client.py) и каталог по кнопке (handlers/catalog.py). Расхождение
между ними покупатель увидел бы сразу: один и тот же товар выглядел бы
по-разному в зависимости от того, спросил он словами или нажал кнопку.
"""
from __future__ import annotations

import html
import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputMediaPhoto, Message

from services import media
from services.format import money, variant_label
from keyboards.orders import product_card_kb

logger = logging.getLogger(__name__)

# Telegram обрезает подпись к фото на 1024 символах — берём с запасом.
MAX_CAPTION = 1000

# Больше трёх фото в одном сообщении — уже лента, а не карточка: клиент
# пролистывает их и теряет из виду кнопку «В корзину».
MAX_SHOTS = 3


def card_caption(product: dict) -> str:
    """Подпись к фото товара: название, цена и что реально есть в наличии."""
    lines = [f"<b>{html.escape(product['title'])}</b>", money(product["price"])]

    in_stock = [v for v in product["variants"] if v["stock"] > 0]
    if in_stock:
        lines += ["", "В наличии:"]
        for variant in in_stock[:10]:
            lines.append(f"{html.escape(variant_label(variant))} — {variant['stock']} шт.")
        if len(in_stock) > 10:
            lines.append(f"…и ещё {len(in_stock) - 10}")
    else:
        lines += ["", "Сейчас нет в наличии."]

    caption = "\n".join(lines)
    return caption if len(caption) <= MAX_CAPTION else caption[:MAX_CAPTION - 1] + "…"


async def send_product_card(message: Message, product: dict) -> None:
    """Отправляет карточку товара: фото, подпись и кнопку «В корзину».

    Товар ожидается из get_product_full — с вариантами и фото.
    """
    caption = card_caption(product)
    markup = product_card_kb(product)  # None — если всё разобрали
    # Снятое в админке фото уходит по file_id, загруженное в веб-CRM — файлом
    # с диска; выданный Telegram file_id запоминаем на будущее.
    shots = media.sendable(product["photos"])[:MAX_SHOTS]
    try:
        if not shots:
            # Товар без фото — карточку всё равно показываем текстом, иначе
            # клиент не увидит ни цены, ни точного остатка.
            await message.answer(caption, parse_mode="HTML", reply_markup=markup)
        elif len(shots) == 1:
            sent = await message.answer_photo(
                shots[0][1], caption=caption, parse_mode="HTML", reply_markup=markup
            )
            await media.remember_file_ids([shots[0][0]], sent)
        else:
            album = [InputMediaPhoto(media=shots[0][1], caption=caption,
                                     parse_mode="HTML")]
            album += [InputMediaPhoto(media=shot) for _, shot in shots[1:]]
            sent = await message.answer_media_group(album)
            await media.remember_file_ids([photo for photo, _ in shots], sent)
            # У альбома кнопок быть не может — досылаем их отдельной строкой,
            # иначе товар с несколькими фото окажется единственным без кнопки.
            if markup:
                await message.answer(
                    f"<b>{html.escape(product['title'])}</b> — {money(product['price'])}",
                    parse_mode="HTML",
                    reply_markup=markup,
                )
    except TelegramBadRequest:
        # file_id мог протухнуть (фото удалено на стороне Telegram) — отдаём
        # хотя бы текст карточки, а не роняем весь ответ.
        logger.exception("Не удалось отправить фото товара %s", product["id"])
        await message.answer(caption, parse_mode="HTML", reply_markup=markup)
