"""Модуль для сохранения истории сообщений."""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from jinja2 import Environment, FileSystemLoader
from telethon.tl.types import Message

from utils.datetime_helpers import dt_sort_ts, max_dt, parse_iso_dt
from utils.history_saver import (
    HtmlHistorySaver,
    HistorySaver,
    JsonHistorySaver,
    TxtHistorySaver,
    _archive_chat_id_for_path,
)

logger = logging.getLogger(__name__)


class MessageHistory:
    """Фасад для сохранения истории сообщений."""

    def __init__(
        self,
        base_directory: str,
        history_format: str = "json",
        history_directory: str = "history",
        config_manager: Optional[Any] = None,
    ):
        """
        Инициализация MessageHistory.

        Parameters
        ----------
        base_directory: str
            Базовая директория для сохранения истории.
        history_format: str
            Формат сохранения ('json', 'txt' или 'html').
        history_directory: str
            Имя директории для истории внутри базовой директории.
        config_manager: Optional[Any]
            Менеджер конфигурации для добавления чатов из ссылок.
        """
        self.base_directory = base_directory
        self.history_format = history_format.lower()
        self.history_directory = history_directory
        self.history_path = os.path.join(base_directory, history_directory)
        os.makedirs(self.history_path, exist_ok=True)
        self.chats_info: Dict[int, Dict[str, Any]] = {}
        self._index_manifest_file = os.path.join(self.history_path, "index.json")
        self.config_manager = config_manager
        self._found_chat_ids: Set[int] = set()

        # Initialize Jinja2
        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
        )
        self.template_env = Environment(loader=FileSystemLoader(templates_dir))

        # Создать стратегию сохранения
        self.saver = self._create_saver()

    def _create_saver(self) -> HistorySaver:
        """Создать стратегию сохранения на основе формата."""
        if self.history_format in ("json", "jsonl"):
            return JsonHistorySaver(
                self.history_path, self.config_manager, self._found_chat_ids
            )
        elif self.history_format == "html":
            return HtmlHistorySaver(
                self.history_path,
                self.config_manager,
                self._found_chat_ids,
                self.template_env,
                self._index_manifest_file,
            )
        elif self.history_format == "txt":
            return TxtHistorySaver(
                self.history_path, self.config_manager, self._found_chat_ids
            )
        else:
            raise ValueError(f"Неподдерживаемый формат: {self.history_format}")

    def save_message(
        self,
        message: Message,
        chat_id: int,
        chat_title: Optional[str] = None,
        downloaded_file_path: Optional[str] = None,
    ) -> None:
        """
        Сохранить одно сообщение.

        Parameters
        ----------
        message: Message
            Сообщение для сохранения.
        chat_id: int
            ID чата.
        chat_title: Optional[str]
            Название чата.
        downloaded_file_path: Optional[str]
            Путь к скачанному файлу (если был скачан).
        """
        # Обновить информацию о чате (для HTML индекса)
        if chat_id not in self.chats_info:
            self.chats_info[chat_id] = {
                "title": chat_title or f"Chat {chat_id}",
                "message_count": 0,
                "last_message_date": None,
            }

        self.chats_info[chat_id]["message_count"] += 1
        if message.date:
            self.chats_info[chat_id]["last_message_date"] = message.date

        # Делегировать сохранение стратегии
        self.saver.save_message(message, chat_id, chat_title, downloaded_file_path)

    def save_batch(
        self,
        messages: List[Message],
        chat_id: int,
        chat_title: Optional[str] = None,
        downloaded_files: Optional[Dict[int, str]] = None,
    ) -> None:
        """
        Сохранить пакет сообщений.

        Parameters
        ----------
        messages: List[Message]
            Список сообщений для сохранения.
        chat_id: int
            ID чата.
        chat_title: Optional[str]
            Название чата.
        downloaded_files: Optional[Dict[int, str]]
            Словарь {message_id: file_path} для скачанных файлов.
        """
        # Для HTML стратегии нужны дополнительные функции
        if isinstance(self.saver, HtmlHistorySaver):
            # Передать chats_info в стратегию
            self.saver.chats_info = self.chats_info

            # Делегировать с передачей функций для работы с манифестом
            self.saver.save_batch(
                messages,
                chat_id,
                chat_title,
                downloaded_files,
                load_index_manifest_fn=self._load_index_manifest,
                save_index_manifest_fn=self._save_index_manifest,
                list_chat_ids_from_jsonl_fn=self._list_chat_ids_from_jsonl,
                try_get_chat_meta_from_jsonl_fn=self._try_get_chat_meta_from_jsonl,
            )
        else:
            # Для JSON и TXT стратегий просто делегируем
            self.saver.save_batch(messages, chat_id, chat_title, downloaded_files)

        # Добавить найденные чаты из ссылок в список загрузок
        self._add_found_chats_to_config()

    def _add_found_chats_to_config(self) -> None:
        """Добавить найденные чаты из ссылок в конфигурацию для дальнейшей загрузки."""
        if not self.config_manager or not self._found_chat_ids:
            return

        # Проверить, включена ли опция автоматического добавления
        config = self.config_manager.config
        download_settings = config.get("download_settings", {})
        if not download_settings.get("auto_add_chats_from_links", False):
            return

        added_count = 0
        for found_chat_id in self._found_chat_ids:
            try:
                was_added = self.config_manager.add_chat_to_download_list(found_chat_id)
                if was_added:
                    added_count += 1
                    logger.info(
                        "Добавлен чат из ссылки в список загрузок: chat_id=%s",
                        found_chat_id,
                    )
            except Exception as e:
                logger.warning(
                    "Ошибка при добавлении чата %s в список загрузок: %s",
                    found_chat_id,
                    e,
                )

        if added_count > 0:
            try:
                self.config_manager.save()
                logger.info(
                    "Сохранено %s новых чатов в конфигурацию для дальнейшей загрузки",
                    added_count,
                )
            except Exception as e:
                logger.warning("Ошибка при сохранении конфигурации: %s", e)

        # Очистить множество найденных chat_id после обработки
        self._found_chat_ids.clear()

    # Методы для работы с манифестом индекса (используются HTML стратегией)

    def _load_index_manifest(self) -> Dict[int, Dict[str, Any]]:
        """
        Загрузить манифест индекса (index.json) из истории.

        Удаляет дубли чатов с одинаковым path_id (abs(chat_id)), оставляя один вариант.
        """
        if not os.path.exists(self._index_manifest_file):
            return {}
        try:
            with open(self._index_manifest_file, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
        except Exception:
            return {}

        manifest: Dict[int, Dict[str, Any]] = {}
        path_id_to_chat_id: Dict[int, int] = {}

        for k, v in raw.items():
            try:
                chat_id = int(k)
            except Exception:
                continue
            if not isinstance(v, dict):
                continue

            path_id = abs(chat_id)

            if path_id in path_id_to_chat_id:
                existing_chat_id = path_id_to_chat_id[path_id]
                existing_info = manifest[existing_chat_id]
                new_info = v

                # Объединить данные
                existing_count = int(existing_info.get("message_count") or 0)
                new_count = int(new_info.get("message_count") or 0)
                merged_count = max(existing_count, new_count)

                existing_date = self._parse_iso_dt(existing_info.get("last_message_date"))
                new_date = self._parse_iso_dt(new_info.get("last_message_date"))
                merged_date = self._max_dt(existing_date, new_date)

                merged_title = new_info.get("title") or existing_info.get("title") or f"Chat {chat_id}"

                manifest[existing_chat_id] = {
                    "title": merged_title,
                    "message_count": merged_count,
                    "last_message_date": merged_date.isoformat() if merged_date else None,
                }

                if chat_id < 0 or existing_chat_id > 0:
                    if chat_id < 0:
                        old_chat_id = existing_chat_id
                        manifest[chat_id] = manifest.pop(old_chat_id)
                        path_id_to_chat_id[path_id] = chat_id
            else:
                manifest[chat_id] = v
                path_id_to_chat_id[path_id] = chat_id

        return manifest

    def _save_index_manifest(self, manifest: Dict[int, Dict[str, Any]]) -> None:
        """Сохранить манифест индекса (index.json) в истории."""
        raw: Dict[str, Dict[str, Any]] = {str(chat_id): info for chat_id, info in manifest.items()}
        try:
            with open(self._index_manifest_file, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _list_chat_ids_from_jsonl(self) -> List[int]:
        """
        Вернуть список chat_id, найденных в истории по chat_*.jsonl.

        Возвращает нормализованные chat_id (с правильным знаком) из JSONL файлов.
        """
        chat_ids: List[int] = []
        seen_path_ids: Set[int] = set()

        try:
            for name in os.listdir(self.history_path):
                if not (name.startswith("chat_") and name.endswith(".jsonl")):
                    continue
                middle = name[len("chat_") : -len(".jsonl")]
                try:
                    path_id = int(middle)
                    if path_id in seen_path_ids:
                        continue
                    seen_path_ids.add(path_id)

                    jsonl_path = os.path.join(self.history_path, name)
                    real_chat_id = self._extract_chat_id_from_jsonl(jsonl_path)
                    if real_chat_id is not None:
                        chat_ids.append(real_chat_id)
                    else:
                        chat_ids.append(path_id)
                except Exception:
                    continue
        except Exception:
            return []
        return chat_ids

    def _extract_chat_id_from_jsonl(self, jsonl_path: str) -> Optional[int]:
        """Извлечь реальный chat_id (с правильным знаком) из JSONL файла."""
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict) and "chat_id" in obj:
                            chat_id = obj["chat_id"]
                            if isinstance(chat_id, int):
                                return chat_id
                    except Exception:
                        continue
        except Exception:
            pass
        return None

    def _try_get_chat_meta_from_jsonl(
        self, chat_id: int
    ) -> Optional[Tuple[str, int, Optional[datetime]]]:
        """
        Попытаться получить метаданные чата из chat_{chat_id}.jsonl.

        Возвращает (title, message_count, last_message_date).
        """
        jsonl_path = os.path.join(
            self.history_path, f"chat_{_archive_chat_id_for_path(chat_id)}.jsonl"
        )
        if not os.path.exists(jsonl_path):
            return None

        title: str = f"Chat {chat_id}"
        message_count = 0
        first_line: Optional[str] = None
        last_line: Optional[str] = None

        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if first_line is None:
                        first_line = line
                    last_line = line
                    message_count += 1
        except Exception:
            return None

        def _safe_parse_title(line: Optional[str]) -> Optional[str]:
            if not line:
                return None
            try:
                obj = json.loads(line)
            except Exception:
                return None
            if isinstance(obj, dict):
                t = obj.get("chat_title") or obj.get("title")
                if isinstance(t, str) and t.strip():
                    return t.strip()
            return None

        parsed_title = _safe_parse_title(first_line) or _safe_parse_title(last_line)
        if parsed_title:
            title = parsed_title

        last_message_date: Optional[datetime] = None
        if last_line:
            try:
                obj = json.loads(last_line)
                if isinstance(obj, dict) and obj.get("date"):
                    last_message_date = datetime.fromisoformat(str(obj["date"]))
            except Exception:
                last_message_date = None

        return title, message_count, last_message_date

    @staticmethod
    def _parse_iso_dt(value: Any) -> Optional[datetime]:
        """Безопасно распарсить ISO datetime."""
        return parse_iso_dt(value)

    @staticmethod
    def _max_dt(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
        """max(a, b) для Optional[datetime]."""
        return max_dt(a, b)

    @staticmethod
    def _dt_sort_ts(dt: Optional[datetime]) -> float:
        """Стабильный sort-key для datetime."""
        return dt_sort_ts(dt)

    # Обратная совместимость: методы, которые могут использоваться тестами

    def _format_message_html(self, msg: Dict[str, Any]) -> str:
        """Обратная совместимость: форматировать сообщение в HTML (для тестов)."""
        if isinstance(self.saver, HtmlHistorySaver):
            return self.saver.html_formatter.format_message(msg)
        # Fallback
        from utils.html_formatter import HtmlFormatter
        formatter = HtmlFormatter(self._found_chat_ids)
        return formatter.format_message(msg)

    def _generate_chat_html(self, chat_id: int) -> None:
        """Обратная совместимость: сгенерировать HTML для чата (для тестов)."""
        if isinstance(self.saver, HtmlHistorySaver):
            self.saver._generate_chat_html(chat_id)

    def _generate_index_html(self) -> None:
        """Обратная совместимость: сгенерировать индексный HTML (для тестов)."""
        if isinstance(self.saver, HtmlHistorySaver):
            self.saver.chats_info = self.chats_info
            self.saver._generate_index_html(
                self._load_index_manifest,
                self._save_index_manifest,
                self._list_chat_ids_from_jsonl,
                self._try_get_chat_meta_from_jsonl,
            )