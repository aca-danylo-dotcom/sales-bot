"""Клиентский контур: весь диалог с покупателем ведёт ИИ-продавец.

Любое текстовое сообщение (в том числе нажатие кнопки нижнего меню) уходит в
модель. Модель через инструменты работает с каталогом и корзиной, а фотографии
товаров, о которых зашла речь, отправляет уже этот хендлер — см. ClientContext.

Порядок роутеров: этот подключается ПОСЛЕДНИМ, потому что ловит свободный текст
целиком и иначе перехватил бы кнопки админки и оформления заказа.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from collections import deque
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import InputMediaPhoto, Message

import config
from db import queries
from services import agent_stats
from services.ai import Message as ConvMessage
from services.ai import ProviderUnavailable, run_agent
from services.ai_tools import MAX_CARDS, TOOLS, ClientContext, build_executor
from services.format import money, variant_label
from services.prompts import build_instructions
from keyboards.menus import main_menu
from keyboards.orders import product_card_kb

logger = logging.getLogger(__name__)
router = Router(name="client")

# Сколько последних реплик диалога отдаём модели. История лежит в БД (её же
# читает менеджер в CRM), в контекст берём только хвост — чтобы не платить за
# всю переписку на каждом сообщении.
_MAX_HISTORY = 12

# Сколько реплик клиента держим в базе: модели нужен хвост, менеджеру — переписка
# за последние дни, остальное только раздувает таблицу.
_KEEP_HISTORY = 200

# Блокировка на клиента: сообщения одного пользователя обрабатываем по очереди.
# Иначе два быстрых сообщения идут параллельно и дают гонку — дубль ответа и,
# что хуже, двойное добавление в корзину.
_user_locks: dict[int, asyncio.Lock] = {}

# Антиспам: каждое текстовое сообщение уходит в платный ИИ. Скользящее окно —
# не больше _RATE_MAX сообщений за _RATE_WINDOW секунд; при превышении один раз
# предупреждаем и молчим.
_RATE_MAX = 10          # сообщений
_RATE_WINDOW = 60.0     # секунд
_user_hits: dict[int, deque[float]] = {}
_user_warned: dict[int, float] = {}

# Максимум символов во входящем сообщении: каждый символ — токены в платный ИИ.
# Лимит щедрый — живой покупатель его не заметит, а намеренный раздув отсекается.
_MAX_INPUT_CHARS = 1000

# Telegram обрезает подпись к фото на 1024 символах — берём с запасом.
_MAX_CAPTION = 1000


def _get_lock(user_id: int) -> asyncio.Lock:
    return _user_locks.setdefault(user_id, asyncio.Lock())


def _rate_ok(user_id: int) -> bool:
    """True — сообщение в пределах лимита; False — лимит превышен."""
    now = time.monotonic()
    hits = _user_hits.setdefault(user_id, deque())
    while hits and now - hits[0] > _RATE_WINDOW:
        hits.popleft()
    if len(hits) >= _RATE_MAX:
        return False
    hits.append(now)
    return True


def _should_warn(user_id: int) -> bool:
    """Предупреждаем о лимите не чаще раза в окно — иначе отвечаем на каждый спам-тик."""
    now = time.monotonic()
    if now - _user_warned.get(user_id, 0.0) > _RATE_WINDOW:
        _user_warned[user_id] = now
        return True
    return False


# Ведущий маркер строки: кружочек/звёздочка/дефис-список, заголовок #, цитата >.
_MARKER_RE = re.compile(r"^(\s*)(?:[•*\-]\s+|#{1,6}\s+|>\s+)")


def _clean_markup(text: str) -> str:
    """Убирает markdown-разметку и маркеры списков из ответа ИИ.

    parse_mode у бота намеренно не задан (чтобы <, >, & в тексте не ломали
    разметку Telegram), поэтому модельные **жирный**, *, `, • показывались бы
    БУКВАЛЬНО — покупатель видит мусорные символы. Чистим их.
    """
    lines = []
    for line in text.split("\n"):
        line = _MARKER_RE.sub(lambda m: m.group(1), line)  # ведущий маркер/заголовок
        line = line.replace("*", "").replace("`", "").replace("•", "")
        lines.append(line)
    return "\n".join(lines)


def _card_caption(product: dict) -> str:
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
    return caption if len(caption) <= _MAX_CAPTION else caption[:_MAX_CAPTION - 1] + "…"


def _history_to_conversation(rows: list[dict]) -> list[ConvMessage]:
    """История из БД → вход для модели.

    В базе лежат только реплики user/assistant: результаты инструментов туда не
    пишутся, они живут внутри одного прогона run_agent. Вход обязан начинаться
    с реплики пользователя, иначе модель его отвергнет.
    """
    conv: list[ConvMessage] = [
        {"role": row["role"], "content": row["content"]}
        for row in rows
        if row["role"] in ("user", "assistant") and row["content"]
    ]
    while conv and conv[0]["role"] != "user":
        conv.pop(0)
    return conv


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Знакомство: заводим клиента и показываем главное меню.

    Историю переписки НЕ стираем: она нужна менеджеру в CRM, а повторный /start
    у покупателя — обычное дело.
    """
    await queries.ensure_client(message.from_user.id)
    await message.answer(
        f"Привет! Это {config.SHOP_NAME}, {config.SHOP_CITY}.\n\n"
        "Спрашивайте что угодно: подберу размер, покажу фото, скажу что есть в наличии.",
        reply_markup=main_menu(),
    )


@router.message(F.text)
async def on_text(message: Message, bot: Bot) -> None:
    """Основной вход: любое текстовое сообщение покупателя обрабатывает ИИ."""
    user_id = message.from_user.id

    # Слишком длинное сообщение в ИИ не пускаем. Проверяем ДО лимита частоты,
    # чтобы случайная «простыня» не тратила попытку клиента.
    if len(message.text) > _MAX_INPUT_CHARS:
        await message.answer(
            "Многовато текста получилось 🙂 Напиши покороче: что ищешь, какой размер "
            "и цвет — так я быстрее подберу.",
            reply_markup=main_menu(),
        )
        return

    if not _rate_ok(user_id):
        if _should_warn(user_id):
            await message.answer(
                "Слишком много сообщений подряд — я не успеваю. Напиши, пожалуйста, "
                "через минуту.",
                reply_markup=main_menu(),
            )
        return

    async with _get_lock(user_id):
        await _run_and_reply(message, bot)


async def _run_and_reply(message: Message, bot: Bot) -> None:
    """Прогоняет агентный цикл, отвечает текстом и досылает карточки товаров."""
    user_id = message.from_user.id
    ctx = ClientContext(client_id=user_id)

    # Клиент мог написать боту, минуя /start, — заводим его здесь же, иначе
    # внешний ключ не даст сохранить ни реплику, ни корзину.
    await queries.ensure_client(user_id)
    await queries.add_message(user_id, "user", message.text)

    # Профиль и корзину подставляем в промпт на каждом сообщении: консультант не
    # переспрашивает уже известное и знает, что у клиента лежит в корзине.
    client = await queries.get_client(user_id)
    cart = await queries.get_cart(user_id)
    conv = _history_to_conversation(await queries.get_history(user_id, _MAX_HISTORY))

    await bot.send_chat_action(message.chat.id, "typing")
    try:
        text = await run_agent(
            instructions=build_instructions(client, cart["count"]),
            conversation=conv,
            tools=TOOLS,
            tool_executor=build_executor(ctx),
        )
    except ProviderUnavailable:
        # Профилактика на стороне провайдера — не наша поломка, и сказать об этом
        # стоит по-человечески: клиент вернётся, а не решит, что бот сломан.
        logger.warning("Провайдер ИИ недоступен, отвечаем клиенту %s мягко", user_id)
        await message.answer(
            "Извини, у меня сейчас технический перерыв — не могу посмотреть каталог. "
            "Напиши, пожалуйста, через несколько минут, я всё подберу 🙏",
            reply_markup=main_menu(),
        )
        _report_escalated(message, user_id)
        return
    except Exception as error:
        logger.exception("Ошибка агента для пользователя %s", user_id)
        await message.answer(
            "Что-то у меня не сложилось с ответом. Напиши, пожалуйста, ещё раз 🙏",
            reply_markup=main_menu(),
        )
        agent_stats.report_error("agent_failure", str(error))
        _report_escalated(message, user_id)
        return

    reply = _clean_markup(text).strip()
    if reply:
        await message.answer(reply, reply_markup=main_menu())
        await queries.add_message(user_id, "assistant", reply)
        await queries.trim_history(user_id, _KEEP_HISTORY)

    await _send_cards(message, ctx)

    # Обращение закрыл бот. started_at — когда клиент отправил сообщение, так что
    # во «время ответа» попадает и ожидание на блокировке пользователя.
    agent_stats.report_operation(
        user_id,
        started_at=message.date,
        responded_at=datetime.now(timezone.utc),
        result="automated",
        channel="ai",
    )


def _report_escalated(message: Message, user_id: int) -> None:
    """Обращение, которое бот сам не закрыл: клиент напишет снова или уйдёт."""
    agent_stats.report_operation(
        user_id,
        started_at=message.date,
        responded_at=datetime.now(timezone.utc),
        result="escalated",
    )


async def _send_cards(message: Message, ctx: ClientContext) -> None:
    """Досылает фото товаров, о которых консультант рассказал в этом ответе.

    Инструменты фото не отправляют (модель их не видит) — они лишь помечают
    товары в ctx.show_products, а список хранит каждый товар один раз, поэтому
    в одном ответе одна и та же карточка не задваивается.

    Под карточкой — кнопка «В корзину» (обработчики в handlers/orders.py):
    положить товар может и сам консультант, но тап по кнопке короче, чем
    объяснять модели, какой именно размер нужен.
    """
    for product_id in ctx.show_products[:MAX_CARDS]:
        product = await queries.get_product_full(product_id)
        if not product or not product["is_active"]:
            continue

        caption = _card_caption(product)
        markup = product_card_kb(product)  # None — если всё разобрали
        file_ids = [p["tg_file_id"] for p in product["photos"] if p["tg_file_id"]][:3]
        try:
            if not file_ids:
                # Товар без фото — карточку всё равно показываем текстом, иначе
                # клиент не увидит ни цены, ни точного остатка.
                await message.answer(caption, parse_mode="HTML", reply_markup=markup)
            elif len(file_ids) == 1:
                await message.answer_photo(
                    file_ids[0], caption=caption, parse_mode="HTML", reply_markup=markup
                )
            else:
                media = [InputMediaPhoto(media=file_ids[0], caption=caption,
                                         parse_mode="HTML")]
                media += [InputMediaPhoto(media=file_id) for file_id in file_ids[1:]]
                await message.answer_media_group(media)
                # У альбома кнопок быть не может — досылаем их отдельной строкой,
                # иначе товар с несколькими фото окажется единственным без кнопки.
                if markup:
                    await message.answer(
                        f"<b>{html.escape(product['title'])}</b> — "
                        f"{money(product['price'])}",
                        parse_mode="HTML",
                        reply_markup=markup,
                    )
        except TelegramBadRequest:
            # file_id мог протухнуть (фото удалено на стороне Telegram) — отдаём
            # хотя бы текст карточки, а не роняем весь ответ.
            logger.exception("Не удалось отправить фото товара %s", product_id)
            await message.answer(caption, parse_mode="HTML", reply_markup=markup)
