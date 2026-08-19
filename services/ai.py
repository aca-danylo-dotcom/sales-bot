"""Агентное ядро ИИ-консультанта.

Провайдер — Anthropic (Claude, Messages API). Здесь спрятано всё, что знает о
конкретном провайдере: формат инструментов, цикл «модель ↔ инструменты», разбор
ответа и учёт расхода. Остальной код о провайдере не знает — он вызывает
`run_agent()` и получает текст для покупателя.

`run_agent()` гоняет цикл: пока модель просит вызвать инструмент — выполняем его
и возвращаем результат; как только она отвечает текстом — отдаём текст. История
диалога (`conversation`) мутируется на месте, чтобы вызывающая сторона могла
сохранить её между сообщениями.

Про формат инструментов. В db-схемах (services/ai_tools.py) они описаны так, как
их принимал прежний провайдер: `{"type": "function", "name", "parameters"}`.
Claude ждёт `{"name", "description", "input_schema"}`. Переписывать четыре сотни
строк схем ради переименования двух ключей незачем — перевод делает `_to_claude`
один раз при загрузке модуля.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

import anthropic

import config
from services import agent_stats

logger = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=config.AI_API_KEY)

# Потолок на один ответ. Консультант пишет две-три фразы, а не статьи; с запасом
# хватает и тысячи токенов. Платим за фактически написанное, поэтому запас
# ничего не стоит — а упёршийся в потолок ответ обрывается на полуслове.
MAX_ANSWER_TOKENS = 2048

# Исполнитель инструмента: (имя, аргументы) -> строка-результат (обычно JSON) для модели.
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]

# Тип элемента истории диалога (реплики ролей; внутри хода — ещё и блоки вызовов).
Message = dict[str, Any]

# Слова провайдера о кончившихся деньгах. Ловим их отдельно: для покупателя это
# такой же «перерыв», как профилактика, а вот в лог должно попасть настоящее
# объяснение — иначе владелец ищет поломку там, где её нет.
_NO_CREDITS_MARKERS = ("credit balance", "insufficient", "billing")


class ProviderUnavailable(RuntimeError):
    """Модель недоступна не по нашей вине: обрыв связи, лимит, кончились деньги.

    Отдельный тип нужен, чтобы хендлер ответил покупателю по-человечески
    («ИИ на техперерыве»), а не общим «что-то пошло не так».
    """


def _to_claude(tool: dict[str, Any]) -> dict[str, Any]:
    """Схема инструмента из формата прежнего провайдера в формат Claude."""
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
    }


def _prepare_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Инструменты для запроса, с пометкой кеширования на последнем.

    Кешируется весь кусок запроса ДО пометки, а инструменты идут первыми —
    значит одна пометка на последнем из них кеширует список целиком. Он не
    меняется никогда, а весит несколько тысяч токенов в каждом запросе: без
    кеша мы платили бы за одно и то же описание девяти инструментов при каждой
    реплике покупателя.

    Системный промпт кешировать нельзя: в нём профиль клиента, корзина и
    витрина — он разный почти на каждом запросе, и пометка на нём только
    создавала бы кеш, которым никто не воспользуется.
    """
    prepared = [_to_claude(tool) for tool in tools]
    if prepared:
        prepared[-1] = {**prepared[-1], "cache_control": {"type": "ephemeral"}}
    return prepared


async def run_agent(
    *,
    instructions: str,
    conversation: list[Message],
    tools: list[dict[str, Any]],
    tool_executor: ToolExecutor,
    max_iterations: int = 6,
) -> str:
    """Прогоняет один «ход» консультанта (возможно с несколькими вызовами инструментов).

    Args:
        instructions: системный промпт (роль, факты о магазине, правила общения).
        conversation: история (мутируется — сюда дописываются вызовы и ответ).
        tools: схемы инструментов (формат ai_tools.TOOLS).
        tool_executor: async-функция, выполняющая инструмент по имени и аргументам.
        max_iterations: предохранитель от зацикливания на инструментах.

    Returns:
        Финальный текст ответа для покупателя.

    Raises:
        ProviderUnavailable: модель не ответила по причине на стороне провайдера.
    """
    prepared = _prepare_tools(tools)

    for _ in range(max_iterations):
        response = await _request(instructions, conversation, prepared)
        _report_usage(response)

        calls = [block for block in response.content if block.type == "tool_use"]
        if not calls:
            text = "".join(
                block.text for block in response.content if block.type == "text"
            )
            conversation.append({"role": "assistant", "content": text})
            return text

        # Ход модели с вызовами кладём в историю целиком: Claude требует, чтобы
        # ответ с результатами ссылался на те же блоки, что он прислал.
        conversation.append({"role": "assistant", "content": response.content})

        # Все результаты — ОДНИМ сообщением. Разбить их на несколько значит
        # отучить модель вызывать инструменты параллельно.
        results = []
        for call in calls:
            output = await _safe_execute(tool_executor, call.name, call.input)
            results.append(
                {"type": "tool_result", "tool_use_id": call.id, "content": output}
            )
        conversation.append({"role": "user", "content": results})

    logger.warning("run_agent: превышен лимит итераций (%d)", max_iterations)
    agent_stats.report_error(
        "max_iterations",
        f"модель не уложилась в {max_iterations} шагов и ответила заглушкой",
    )
    fallback = "Не поймал запрос с ходу. Напишите, пожалуйста, иначе — подберу."
    conversation.append({"role": "assistant", "content": fallback})
    return fallback


async def _request(
    instructions: str, conversation: list[Message], tools: list[dict[str, Any]]
):
    """Один запрос к модели.

    Всё, что провайдер может вернуть не по нашей вине, переводим в
    ProviderUnavailable: покупателю в любом случае уходит одно и то же
    «технический перерыв», а разбираться по логам должен владелец, и там
    причина названа своим именем.

    Ошибки в самом запросе (битая схема инструмента, неверная модель) наверх
    идут как есть: это наша поломка, и прятать её за «перерывом» — верный
    способ не узнать о ней никогда.
    """
    try:
        return await _client.messages.create(
            model=config.AI_MODEL,
            max_tokens=MAX_ANSWER_TOKENS,
            system=instructions,
            messages=conversation,
            tools=tools,
        )
    except anthropic.BadRequestError as error:
        text = str(error).lower()
        if any(marker in text for marker in _NO_CREDITS_MARKERS):
            logger.error("НА СЧЕТУ ANTHROPIC КОНЧИЛИСЬ ДЕНЬГИ: %s", error)
            agent_stats.report_error("no_credits", str(error))
            raise ProviderUnavailable("нет средств на счету провайдера") from error
        raise
    except anthropic.AuthenticationError as error:
        # Ключ не тот или отозван. Для покупателя это тоже «перерыв», но в логе
        # должно быть видно, что чинится это одной переменной окружения.
        logger.error("Anthropic не принял ключ (ANTHROPIC_API_KEY): %s", error)
        agent_stats.report_error("bad_api_key", str(error))
        raise ProviderUnavailable("провайдер не принял ключ") from error
    except anthropic.RateLimitError as error:
        logger.warning("Anthropic ограничил частоту запросов: %s", error)
        agent_stats.report_error("rate_limited", str(error))
        raise ProviderUnavailable("слишком много запросов") from error
    except anthropic.APIStatusError as error:
        if error.status_code >= 500:
            logger.warning("Anthropic отвечает ошибкой %s", error.status_code)
            agent_stats.report_error("provider_unavailable", str(error))
            raise ProviderUnavailable(f"ошибка провайдера {error.status_code}") from error
        raise
    except anthropic.APIConnectionError as error:
        logger.warning("Не достучались до Anthropic: %s", error)
        agent_stats.report_error("provider_unavailable", str(error))
        raise ProviderUnavailable("нет связи с провайдером") from error


def _report_usage(response) -> None:
    """Расход одного запроса — в статистику агентства.

    Кешированные токены считаем вместе с обычными: они тоже потрачены, просто
    дешевле. Стоимость при этом считается по полной цене входа — небольшая
    переплата в отчёте честнее, чем занижение: владелец планирует бюджет по
    этим числам, и «вышло дешевле, чем в отчёте» его не подведёт.
    """
    if not agent_stats.enabled():
        return

    usage = getattr(response, "usage", None)
    if usage is None:
        return

    tokens_in = (
        int(getattr(usage, "input_tokens", 0) or 0)
        + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    )
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    if not (tokens_in or tokens_out):
        return

    cost = (tokens_in * config.AI_PRICE_IN + tokens_out * config.AI_PRICE_OUT) / 1_000_000
    agent_stats.report_tokens(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=round(cost, 6),
        provider="anthropic",
        model=config.AI_MODEL,
    )


async def _safe_execute(
    tool_executor: ToolExecutor, name: str, arguments: dict[str, Any]
) -> str:
    """Выполняет инструмент, не давая исключению уронить весь диалог.

    Аргументы приходят уже разобранными — Claude отдаёт их объектом, а не
    строкой JSON, поэтому разбирать здесь нечего.
    """
    try:
        return await tool_executor(name, arguments or {})
    except Exception as exc:  # инструмент не должен ронять разговор
        logger.exception("Ошибка инструмента %s", name)
        agent_stats.report_error("tool_failure", f"{name}: {exc}")
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
