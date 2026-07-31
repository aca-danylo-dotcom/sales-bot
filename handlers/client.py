"""Клиентский контур: весь диалог с покупателем ведёт ИИ-продавец.

Любое текстовое сообщение (в том числе нажатие кнопки нижнего меню) уходит в
модель. Модель через инструменты работает с каталогом и корзиной, а фотографии
товаров, о которых зашла речь, отправляет уже этот хендлер — см. ClientContext.

Порядок роутеров: этот подключается ПОСЛЕДНИМ, потому что ловит свободный текст
целиком и иначе перехватил бы кнопки админки и оформления заказа.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter, deque
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

import config
from db import queries
from services import agent_stats
from services.ai import Message as ConvMessage
from services.ai import ProviderUnavailable, run_agent
from services.ai_tools import MAX_CARDS, TOOLS, ClientContext, build_executor
from services.cards import send_product_card
from services.prompts import build_instructions
from keyboards.menus import main_menu
from keyboards.orders import added_kb

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

# Сколько разных пользователей держим в памяти для лимита частоты. Словари живут
# вечно, а ключ в них создаёт любой, кто написал боту хоть раз: при рассылке по
# ботам это тысячи мёртвых записей. Дойдя до потолка, выкидываем тех, чьё окно
# давно закрыто, — активным лимит от этого не сбивается.
_RATE_USERS_MAX = 5000

# Кому и когда уже сказали про суточный потолок (день по времени магазина).
# Ключ — клиент, значение — 'YYYY-MM-DD': повторять это сообщение на каждое
# следующее его сообщение бессмысленно, а один раз в сутки — честно.
_daily_warned: dict[int, str] = {}
# День, в который владельцу уже ушло письмо про общий потолок. Тоже один раз:
# упёршись в потолок, бот молчит для всех, и десять одинаковых сообщений подряд
# владельцу ничего не добавят.
_total_reported_day: str = ""

# Максимум символов во входящем сообщении: каждый символ — токены в платный ИИ.
# Лимит щедрый — живой покупатель его не заметит, а намеренный раздув отсекается.
_MAX_INPUT_CHARS = 1000

# Через сколько часов карточку товара можно показать тому же клиенту заново.
# В пределах одного разговора повтор не нужен — фото и цена видны выше в
# переписке. Но человек, вернувшийся на следующий день, листать вверх не станет,
# поэтому память о показанном протухает.
_CARD_REPEAT_HOURS = 12


def _get_lock(user_id: int) -> asyncio.Lock:
    return _user_locks.setdefault(user_id, asyncio.Lock())


def _forget_idle(now: float) -> None:
    """Убирает из памяти лимитов тех, кто давно не писал."""
    for stale in [uid for uid, hits in _user_hits.items()
                  if not hits or now - hits[-1] > _RATE_WINDOW]:
        lock = _user_locks.get(stale)
        # Занятый замок не трогаем: у клиента прямо сейчас думает модель, а
        # новый замок вместо него пустил бы следующее сообщение в обработку
        # параллельно — это гонка и дубль товара в корзине.
        if lock is not None and lock.locked():
            continue
        _user_hits.pop(stale, None)
        _user_warned.pop(stale, None)
        _user_locks.pop(stale, None)


def _rate_ok(user_id: int) -> bool:
    """True — сообщение в пределах лимита; False — лимит превышен."""
    now = time.monotonic()
    if len(_user_hits) > _RATE_USERS_MAX:
        _forget_idle(now)
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


async def _daily_ok(message: Message, bot: Bot, user_id: int) -> bool:
    """Суточные потолки платных запросов: True — можно идти в модель.

    Два рубежа. Первый — на клиента: обычный покупатель за день не пишет и сотни
    сообщений, а тот, кто пишет тысячи, платит нашими деньгами за чужой трафик.
    Второй — на весь бот: если аккаунтов много, персональный лимит их не удержит.

    Счётчик увеличиваем ТОЛЬКО когда запрос действительно уходит в модель:
    иначе упёршийся клиент своими же отбитыми сообщениями накручивал бы общий
    потолок и валил бота для остальных.
    """
    global _total_reported_day

    mine, total = await queries.get_ai_usage(user_id)
    today = config.today_local().isoformat()

    if total >= config.AI_DAILY_TOTAL:
        logger.error(
            "Достигнут общий суточный потолок запросов к ИИ (%d) — бот отвечать не будет",
            config.AI_DAILY_TOTAL,
        )
        if _total_reported_day != today:
            _total_reported_day = today
            try:
                await bot.send_message(
                    config.ADMIN_ID,
                    f"⚠️ Бот за сегодня израсходовал дневной лимит запросов к ИИ "
                    f"({config.AI_DAILY_TOTAL}) и больше не отвечает покупателям.\n\n"
                    "Так выглядит либо очень удачный день, либо накрутка. "
                    "Лимит поднимается переменной AI_DAILY_TOTAL.",
                )
            except Exception:  # владелец мог заблокировать бота
                logger.exception("Не удалось предупредить владельца о лимите ИИ")
        if _should_warn(user_id):
            await message.answer(
                "Извините, сегодня я уже не справляюсь с потоком сообщений. "
                "Напишите, пожалуйста, завтра — или оставьте вопрос, менеджер "
                "ответит вам сам 🙏",
                reply_markup=main_menu(),
            )
        return False

    if mine >= config.AI_DAILY_PER_CLIENT:
        logger.warning("Клиент %s выбрал суточный лимит запросов к ИИ (%d)",
                       user_id, config.AI_DAILY_PER_CLIENT)
        if _daily_warned.get(user_id) != today:
            _daily_warned[user_id] = today
            # Сообщение, на которое бот не ответил, всё же кладём в переписку:
            # менеджер в CRM должен видеть, чем человек закончил. Дальнейшие его
            # сообщения уже не пишем — иначе спам раздувает таблицу.
            await queries.add_message(user_id, "user", message.text)
            await queries.trim_history(user_id, _KEEP_HISTORY)
            # Владельцу — один раз за сутки на клиента. Живой покупатель до этой
            # черты не доходит, так что каждый такой случай стоит увидеть: это
            # либо накрутка, либо человек, которому бот так и не помог.
            try:
                await bot.send_message(
                    config.ADMIN_ID,
                    f"⚠️ Клиент {user_id} за сегодня выбрал дневной лимит "
                    f"сообщений ({config.AI_DAILY_PER_CLIENT}) — бот ему больше "
                    "не отвечает. Переписка есть в CRM.",
                )
            except Exception:  # владелец мог заблокировать бота
                logger.exception("Не удалось предупредить владельца о лимите клиента")
            await message.answer(
                "На сегодня мы с вами наговорились 🙂 Я отвечу завтра — "
                "а если вопрос срочный, напишите его одним сообщением, "
                "менеджер прочитает.",
                reply_markup=main_menu(),
            )
        return False

    await queries.bump_ai_usage(user_id)
    return True


# Ведущий маркер строки: кружочек/звёздочка/дефис-список, заголовок #, цитата >.
_MARKER_RE = re.compile(r"^(\s*)(?:[•*\-]\s+|#{1,6}\s+|>\s+)")

# Слова названия товара, чтобы понять, назвал ли его бот в ответе. Цифры внутри
# слова оставляем: «12 oz» и «Air Zoom 2» — часть названия.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Слова, которые сами по себе товар не опознают: они есть в половине названий.
_TITLE_STOP_WORDS = frozenset({"для", "від", "или", "под", "про", "the", "and", "with"})

# Прямая просьба показать фото — единственный повод прислать карточку повторно.
# Список нарочно узкий: одно только «покажи» сюда не входит, иначе «покажи 43-й»
# снова тащило бы карточку, от чего мы и уходим.
_PHOTO_REQUEST_RE = re.compile(
    r"фот|картинк|изображен|зображен|снимок|снимк|как выглядит|як вигляда", re.IGNORECASE
)


def _asks_for_photo(text: str) -> bool:
    """Клиент сам попросил фото — тогда повтор карточки уместен."""
    return bool(_PHOTO_REQUEST_RE.search(text))


# Слова-связки: из них состоит подводка к карточкам («вот что есть у нас в
# наличии»). Сами по себе они клиенту ничего не сообщают — всё, что он должен
# узнать, стоит в карточке ниже.
_LEAD_IN_WORDS = frozenset({
    "а", "в", "вам", "вас", "ваш", "ваши", "вот", "все", "всі", "выбирай",
    "выбирайте", "глянь", "гляньте", "готов", "готовы", "давай", "давайте",
    "держи", "держите", "для", "доступны", "друг", "есть", "є", "ж", "же", "и",
    "из", "или",
    "их", "й", "к", "как", "которые", "лежат", "лишились", "могу", "можно", "на",
    "наличии", "наличия", "наявності", "нас", "но", "ниже", "ну",
    "оба", "обе", "они", "остались", "от", "по", "под", "подойдут", "показываю",
    "посмотри", "посмотрите", "предложить", "прямо", "с", "сейчас", "смотри",
    "смотрите", "так", "также",
    "та", "тебе", "тобі", "тут", "у", "уже", "фото", "что", "щас", "это",
    "этого", "эти",
    # Цена и валюта: в карточке они стоят отдельной строкой.
    "грн", "uah", "за", "цена", "ціна", "стоит", "коштує", "размер", "розмір",
    "размеры", "розміри",
    # Счёт перечня — «есть три модели», «два варианта»: сколько карточек пришло,
    # клиент и так видит.
    "две", "дві", "два", "модель", "моделі", "модели", "моделей", "пять", "п'ять",
    "позиции", "позиції", "три", "четыре", "чотири", "штуки", "варианта",
    "варианты", "вариантов", "варіанти", "варіантів",
})

# Сколько «своих» слов сверх названий и связок ещё оставляют предложение
# подводкой. Одно — запас на категорию, которую бот почти всегда упоминает («из
# кроссовок», «по обуви»); двух слов хватает уже на рассказ о товаре («лёгкие,
# для бега»), а описание в карточку не входит, и терять его нельзя.
_LEAD_IN_TOLERANCE = 1

# Номер строки в перечне: «1.» и «2)» — оформление списка, а не сказанное число.
_LIST_NUMBER_RE = re.compile(r"^\s*\d+[.)]\s*", re.MULTILINE)

_DIGITS_RE = re.compile(r"\d+")


def _card_numbers(products: list[dict]) -> set[str]:
    """Числа, которые клиент и так прочитает в карточках: цены, размеры, остатки."""
    numbers: set[str] = set()
    for product in products:
        numbers |= set(_DIGITS_RE.findall(f"{product['price']:g}"))
        for variant in product["variants"]:
            numbers |= set(_DIGITS_RE.findall(f"{variant['size']} {variant['stock']}"))
    return numbers


# Одно предложение: разбираем ответ по частям, а не целиком.
_SENTENCE_RE = re.compile(r"[^.!?…]+[.!?…]*")


def _adds_nothing(reply: str, products: list[dict]) -> bool:
    """Кусок ответа — только подводка к карточкам, без собственного содержания?

    Клиент жаловался на лишнее сообщение перед фотографиями: бот перечислял
    товары с ценами, а следом приходили те же товары карточками — название,
    цена, размеры и кнопка «В корзину» в них уже есть. Такой текст ничего не
    добавляет, и мы его не отправляем: остаются одни карточки.

    Отправить всё же нужно, когда бот сказал что-то своё: задал вопрос, назвал
    число, которого в карточках нет (скидка, срок доставки), или добавил слова
    помимо названий товаров и служебных связок — описание в карточку не входит,
    и рассказ про товар клиент должен увидеть. Ошибиться безопаснее в сторону
    «отправить»: пропавший вопрос оставит клиента без ответа, а лишняя строка —
    просто лишняя строка.
    """
    if not products:
        return False
    if "?" in reply:
        return False

    body = _LIST_NUMBER_RE.sub("", reply)
    if set(_DIGITS_RE.findall(body)) - _card_numbers(products):
        return False

    title_words = {word for product in products for word in _title_words(product["title"])}
    own = [
        word for word in _WORD_RE.findall(body.lower())
        if not any(char.isdigit() for char in word)
        and word not in title_words and word not in _LEAD_IN_WORDS
    ]
    return len(own) <= _LEAD_IN_TOLERANCE


def _strip_lead_in(reply: str, products: list[dict]) -> str:
    """Убирает из ответа перечень товаров, оставляя всё остальное.

    Целиком ответ отбрасывать нельзя: к перечню бот часто добавляет полезное —
    «Какой размер ищете?». Поэтому смотрим предложение за предложением и
    выбрасываем только те, что называют товар и ничего к карточке не добавляют.
    Предложение без названия не трогаем никогда: это уже разговор, а не витрина.
    """
    if not products:
        return reply

    title_words = {word for product in products for word in _title_words(product["title"])}
    lines = []
    for line in reply.split("\n"):
        # Номер пункта отделяем сразу: точка в «1.» — оформление списка, по ней
        # нельзя резать на предложения. Пустой пункт потом уходит целиком.
        number = _LIST_NUMBER_RE.match(line)
        prefix, rest = (number.group(0), line[number.end():]) if number else ("", line)
        # Двоеточие в конце — шапка перечня («Есть три модели:»). Названий в ней
        # нет, но и смысла тоже: перечень под ней мы как раз убираем.
        heading = rest.rstrip().endswith(":")
        kept = [
            part for part in _SENTENCE_RE.findall(rest)
            if not (
                (heading or title_words & set(_WORD_RE.findall(part.lower())))
                and _adds_nothing(part, products)
            )
        ]
        body = "".join(kept).strip()
        lines.append(prefix + body if body else "")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


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
        f"Здравствуйте! Это {config.SHOP_NAME}, {config.SHOP_CITY}.\n\n"
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
            "Многовато текста получилось 🙂 Напишите покороче: что ищете, какой "
            "размер и цвет — так я быстрее подберу.",
            reply_markup=main_menu(),
        )
        return

    if not _rate_ok(user_id):
        if _should_warn(user_id):
            await message.answer(
                "Слишком много сообщений подряд — я не успеваю. Напишите, "
                "пожалуйста, через минуту.",
                reply_markup=main_menu(),
            )
        return

    # Суточный потолок — последний рубеж перед платным запросом: минутный лимит
    # держит очередь сообщений подряд, но не ровный поток сутки напролёт.
    if not await _daily_ok(message, bot, user_id):
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
    # Витрина — там же: пока модель узнавала ассортимент только через поиск, она
    # соглашалась с размером, которого на складе нет. Теперь список товаров и
    # размеров в наличии у неё перед глазами на каждом сообщении.
    showcase = await queries.get_showcase()
    # Память между разговорами. В модель уходит только хвост переписки, поэтому
    # прошлые покупки и заметки о клиенте подставляем отдельной выжимкой —
    # иначе постоянный покупатель каждый раз начинает знакомство заново.
    purchases = await queries.get_client_purchases(user_id)
    notes = await queries.get_client_notes(user_id)
    conv = _history_to_conversation(await queries.get_history(user_id, _MAX_HISTORY))

    await bot.send_chat_action(message.chat.id, "typing")
    try:
        text = await run_agent(
            instructions=build_instructions(
                client, cart["count"], showcase, purchases, notes),
            conversation=conv,
            tools=TOOLS,
            tool_executor=build_executor(ctx),
        )
    except ProviderUnavailable:
        # Профилактика на стороне провайдера — не наша поломка, и сказать об этом
        # стоит по-человечески: клиент вернётся, а не решит, что бот сломан.
        logger.warning("Провайдер ИИ недоступен, отвечаем клиенту %s мягко", user_id)
        await message.answer(
            "Извините, у меня сейчас технический перерыв — не могу посмотреть "
            "каталог. Напишите, пожалуйста, через несколько минут, я всё подберу 🙏",
            reply_markup=main_menu(),
        )
        _report_escalated(message, user_id)
        return
    except Exception as error:
        logger.exception("Ошибка агента для пользователя %s", user_id)
        await message.answer(
            "Что-то у меня не сложилось с ответом. Напишите, пожалуйста, ещё раз 🙏",
            reply_markup=main_menu(),
        )
        agent_stats.report_error("agent_failure", str(error))
        _report_escalated(message, user_id)
        return

    reply = _clean_markup(text).strip()
    # Карточки выбираем до отправки текста: перечень товаров из него вырезаем, и
    # если сверх перечня ничего не сказано — уходят одни карточки. Товар в
    # корзине карточки не требует: она
    # про то, что уже лежит в корзине, и кнопка «В корзину» под ней только путает.
    cards = [] if ctx.cart_added else await _cards_to_send(message, ctx, reply)

    if reply:
        visible = _strip_lead_in(reply, cards)
        if visible:
            # Товар уехал в корзину — под ответом нужен следующий шаг, а не нижнее
            # меню: те же кнопки, что и при добавлении из каталога.
            await message.answer(
                visible, reply_markup=added_kb() if ctx.cart_added else main_menu()
            )
        # В историю пишем в любом случае: для модели это её ответ, даже если
        # клиент прочитал его в виде карточек.
        await queries.add_message(user_id, "assistant", reply)
        await queries.trim_history(user_id, _KEEP_HISTORY)
    elif ctx.cart_added:
        await message.answer("Положил в корзину.", reply_markup=added_kb())

    for product in cards:
        await send_product_card(message, product)

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


def _title_words(title: str) -> list[str]:
    """Значимые слова названия, каждое по одному разу и в порядке названия."""
    return [
        word for word in dict.fromkeys(_WORD_RE.findall(title.lower()))
        if len(word) >= 3 and word not in _TITLE_STOP_WORDS
    ]


def _mentioned_positions(titles: dict[int, str], reply: str) -> dict[int, int]:
    """Какие товары консультант назвал в ответе и в каком месте — {id: позиция}.

    Поиск в каталоге нарочно ослабляет фильтры, поэтому на «давай посмотрим
    ботинки» он возвращает и боксёрские перчатки. Раньше фото уходило по всему,
    что вернул поиск, — клиент просил ботинки, а получал объявление с
    перчатками. Теперь карточка уходит только по товару, который назван в тексте.

    Сверяем по словам, а не по строке целиком: в базе «Чоловічі кросівки для
    бігу Adidas Adizero SL2», а бот пишет «Adidas Adizero SL2». Товар считаем
    названным, если в ответе нашлись два его слова — или хотя бы одно, которого
    нет в названиях других найденных товаров («adizero», «venum»): такое слово
    ни с чем не спутать. А общее «кросівки» при двух моделях в выдаче за
    название не сходит — иначе уедут обе карточки вместо одной.
    """
    words = {pid: _title_words(title) for pid, title in titles.items()}
    across = Counter(word for title_words in words.values() for word in title_words)
    reply_words = _WORD_RE.findall(reply.lower())

    positions: dict[int, int] = {}
    for pid, title_words in words.items():
        found = [(reply_words.index(word), word) for word in title_words
                 if word in reply_words]
        if not found:
            continue
        distinctive = any(across[word] == 1 for _, word in found)
        if len(found) >= 2 or distinctive or len(title_words) == 1:
            positions[pid] = min(index for index, _ in found)
    return positions


async def _cards_to_send(message: Message, ctx: ClientContext, reply: str) -> list[dict]:
    """Карточки товаров, о которых консультант рассказал в этом ответе.

    Инструменты фото не отправляют (модель их не видит) — они лишь помечают
    товары в ctx.show_products, а список хранит каждый товар один раз, поэтому
    в одном ответе одна и та же карточка не задваивается.

    Между сообщениями карточку не повторяем: клиент уточняет размер, модель в
    ответе обязана назвать товар по названию (иначе фото не уходит вообще) — и
    раньше на каждую такую реплику прилетала та же карточка по новому кругу.
    Что уже показано, помнит таблица shown_cards; повтор возможен, только если
    клиент прямо попросил фото или вернулся к товару спустя _CARD_REPEAT_HOURS.

    Порядок карточек — как в ответе: клиент читает «есть кроссовки и перчатки» и
    ниже видит их в том же порядке.

    Возвращаем список, а не отправляем сразу: по нему хендлер решает, нужно ли
    вообще слать текст ответа — см. _adds_nothing.

    Сама карточка живёт в services/cards.py: её же показывает каталог по
    кнопке, и выглядеть они обязаны одинаково.
    """
    products: dict[int, dict] = {}
    for product_id in ctx.show_products:
        product = await queries.get_product_full(product_id)
        if product and product["is_active"]:
            products[product_id] = product

    positions = _mentioned_positions(
        {pid: product["title"] for pid, product in products.items()}, reply
    )
    ordered = [pid for pid, _ in sorted(positions.items(), key=lambda item: item[1])]

    if not _asks_for_photo(message.text or ""):
        seen = await queries.get_shown_cards(message.from_user.id, _CARD_REPEAT_HOURS)
        ordered = [pid for pid in ordered if pid not in seen]

    ordered = ordered[:MAX_CARDS]
    await queries.remember_shown_cards(message.from_user.id, ordered)
    return [products[product_id] for product_id in ordered]
