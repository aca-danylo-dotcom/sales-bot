"""Агентное ядро ИИ-консультанта.

Провайдер — kie.ai (OpenAI Responses API). Тонкости подключения (браузерный
User-Agent от Cloudflare, стриминг SSE, формат tool-цикла) инкапсулированы здесь.

`run_agent()` гоняет цикл «модель ↔ инструменты»: пока модель просит вызвать
инструмент — выполняем его и возвращаем результат; как только она отвечает
текстом — отдаём текст покупателю. История диалога (`conversation`) мутируется
на месте, чтобы вызывающая сторона могла сохранить её между сообщениями.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

import config
from services import agent_stats

logger = logging.getLogger(__name__)

# Без браузерного UA Cloudflare kie.ai отдаёт 403 (error 1010).
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_client = AsyncOpenAI(
    api_key=config.AI_API_KEY,
    base_url=config.AI_BASE_URL,
    default_headers={"User-Agent": _USER_AGENT},
)

# Исполнитель инструмента: (имя, аргументы) -> строка-результат (обычно JSON) для модели.
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]

# Тип элемента истории диалога (сообщения ролей + записи вызовов инструментов).
Message = dict[str, Any]


class ProviderUnavailable(RuntimeError):
    """Провайдер недоступен (техобслуживание, обрыв потока) — не вина нашего кода.

    Отдельный тип нужен, чтобы хендлер ответил покупателю по-человечески
    («ИИ на техперерыве»), а не общим «что-то пошло не так».
    """


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
        tools: схемы инструментов в формате Responses API.
        tool_executor: async-функция, выполняющая инструмент по имени и аргументам.
        max_iterations: предохранитель от зацикливания на инструментах.

    Returns:
        Финальный текст ответа для покупателя.

    Raises:
        ProviderUnavailable: провайдер не отдал ответ (профилактика, обрыв SSE).
    """
    for _ in range(max_iterations):
        final = await _request(instructions, conversation, tools)
        _report_usage(final)

        calls = [item for item in final.output if item.type == "function_call"]
        if not calls:
            text = final.output_text or ""
            conversation.append({"role": "assistant", "content": text})
            return text

        # Модель запросила инструменты — выполняем каждый и возвращаем результат.
        for call in calls:
            conversation.append(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
            )
            result = await _safe_execute(tool_executor, call.name, call.arguments)
            conversation.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result,
                }
            )

    logger.warning("run_agent: превышен лимит итераций (%d)", max_iterations)
    agent_stats.report_error(
        "max_iterations",
        f"модель не уложилась в {max_iterations} шагов и ответила заглушкой",
    )
    fallback = "Не поймал запрос с ходу. Напишите иначе или загляните в каталог по кнопке ниже."
    conversation.append({"role": "assistant", "content": fallback})
    return fallback


async def _request(instructions: str, conversation: list[Message], tools: list[dict[str, Any]]):
    """Один запрос к модели через стриминг (kie.ai отдаёт только SSE).

    Во время профилактики kie.ai отвечает HTTP 200 с телом
    `{"code":500,"msg":"...maintained..."}` вместо потока событий. SDK не видит
    ошибки (статус-то 200), не дожидается `response.completed` и падает с
    RuntimeError. Переводим этот случай в ProviderUnavailable, чтобы наверху
    отличить чужую профилактику от нашей поломки.
    """
    try:
        async with _client.responses.stream(
            model=config.AI_MODEL,
            instructions=instructions,
            input=conversation,
            tools=tools,
            tool_choice="auto",
            reasoning={"effort": config.AI_REASONING_EFFORT},
        ) as stream:
            async for _event in stream:
                pass  # дельты нам не нужны — забираем финал целиком
            return await stream.get_final_response()
    except RuntimeError as exc:
        if "response.completed" in str(exc):
            logger.warning("kie.ai не отдал поток (похоже на техобслуживание): %s", exc)
            agent_stats.report_error("provider_unavailable", str(exc))
            raise ProviderUnavailable(str(exc)) from exc
        raise


def _report_usage(response) -> None:
    """Расход одного запроса к модели — в статистику агентства.

    Провайдер может не прислать usage (или прислать неполный) — тогда просто молчим:
    статистика не повод ронять ответ покупателю.
    """
    if not agent_stats.enabled():
        return

    usage = getattr(response, "usage", None)
    tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    if not (tokens_in or tokens_out):
        return

    cost = (tokens_in * config.AI_PRICE_IN + tokens_out * config.AI_PRICE_OUT) / 1_000_000
    agent_stats.report_tokens(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=round(cost, 6),
        provider="kie.ai",
        model=config.AI_MODEL,
    )


async def _safe_execute(tool_executor: ToolExecutor, name: str, raw_arguments: str) -> str:
    """Выполняет инструмент, не давая исключению уронить весь диалог."""
    try:
        arguments = json.loads(raw_arguments) if raw_arguments else {}
    except json.JSONDecodeError:
        logger.warning("Некорректный JSON аргументов инструмента %s: %r", name, raw_arguments)
        agent_stats.report_error("invalid_tool_arguments", f"{name}: {raw_arguments!r}")
        return json.dumps({"error": "invalid_arguments"}, ensure_ascii=False)

    try:
        return await tool_executor(name, arguments)
    except Exception as exc:  # инструмент не должен ронять разговор
        logger.exception("Ошибка инструмента %s", name)
        agent_stats.report_error("tool_failure", f"{name}: {exc}")
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
