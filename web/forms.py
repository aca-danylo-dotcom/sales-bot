"""Разбор того, что пришло из HTML-формы.

Браузер шлёт только строки, причём любые: поле цены может приехать пустым,
с запятой вместо точки, с пробелами из «1 200» или вообще с буквами, если
человек печатал в спешке. Значит, каждое значение приводится к типу здесь,
и хендлер работает уже с числом или None, а не с «чем-то от пользователя».

Правило раздела: непонятное значение — это не ноль и не пустая строка, а None.
Ноль в цене или остатке молча испортил бы каталог, а None даёт хендлеру шанс
сказать «проверьте поле» и ничего не сохранять.
"""
from __future__ import annotations

from typing import Mapping

# Длины полей: обрезаем, а не отклоняем — терять уже набранный текст обиднее,
# чем недосчитаться хвоста, который всё равно не поместится ни в карточку
# Telegram, ни в таблицу CRM.
MAX_TITLE = 120
MAX_DESCRIPTION = 1000
MAX_SHORT = 50


def text(data: Mapping, key: str, *, max_len: int = MAX_SHORT, default: str = "") -> str:
    """Строковое поле: убираем пробелы по краям и обрезаем по длине."""
    value = data.get(key)
    if not isinstance(value, str):
        return default
    return value.strip()[:max_len]


def price(value: object) -> float | None:
    """«1 200,50» → 1200.5. None — если это не похожа на цену.

    Формат тот же, что понимает админка бота (handlers/admin.py::parse_price):
    один и тот же продавец вводит цену то с телефона, то из браузера, и «1200,50»
    должно означать одно и то же в обоих местах.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.replace(",", ".").replace(" ", "").replace(" ", "").strip()
    if not cleaned:
        return None
    try:
        result = float(cleaned)
    except ValueError:
        return None
    return round(result, 2) if result > 0 else None


def optional_price(value: object) -> tuple[float | None, bool]:
    """Необязательная цена (например, «было»): (значение, всё ли в порядке).

    Пустое поле — это осознанное «скидки нет», а не ошибка ввода, поэтому
    возвращаем (None, True). А вот «абв» — уже (None, False).
    """
    if not isinstance(value, str) or not value.strip():
        return None, True
    parsed = price(value)
    return parsed, parsed is not None


def integer(value: object, *, minimum: int = 0, maximum: int = 1_000_000) -> int | None:
    """Целое в разумных границах. None — не число или вне границ."""
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
        number = int(value.strip())
    else:
        return None
    return number if minimum <= number <= maximum else None


def checkbox(data: Mapping, key: str) -> bool:
    """Флажок: браузер присылает поле только когда оно отмечено."""
    return key in data


def page(value: object) -> int:
    """Номер страницы из адреса. Мусор и отрицательные — первая страница."""
    number = integer(value, minimum=1, maximum=100_000)
    return (number or 1) - 1  # наружу 1-я, внутрь offset-страница 0-я


def stock_filter(value: object) -> bool | None:
    """Фильтр наличия: 'in' — есть, 'out' — нет, всё остальное — без фильтра."""
    if value == "in":
        return True
    if value == "out":
        return False
    return None


def plain_number(value: object) -> str:
    """Число обратно в поле формы: 1200.0 → «1200», 1200.5 → «1200.5», None → «».

    В поле ввода цена должна выглядеть так же, как её набирали. Показать там
    «1200.0» — значит заставить продавца стирать хвост при каждой правке, а
    показать «1 200 грн» — сломать сохранение, ведь обратно приедет та же строка.
    """
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else f"{number:.2f}".rstrip("0")


def status_filter(value: object) -> str | None:
    """Фильтр видимости: 'active' / 'hidden' / без фильтра."""
    return value if value in ("active", "hidden") else None
