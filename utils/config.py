"""Утилиты для управления конфигурацией."""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

logger = logging.getLogger(__name__)


class ConfigManager:
    """Класс для управления конфигурацией проекта."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Инициализация ConfigManager.

        Parameters
        ----------
        config_path: Optional[str]
            Путь к файлу конфигурации. Если None, используется config.yaml в директории скрипта.
        """
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml"
            )
        self.config_path = config_path
        self._config: Optional[Dict[str, Any]] = None

    def load(self) -> Dict[str, Any]:
        """
        Загрузить конфигурацию из файла.

        Returns
        -------
        Dict[str, Any]
            Словарь с конфигурацией.

        Raises
        ------
        FileNotFoundError
            Если файл конфигурации не найден.
        yaml.YAMLError
            Если файл содержит некорректный YAML.
        """
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Файл конфигурации не найден: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f) or {}

        self.validate()
        return self._config

    def validate(self) -> None:
        """
        Валидация конфигурации.

        Raises
        ------
        ValueError
            Если конфигурация некорректна.
        """
        if self._config is None:
            raise ValueError("Конфигурация не загружена. Вызовите load() сначала.")

        # Проверка обязательных полей
        required_fields = ["api_id", "api_hash"]
        for field in required_fields:
            if field not in self._config:
                raise ValueError(f"Отсутствует обязательное поле: {field}")

        # Валидация api_id
        if not isinstance(self._config["api_id"], int):
            raise ValueError("api_id должен быть целым числом")

        # Валидация api_hash
        if not isinstance(self._config["api_hash"], str) or not self._config["api_hash"]:
            raise ValueError("api_hash должен быть непустой строкой")

        # Валидация media_types
        if "media_types" in self._config:
            valid_media_types = [
                "audio",
                "document",
                "photo",
                "video",
                "voice",
                "video_note",
                "all",
            ]
            media_types = self._config["media_types"]
            if isinstance(media_types, list):
                for media_type in media_types:
                    if media_type not in valid_media_types:
                        raise ValueError(
                            f"Некорректный тип медиа: {media_type}. "
                            f"Допустимые значения: {', '.join(valid_media_types)}"
                        )
            elif media_types not in valid_media_types:
                raise ValueError(
                    f"Некорректный тип медиа: {media_types}. "
                    f"Допустимые значения: {', '.join(valid_media_types)}"
                )

        # Валидация file_formats
        if "file_formats" in self._config:
            file_formats = self._config["file_formats"]
            if not isinstance(file_formats, dict):
                raise ValueError("file_formats должен быть словарем")

            for media_type, formats in file_formats.items():
                if not isinstance(formats, list):
                    raise ValueError(
                        f"file_formats[{media_type}] должен быть списком"
                    )

        # Валидация proxy
        if "proxy" in self._config:
            # Отложенный импорт для избежания циклической зависимости
            from utils.proxy import validate_proxy_config

            if not validate_proxy_config(self._config["proxy"]):
                logger.warning("⚠️ Конфигурация прокси невалидна, прокси не будет использован")
                self._config["proxy"] = None

    def save(self, config: Optional[Dict[str, Any]] = None) -> None:
        """
        Сохранить конфигурацию в файл.

        Parameters
        ----------
        config: Optional[Dict[str, Any]]
            Конфигурация для сохранения. Если None, используется загруженная конфигурация.
        """
        config_to_save = config if config is not None else self._config
        if config_to_save is None:
            raise ValueError("Нет конфигурации для сохранения")

        # Создать директорию, если не существует
        config_dir = os.path.dirname(self.config_path)
        if config_dir:
            os.makedirs(config_dir, exist_ok=True)

        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_to_save, f, default_flow_style=False, allow_unicode=True)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Получить значение из конфигурации.

        Parameters
        ----------
        key: str
            Ключ конфигурации.
        default: Any
            Значение по умолчанию, если ключ не найден.

        Returns
        -------
        Any
            Значение конфигурации или default.
        """
        if self._config is None:
            self.load()
        return self._config.get(key, default) if self._config else default

    def set(self, key: str, value: Any) -> None:
        """
        Установить значение в конфигурации.

        Parameters
        ----------
        key: str
            Ключ конфигурации.
        value: Any
            Значение для установки.
        """
        if self._config is None:
            self.load()
        if self._config is not None:
            self._config[key] = value

    def update_chat_state(
        self, chat_id: int, last_read_message_id: int, ids_to_retry: List[int]
    ) -> None:
        """
        Обновить состояние чата в конфигурации.

        Parameters
        ----------
        chat_id: int
            ID чата.
        last_read_message_id: int
            ID последнего прочитанного сообщения.
        ids_to_retry: List[int]
            Список ID сообщений для повторной попытки загрузки.
        """
        if self._config is None:
            self.load()

        # Поддержка новой структуры с несколькими чатами
        if "chats" in self._config and isinstance(self._config["chats"], list):
            for chat in self._config["chats"]:
                if chat.get("chat_id") == chat_id:
                    chat["last_read_message_id"] = last_read_message_id
                    chat["ids_to_retry"] = ids_to_retry
                    return
            # Если чат не найден, добавить его
            self._config["chats"].append(
                {
                    "chat_id": chat_id,
                    "last_read_message_id": last_read_message_id,
                    "ids_to_retry": ids_to_retry,
                    "enabled": True,
                }
            )
        else:
            # Старая структура с одним чатом
            self._config["last_read_message_id"] = last_read_message_id
            self._config["ids_to_retry"] = ids_to_retry

    def set_selected_chats(
        self,
        selected_chats: Sequence[Tuple[int, str]],
    ) -> None:
        """
        Сохранить список выбранных чатов в конфигурации с возможностью редактирования.

        Логика:
        - Все существующие записи в `config["chats"]` помечаются как `enabled=False`
        - Для выбранных чатов создаются/обновляются записи и ставится `enabled=True`
        - Для уже известных чатов сохраняются `last_read_message_id` и `ids_to_retry`

        Parameters
        ----------
        selected_chats: Sequence[Tuple[int, str]]
            Список (chat_id, title) выбранных чатов.
        """
        if self._config is None:
            self.load()
        if self._config is None:
            raise ValueError("Конфигурация не загружена")

        if "chats" not in self._config or not isinstance(self._config.get("chats"), list):
            self._config["chats"] = []

        chats_list: List[Dict[str, Any]] = self._config["chats"]
        by_id: Dict[int, Dict[str, Any]] = {
            c.get("chat_id"): c for c in chats_list if isinstance(c, dict) and "chat_id" in c
        }

        # По умолчанию выключить все
        for chat in chats_list:
            if isinstance(chat, dict):
                chat["enabled"] = False

        for order_idx, (chat_id, title) in enumerate(selected_chats):
            existing = by_id.get(chat_id)
            if existing is None:
                existing = {
                    "chat_id": chat_id,
                    "title": title,
                    "last_read_message_id": 0,
                    "ids_to_retry": [],
                }
                chats_list.append(existing)
                by_id[chat_id] = existing

            # Обновить title, если получили непустой
            if isinstance(title, str) and title.strip():
                existing["title"] = title

            if "last_read_message_id" not in existing:
                existing["last_read_message_id"] = 0
            if "ids_to_retry" not in existing:
                existing["ids_to_retry"] = []

            existing["enabled"] = True
            # Порядок очереди загрузки (0-based)
            existing["order"] = order_idx

    def add_chat_to_download_list(self, chat_id: int, chat_title: Optional[str] = None) -> bool:
        """
        Добавить чат в список загрузок, если его там ещё нет.

        Parameters
        ----------
        chat_id: int
            ID чата для добавления.
        chat_title: Optional[str]
            Название чата (если известно).

        Returns
        -------
        bool
            True, если чат был добавлен, False если уже был в списке.
        """
        if self._config is None:
            self.load()
        if self._config is None:
            raise ValueError("Конфигурация не загружена")

        if "chats" not in self._config or not isinstance(self._config.get("chats"), list):
            self._config["chats"] = []

        chats_list: List[Dict[str, Any]] = self._config["chats"]
        
        # Проверить, есть ли уже этот чат в списке
        for chat in chats_list:
            if isinstance(chat, dict) and chat.get("chat_id") == chat_id:
                # Чат уже есть, обновить title если нужно
                if chat_title and isinstance(chat_title, str) and chat_title.strip():
                    chat["title"] = chat_title
                return False

        # Чат не найден, добавить его
        new_chat = {
            "chat_id": chat_id,
            "title": chat_title or f"Chat {chat_id}",
            "last_read_message_id": 0,
            "ids_to_retry": [],
            "enabled": False,  # По умолчанию выключен, пользователь включит при следующем запуске
        }
        
        # Определить порядок (последний в очереди)
        max_order = -1
        for chat in chats_list:
            if isinstance(chat, dict) and "order" in chat:
                try:
                    order = int(chat.get("order", -1))
                    if order > max_order:
                        max_order = order
                except (ValueError, TypeError):
                    pass
        
        new_chat["order"] = max_order + 1
        chats_list.append(new_chat)
        
        return True

    @property
    def config(self) -> Dict[str, Any]:
        """
        Получить текущую конфигурацию.

        Returns
        -------
        Dict[str, Any]
            Текущая конфигурация.
        """
        if self._config is None:
            self.load()
        return self._config or {}
