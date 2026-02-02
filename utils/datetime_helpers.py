"""Вспомогательные функции для работы с datetime."""
from datetime import datetime, timezone
from typing import Any, Optional


def parse_iso_dt(value: Any) -> Optional[datetime]:
    """
    Безопасно распарсить ISO datetime.

    Parameters
    ----------
    value: Any
        Значение для парсинга (строка ISO формата, datetime объект или None).

    Returns
    -------
    Optional[datetime]
        Datetime объект или None.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def max_dt(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
    """
    Вернуть максимальное значение из двух datetime объектов.

    Корректно обрабатывает None и mixed timezone (aware/naive).

    Parameters
    ----------
    a: Optional[datetime]
        Первый datetime объект.
    b: Optional[datetime]
        Второй datetime объект.

    Returns
    -------
    Optional[datetime]
        Максимальный datetime или None.
    """
    if a is None:
        return b
    if b is None:
        return a
    try:
        return a if a >= b else b
    except TypeError:
        # aware vs naive: сравниваем по timestamp в UTC
        ts_a = dt_sort_ts(a)
        ts_b = dt_sort_ts(b)
        return a if ts_a >= ts_b else b


def dt_sort_ts(dt: Optional[datetime]) -> float:
    """
    Получить timestamp для сортировки datetime.

    Стабильный sort-key для datetime (не падает на aware/naive).

    Parameters
    ----------
    dt: Optional[datetime]
        Datetime объект.

    Returns
    -------
    float
        Timestamp в секундах или -inf для None.
    """
    if dt is None:
        return float("-inf")
    try:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).timestamp()
        return dt.timestamp()
    except Exception:
        return float("-inf")
