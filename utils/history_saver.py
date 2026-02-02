"""Стратегии сохранения истории сообщений."""
import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from jinja2 import Environment, FileSystemLoader
from telethon.tl.types import Message, MessageMediaDocument, MessageMediaPhoto

from utils.datetime_helpers import dt_sort_ts, max_dt, parse_iso_dt
from utils.html_formatter import HtmlFormatter
from utils.media_utils import get_media_type as get_media_type_util
from utils.validation import validate_archive_file

logger = logging.getLogger(__name__)


def _archive_chat_id_for_path(chat_id: int) -> int:
    """ID чата для путей архива: приоритет без минуса (abs)."""
    return abs(chat_id)


class HistorySaver(ABC):
    """Абстрактный базовый класс для стратегий сохранения истории."""

    def __init__(
        self,
        history_path: str,
        config_manager: Optional[Any] = None,
        found_chat_ids: Optional[Set[int]] = None,
    ):
        """
        Инициализация стратегии сохранения.

        Parameters
        ----------
        history_path: str
            Путь к директории для сохранения истории.
        config_manager: Optional[Any]
            Менеджер конфигурации.
        found_chat_ids: Optional[Set[int]]
            Множество найденных chat_id из ссылок.
        """
        self.history_path = history_path
        self.config_manager = config_manager
        self.found_chat_ids = found_chat_ids or set()

    @abstractmethod
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
        pass

    @abstractmethod
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
        pass

    # Вспомогательные методы (общие для всех стратегий)

    @staticmethod
    def _get_media_type(message: Message) -> str:
        """Wrapper для get_media_type с возвратом 'None' вместо None."""
        result = get_media_type_util(message)
        return result if result else "None"


    def _extract_media_info(self, message: Message) -> Dict[str, Any]:
        """
        Извлечь детальную информацию о медиа.

        Parameters
        ----------
        message: Message
            Сообщение.

        Returns
        -------
        Dict[str, Any]
            Словарь с информацией о медиа.
        """
        media_info: Dict[str, Any] = {
            "media_type": self._get_media_type(message)
        }

        if isinstance(message.media, MessageMediaPhoto):
            photo = message.media.photo
            if photo:
                media_info["photo_id"] = photo.id
                photo_size = self._get_photo_file_size(photo)
                if photo_size is not None:
                    media_info["file_size"] = photo_size

        elif isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            if doc:
                media_info["document_id"] = doc.id
                media_info["file_size"] = doc.size
                media_info["mime_type"] = doc.mime_type

                # Извлечь имя файла и другие атрибуты
                for attr in doc.attributes:
                    if hasattr(attr, "file_name"):
                        media_info["file_name"] = attr.file_name
                    if hasattr(attr, "duration"):
                        media_info["duration"] = attr.duration
                    if hasattr(attr, "w") and hasattr(attr, "h"):
                        media_info["width"] = attr.w
                        media_info["height"] = attr.h

        return media_info

    @staticmethod
    def _get_photo_file_size(photo: Any) -> Optional[int]:
        """
        Попробовать получить размер фото (в байтах) из Telethon объекта.

        У фото размер часто доступен только на уровне `sizes[*].size` или `len(sizes[*].bytes)`.
        Возвращаем максимальный известный размер или None.
        """
        sizes = getattr(photo, "sizes", None)
        if not sizes:
            return None

        max_size = 0
        for s in sizes:
            s_size = getattr(s, "size", None)
            if isinstance(s_size, int) and s_size > max_size:
                max_size = s_size
                continue

            s_bytes = getattr(s, "bytes", None)
            if isinstance(s_bytes, (bytes, bytearray)):
                max_size = max(max_size, len(s_bytes))

        return max_size if max_size > 0 else None




class JsonHistorySaver(HistorySaver):
    """Стратегия сохранения истории в JSON (JSONL) формате."""

    def save_message(
        self,
        message: Message,
        chat_id: int,
        chat_title: Optional[str] = None,
        downloaded_file_path: Optional[str] = None,
    ) -> None:
        """Сохранить сообщение в JSONL формате."""
        chat_file = os.path.join(
            self.history_path, f"chat_{_archive_chat_id_for_path(chat_id)}.jsonl"
        )
        message_data: Dict[str, Any] = {
            "id": message.id,
            "date": message.date.isoformat() if message.date else None,
            "text": message.message or "",
            "sender_id": message.sender_id,
            "chat_id": chat_id,
            "chat_title": chat_title,
            "has_media": bool(message.media),
            "views": getattr(message, "views", None),
            "forwards": getattr(message, "forwards", None),
            "reply_to_msg_id": message.reply_to_msg_id if message.reply_to else None,
            "edit_date": message.edit_date.isoformat() if message.edit_date else None,
        }

        # Добавить информацию о медиа, если есть
        if message.media:
            media_info = self._extract_media_info(message)
            message_data.update(media_info)

        # Добавить путь к скачанному файлу
        if downloaded_file_path:
            message_data["downloaded_file"] = downloaded_file_path

        with open(chat_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(message_data, ensure_ascii=False) + "\n")

    def save_batch(
        self,
        messages: List[Message],
        chat_id: int,
        chat_title: Optional[str] = None,
        downloaded_files: Optional[Dict[int, str]] = None,
    ) -> None:
        """Сохранить пакет сообщений в JSONL формате."""
        downloaded_files = downloaded_files or {}
        path_id = _archive_chat_id_for_path(chat_id)
        archive_path = os.path.join(self.history_path, f"chat_{path_id}.jsonl")

        # Проверка дублей
        message_ids = [msg.id for msg in messages]
        if self._check_archive_duplicates(archive_path, message_ids):
            logger.info(
                "Архив чата уже содержит все сообщения: chat_id=%s, path=%s, сообщений=%s (пропуск сохранения)",
                chat_id,
                archive_path,
                len(messages),
            )
            return

        logger.info(
            "Сохранение архива чата: chat_id=%s, path=%s, сообщений=%s",
            chat_id,
            archive_path,
            len(messages),
        )
        for message in messages:
            file_path = downloaded_files.get(message.id)
            self.save_message(message, chat_id, chat_title, file_path)
        logger.info(
            "Архив чата сохранён: chat_id=%s, path=%s",
            chat_id,
            archive_path,
        )

    def _check_archive_duplicates(
        self, archive_path: str, message_ids: List[int]
    ) -> bool:
        """
        Проверить, есть ли все сообщения уже в архиве (проверка дублей).

        Returns True, если все сообщения уже есть в архиве.
        """
        if not os.path.exists(archive_path):
            return False

        if not validate_archive_file(archive_path, "jsonl"):
            return False

        if not message_ids:
            return True

        existing_ids: set = set()
        try:
            with open(archive_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict) and "id" in obj:
                            existing_ids.add(obj["id"])
                    except Exception:
                        continue
        except Exception:
            return False

        return all(msg_id in existing_ids for msg_id in message_ids)


class TxtHistorySaver(HistorySaver):
    """Стратегия сохранения истории в текстовом формате."""

    def save_message(
        self,
        message: Message,
        chat_id: int,
        chat_title: Optional[str] = None,
        downloaded_file_path: Optional[str] = None,
    ) -> None:
        """Сохранить сообщение в текстовом формате."""
        chat_file = os.path.join(
            self.history_path, f"chat_{_archive_chat_id_for_path(chat_id)}.txt"
        )
        date_str = message.date.strftime("%Y-%m-%d %H-%M-%S") if message.date else "Unknown"
        text = message.message or "[Без текста]"
        media_info = ""

        if message.media:
            media_type = self._get_media_type(message)
            media_details = self._extract_media_info(message)
            media_info = f" [Медиа: {media_type}"
            if media_details.get("file_name"):
                media_info += f", файл: {media_details['file_name']}"
            media_info += "]"

        file_info = ""
        if downloaded_file_path:
            file_info = f"\n  Скачано: {downloaded_file_path}"

        with open(chat_file, "a", encoding="utf-8") as f:
            f.write(f"[{date_str}] ID:{message.id} {text}{media_info}{file_info}\n")

    def save_batch(
        self,
        messages: List[Message],
        chat_id: int,
        chat_title: Optional[str] = None,
        downloaded_files: Optional[Dict[int, str]] = None,
    ) -> None:
        """Сохранить пакет сообщений в текстовом формате."""
        downloaded_files = downloaded_files or {}
        path_id = _archive_chat_id_for_path(chat_id)
        archive_path = os.path.join(self.history_path, f"chat_{path_id}.txt")

        logger.info(
            "Сохранение архива чата: chat_id=%s, path=%s, сообщений=%s",
            chat_id,
            archive_path,
            len(messages),
        )
        for message in messages:
            file_path = downloaded_files.get(message.id)
            self.save_message(message, chat_id, chat_title, file_path)
        logger.info(
            "Архив чата сохранён: chat_id=%s, path=%s",
            chat_id,
            archive_path,
        )


class HtmlHistorySaver(HistorySaver):
    """Стратегия сохранения истории с генерацией HTML."""

    def __init__(
        self,
        history_path: str,
        config_manager: Optional[Any] = None,
        found_chat_ids: Optional[Set[int]] = None,
        template_env: Optional[Environment] = None,
        index_manifest_file: Optional[str] = None,
    ):
        """
        Инициализация стратегии HTML сохранения.

        Parameters
        ----------
        history_path: str
            Путь к директории для сохранения истории.
        config_manager: Optional[Any]
            Менеджер конфигурации.
        found_chat_ids: Optional[Set[int]]
            Множество найденных chat_id из ссылок.
        template_env: Optional[Environment]
            Jinja2 окружение для рендеринга шаблонов.
        index_manifest_file: Optional[str]
            Путь к файлу манифеста индекса.
        """
        super().__init__(history_path, config_manager, found_chat_ids)
        self.template_env = template_env
        self.index_manifest_file = index_manifest_file or os.path.join(
            history_path, "index.json"
        )
        self.html_formatter = HtmlFormatter(self.found_chat_ids)
        self.chats_info: Dict[int, Dict[str, Any]] = {}

    def save_message(
        self,
        message: Message,
        chat_id: int,
        chat_title: Optional[str] = None,
        downloaded_file_path: Optional[str] = None,
    ) -> None:
        """Сохранить сообщение в JSONL (для последующей генерации HTML)."""
        # Обновить информацию о чате
        if chat_id not in self.chats_info:
            self.chats_info[chat_id] = {
                "title": chat_title or f"Chat {chat_id}",
                "message_count": 0,
                "last_message_date": None,
            }

        self.chats_info[chat_id]["message_count"] += 1
        if message.date:
            self.chats_info[chat_id]["last_message_date"] = message.date

        # Сохраняем в JSONL для последующей генерации HTML
        chat_file = os.path.join(
            self.history_path, f"chat_{_archive_chat_id_for_path(chat_id)}.jsonl"
        )
        message_data: Dict[str, Any] = {
            "id": message.id,
            "date": message.date.isoformat() if message.date else None,
            "text": message.message or "",
            "sender_id": message.sender_id,
            "chat_id": chat_id,
            "chat_title": chat_title,
            "has_media": bool(message.media),
            "views": getattr(message, "views", None),
            "forwards": getattr(message, "forwards", None),
            "reply_to_msg_id": message.reply_to_msg_id if message.reply_to else None,
            "edit_date": message.edit_date.isoformat() if message.edit_date else None,
        }

        # Сохранить entities (форматирование текста, ссылки)
        if hasattr(message, "entities") and message.entities:
            entities_data = []
            for entity in message.entities:
                entity_dict = {
                    "offset": entity.offset,
                    "length": entity.length,
                    "type": type(entity).__name__,
                }

                if hasattr(entity, "url"):
                    entity_dict["url"] = entity.url

                if hasattr(entity, "user_id"):
                    entity_dict["user_id"] = entity.user_id

                entities_data.append(entity_dict)
            message_data["entities"] = entities_data

        if message.media:
            media_info = self._extract_media_info(message)
            message_data.update(media_info)

        if downloaded_file_path:
            message_data["downloaded_file"] = downloaded_file_path

        with open(chat_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(message_data, ensure_ascii=False) + "\n")

    def save_batch(
        self,
        messages: List[Message],
        chat_id: int,
        chat_title: Optional[str] = None,
        downloaded_files: Optional[Dict[int, str]] = None,
        # Параметры для интеграции с MessageHistory
        load_index_manifest_fn: Optional[Any] = None,
        save_index_manifest_fn: Optional[Any] = None,
        list_chat_ids_from_jsonl_fn: Optional[Any] = None,
        try_get_chat_meta_from_jsonl_fn: Optional[Any] = None,
    ) -> None:
        """Сохранить пакет сообщений и сгенерировать HTML."""
        downloaded_files = downloaded_files or {}
        path_id = _archive_chat_id_for_path(chat_id)
        archive_path = os.path.join(self.history_path, f"chat_{path_id}.jsonl")

        # Проверка дублей
        message_ids = [msg.id for msg in messages]
        if self._check_archive_duplicates(archive_path, message_ids):
            logger.info(
                "Архив чата уже содержит все сообщения: chat_id=%s, path=%s, сообщений=%s (пропуск сохранения)",
                chat_id,
                archive_path,
                len(messages),
            )
            # Всё равно обновить индекс HTML
            self._generate_index_html(
                load_index_manifest_fn,
                save_index_manifest_fn,
                list_chat_ids_from_jsonl_fn,
                try_get_chat_meta_from_jsonl_fn,
            )
            return

        logger.info(
            "Сохранение архива чата: chat_id=%s, path=%s, сообщений=%s",
            chat_id,
            archive_path,
            len(messages),
        )
        for message in messages:
            file_path = downloaded_files.get(message.id)
            self.save_message(message, chat_id, chat_title, file_path)
        logger.info(
            "Архив чата сохранён: chat_id=%s, path=%s",
            chat_id,
            archive_path,
        )

        # Генерация HTML
        self._generate_index_html(
            load_index_manifest_fn,
            save_index_manifest_fn,
            list_chat_ids_from_jsonl_fn,
            try_get_chat_meta_from_jsonl_fn,
        )

    def _check_archive_duplicates(
        self, archive_path: str, message_ids: List[int]
    ) -> bool:
        """Проверить, есть ли все сообщения уже в архиве."""
        if not os.path.exists(archive_path):
            return False

        if not validate_archive_file(archive_path, "jsonl"):
            return False

        if not message_ids:
            return True

        existing_ids: set = set()
        try:
            with open(archive_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict) and "id" in obj:
                            existing_ids.add(obj["id"])
                    except Exception:
                        continue
        except Exception:
            return False

        return all(msg_id in existing_ids for msg_id in message_ids)

    def _generate_chat_html(self, chat_id: int) -> None:
        """Сгенерировать HTML файл для конкретного чата."""
        if not self.template_env:
            return

        path_id = _archive_chat_id_for_path(chat_id)
        jsonl_file = os.path.join(self.history_path, f"chat_{path_id}.jsonl")
        if not os.path.exists(jsonl_file):
            return

        messages: List[Dict[str, Any]] = []
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    messages.append(obj)

        if not messages:
            return

        chat_title = messages[0].get("chat_title") or f"Чат {chat_id}"
        html_file = os.path.join(self.history_path, f"chat_{path_id}.html")

        # Generate HTML for messages
        messages_html = ""
        for msg in messages:
            messages_html += self.html_formatter.format_message(msg)

        html_content = self.template_env.get_template("chat_history.html").render(
            chat_title=chat_title,
            messages_html=messages_html,
            message_count=len(messages),
        )

        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)

    def _generate_index_html(
        self,
        load_index_manifest_fn: Optional[Any] = None,
        save_index_manifest_fn: Optional[Any] = None,
        list_chat_ids_from_jsonl_fn: Optional[Any] = None,
        try_get_chat_meta_from_jsonl_fn: Optional[Any] = None,
    ) -> None:
        """Сгенерировать индексный HTML файл со списком всех чатов."""
        if not self.template_env:
            return

        # Если функции не переданы, используем заглушки
        # (это для обратной совместимости, реальная логика будет в MessageHistory)
        if not all([
            load_index_manifest_fn,
            save_index_manifest_fn,
            list_chat_ids_from_jsonl_fn,
            try_get_chat_meta_from_jsonl_fn,
        ]):
            logger.warning(
                "HtmlHistorySaver: отсутствуют функции для генерации индекса, пропуск"
            )
            return

        # Генерация HTML для чатов текущего запуска
        for chat_id in self.chats_info.keys():
            self._generate_chat_html(chat_id)

        # Загрузка и обновление манифеста через переданные функции
        manifest = load_index_manifest_fn()

        # Обновление манифеста чатами из текущего запуска
        for chat_id, info in self.chats_info.items():
            seeded = try_get_chat_meta_from_jsonl_fn(chat_id)
            if seeded is not None:
                title, message_count, last_message_date = seeded
                if chat_id in manifest:
                    manifest[chat_id]["message_count"] = message_count
                    if title:
                        manifest[chat_id]["title"] = title
                    if last_message_date:
                        old_last = self._parse_iso_dt(manifest[chat_id].get("last_message_date"))
                        last = self._max_dt(old_last, last_message_date)
                        manifest[chat_id]["last_message_date"] = last.isoformat() if last else None
                else:
                    manifest[chat_id] = {
                        "title": title,
                        "message_count": message_count,
                        "last_message_date": last_message_date.isoformat() if last_message_date else None,
                    }

        # Подтянуть чаты из архива
        manifest_path_ids = {abs(cid): cid for cid in manifest.keys()}

        for chat_id in list_chat_ids_from_jsonl_fn():
            path_id = abs(chat_id)
            if path_id in manifest_path_ids:
                existing_chat_id = manifest_path_ids[path_id]
                if existing_chat_id != chat_id:
                    if existing_chat_id in manifest:
                        manifest[chat_id] = manifest.pop(existing_chat_id)
                        manifest_path_ids[path_id] = chat_id

                meta = try_get_chat_meta_from_jsonl_fn(chat_id)
                if meta is not None:
                    title, message_count, last_message_date = meta
                    manifest[chat_id]["message_count"] = message_count
                    if title:
                        manifest[chat_id]["title"] = title
                    if last_message_date:
                        old_last = self._parse_iso_dt(manifest[chat_id].get("last_message_date"))
                        last = self._max_dt(old_last, last_message_date)
                        manifest[chat_id]["last_message_date"] = last.isoformat() if last else None
                continue

            meta = try_get_chat_meta_from_jsonl_fn(chat_id)
            if meta is None:
                continue
            title, message_count, last_message_date = meta
            manifest[chat_id] = {
                "title": title,
                "message_count": message_count,
                "last_message_date": last_message_date.isoformat() if last_message_date else None,
            }
            manifest_path_ids[path_id] = chat_id

        # Сохранить манифест
        save_index_manifest_fn(manifest)

        # Построить index.html
        index_file = os.path.join(self.history_path, "index.html")

        items: List[Tuple[int, Dict[str, Any]]] = list(manifest.items())
        items.sort(
            key=lambda x: self._dt_sort_ts(self._parse_iso_dt(x[1].get("last_message_date"))),
            reverse=True,
        )

        chats_list = []
        for chat_id, info in items:
            title = str(info.get("title") or f"Chat {chat_id}")
            count = int(info.get("message_count") or 0)
            last_date = self._parse_iso_dt(info.get("last_message_date"))
            date_str = last_date.strftime("%d.%m.%Y %H:%M") if last_date else "Неизвестно"

            first_letter = title[0].upper() if title else "?"

            path_id = _archive_chat_id_for_path(chat_id)
            chat_href = f"chat_{path_id}.html"
            chat_html_path = os.path.join(self.history_path, chat_href)
            has_html = os.path.exists(chat_html_path)

            chats_list.append({
                "title": title,
                "count": count,
                "date_str": date_str,
                "avatar_letter": first_letter,
                "href": chat_href,
                "has_html": has_html,
            })

        html_content = self.template_env.get_template("index.html").render(chats=chats_list)

        with open(index_file, "w", encoding="utf-8") as f:
            f.write(html_content)

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
