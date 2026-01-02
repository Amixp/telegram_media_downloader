"""Модуль для фильтрации сообщений."""
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from telethon.tl.types import Message


class MediaFilter:
    """Класс для фильтрации медиа по различным критериям."""

    def __init__(self, config: Dict[str, Any]):
        """
        Инициализация MediaFilter.

        Parameters
        ----------
        config: Dict[str, Any]
            Конфигурация с настройками фильтров.
        """
        self.config = config
        self.sender_filter = config.get("sender_filter", {})
        self.enabled = self.sender_filter.get("enabled", False)
        self.user_ids = self.sender_filter.get("user_ids", [])
        self.usernames = self.sender_filter.get("usernames", [])
        
        # Преобразование дат из конфига в datetime объекты
        self.start_date = self._parse_date(config.get("start_date"))
        self.end_date = self._parse_date(config.get("end_date"))

    def should_download_by_sender(self, message: Message) -> bool:
        """
        Проверить, нужно ли загружать сообщение по отправителю.

        Parameters
        ----------
        message: Message
            Сообщение для проверки.

        Returns
        -------
        bool
            True, если сообщение должно быть загружено, False иначе.
        """
        if not self.enabled:
            return True

        # Если фильтр включен, но списки пусты, не загружаем ничего
        if not self.user_ids and not self.usernames:
            return False

        sender = message.sender_id
        if sender is None:
            return False

        # Проверка по user_id
        if self.user_ids and sender in self.user_ids:
            return True

        # Проверка по username (требует дополнительной информации о пользователе)
        # Это будет реализовано позже, когда будет доступ к информации о пользователе
        if self.usernames:
            # Пока возвращаем True, если есть user_ids и sender в списке
            # Или если нет user_ids, но есть usernames (требует дополнительной логики)
            pass

        return False

    def _parse_date(self, date_val: Any) -> Optional[datetime]:
        """
        Преобразовать значение даты из конфига в datetime объект.

        Parameters
        ----------
        date_val: Any
            Значение даты из конфига (строка ISO формата, date объект или None).

        Returns
        -------
        Optional[datetime]
            Datetime объект с timezone UTC или None.
        """
        if isinstance(date_val, str) and date_val.strip():
            parsed_date = datetime.fromisoformat(date_val)
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            return parsed_date
        elif isinstance(date_val, date):
            return datetime.combine(
                date_val, datetime.min.time(), tzinfo=timezone.utc
            )
        return None

    def should_download_by_size(
        self, file_size: Optional[int], min_size: Optional[int] = None, max_size: Optional[int] = None
    ) -> bool:
        """
        Проверить, нужно ли загружать файл по размеру.

        Parameters
        ----------
        file_size: Optional[int]
            Размер файла в байтах.
        min_size: Optional[int]
            Минимальный размер файла в байтах.
        max_size: Optional[int]
            Максимальный размер файла в байтах.

        Returns
        -------
        bool
            True, если файл должен быть загружен, False иначе.
        """
        if file_size is None:
            return True

        if min_size is not None and file_size < min_size:
            return False

        if max_size is not None and file_size > max_size:
            return False

        return True

    def should_download_by_date(
        self, message_date, start_date=None, end_date=None
    ) -> bool:
        """
        Проверить, нужно ли загружать сообщение по дате.

        Parameters
        ----------
        message_date
            Дата сообщения.
        start_date
            Начальная дата фильтра.
        end_date
            Конечная дата фильтра.

        Returns
        -------
        bool
            True, если сообщение должно быть загружено, False иначе.
        """
        if start_date and message_date < start_date:
            return False

        if end_date and message_date > end_date:
            return False

        return True

    def filter_message(self, message: Message) -> bool:
        """
        Применить все фильтры к сообщению.

        Parameters
        ----------
        message: Message
            Сообщение для фильтрации.

        Returns
        -------
        bool
            True, если сообщение должно быть загружено, False иначе.
        """
        # Фильтр по отправителю
        if not self.should_download_by_sender(message):
            return False

        # Фильтр по дате (используются предварительно преобразованные datetime объекты)
        if not self.should_download_by_date(message.date, self.start_date, self.end_date):
            return False

        return True
