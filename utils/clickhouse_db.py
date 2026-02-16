import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime
import asyncio

try:
    from clickhouse_driver import Client
except ImportError:
    Client = None

logger = logging.getLogger(__name__)

# Буфер логов для пакетной записи в ClickHouse
_LOG_BUFFER: List[Dict[str, Any]] = []
_LOG_BUFFER_MAX = 100


class ClickHouseMetadataDB:
    """Класс для работы с ClickHouse в качестве хранилища метаданных."""

    def __init__(self, config: Dict[str, Any]):
        """
        Инициализация подключения к ClickHouse.

        Parameters
        ----------
        config: Dict[str, Any]
            Секция clickhouse из конфигурации.
        """
        self.enabled = config.get("enabled", False)
        if not self.enabled:
            return

        if Client is None:
            logger.error("Пакет 'clickhouse-driver' не установлен. ClickHouse будет отключен.")
            self.enabled = False
            return

        self.host = config.get("host", "localhost")
        self.port = config.get("port", 9000)
        self.user = config.get("user", "default")
        self.password = config.get("password", "")
        self.database = config.get("database", "telegram_downloader")
        self.batch_size = config.get("batch_size", 1000)

        self._client = None
        self._message_buffer = []

    def _get_client(self):
        """Создать или вернуть существующий клиент."""
        if self._client is None:
            self._client = Client(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database
            )
            self._init_db()
        return self._client

    def check_connection(self) -> None:
        """Проверить доступ к БД. При недоступности выбрасывает исключение."""
        if not self.enabled:
            return
        client = self._get_client()
        client.execute("SELECT 1")

    def _init_db(self):
        """Инициализация схемы БД."""
        client = self._client

        # Создание БД если её нет
        # clickhouse-driver не поддерживает CREATE DATABASE IF NOT EXISTS напрямую в конструкторе database
        # поэтому подключаемся к default сначала
        root_client = Client(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password
        )
        root_client.execute(f"CREATE DATABASE IF NOT EXISTS {self.database}")

        # Таблица сообщений
        client.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                chat_id Int64,
                message_id Int64,
                date DateTime,
                text String,
                media_type LowCardinality(String),
                file_path String,
                file_size UInt64,
                downloaded UInt8,
                download_date DateTime,
                sender_id Int64,
                chat_title String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMM(date)
            ORDER BY (chat_id, date, message_id)
        """)

        # Таблица чатов
        client.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id Int64,
                title String,
                last_sync DateTime,
                message_count UInt32,
                total_size UInt64
            ) ENGINE = ReplacingMergeTree()
            ORDER BY chat_id
        """)

        # Таблица логов приложения (поиск по сообщению и уровню)
        client.execute("""
            CREATE TABLE IF NOT EXISTS app_logs (
                ts DateTime,
                level LowCardinality(String),
                logger_name String,
                message String
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMM(ts)
            ORDER BY (ts, logger_name)
        """)

        # Таблица файлов загрузки: путь, статус (downloaded/failed/skipped)
        client.execute("""
            CREATE TABLE IF NOT EXISTS file_downloads (
                chat_id Int64,
                message_id Int64,
                chat_title String,
                file_name String,
                file_path String,
                status LowCardinality(String),
                file_size UInt64,
                error_message String,
                created_at DateTime
            ) ENGINE = ReplacingMergeTree(created_at)
            ORDER BY (chat_id, message_id)
        """)

        # Миграция: колонка file_hash для верификации целостности
        try:
            client.execute(
                "ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_hash String DEFAULT ''"
            )
            client.execute(
                "ALTER TABLE file_downloads ADD COLUMN IF NOT EXISTS file_hash String DEFAULT ''"
            )
        except Exception as e:
            logger.warning("Миграция file_hash (возможно уже применена): %s", e)

    async def save_message(self, data: Dict[str, Any]):
        """
        Добавить сообщение в буфер для пакетной вставки.

        Parameters
        ----------
        data: Dict[str, Any]
            Метаданные сообщения.
        """
        if not self.enabled:
            return

        self._message_buffer.append(data)
        if len(self._message_buffer) >= self.batch_size:
            await self.flush()

    async def flush(self):
        """Принудительно записать буфер в БД."""
        if not self.enabled or not self._message_buffer:
            return

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._insert_messages, self._message_buffer)
            self._message_buffer = []
        except Exception as e:
            logger.error(f"Ошибка при записи в ClickHouse: {e}")

    def _insert_messages(self, messages: List[Dict[str, Any]]):
        """Вставка сообщений (синхронно для executor)."""
        client = self._get_client()
        query = (
            "INSERT INTO messages (chat_id, message_id, date, text, media_type, "
            "file_path, file_size, downloaded, download_date, sender_id, chat_title, file_hash) VALUES"
        )
        def _str(v: Any) -> str:
            return "" if v is None else str(v)

        data = [
            (
                m["chat_id"],
                m["message_id"],
                m["date"],
                _str(m.get("text")),
                _str(m.get("media_type")),
                _str(m.get("file_path")),
                m.get("file_size", 0),
                1 if m.get("file_path") else 0,
                datetime.now(),
                m.get("sender_id", 0),
                _str(m.get("chat_title")),
                _str(m.get("file_hash", "")),
            )
            for m in messages
        ]
        client.execute(query, data)

    async def update_chat_info(self, chat_id: int, title: str, message_count: int, total_size: int = 0):
        """Обновить информацию о чате."""
        if not self.enabled:
            return

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._insert_chat,
                chat_id, title, message_count, total_size
            )
        except Exception as e:
            logger.error(f"Ошибка при обновлении информации о чате в ClickHouse: {e}")

    def _insert_chat(self, chat_id, title, message_count, total_size):
        client = self._get_client()
        query = "INSERT INTO chats (chat_id, title, last_sync, message_count, total_size) VALUES"
        client.execute(
            query,
            [(chat_id, "" if title is None else str(title), datetime.now(), message_count, total_size)],
        )

    def get_existing_message_ids(self, chat_id: int, message_ids: List[int]) -> Set[int]:
        """
        Вернуть множество message_id, которые уже есть в БД для чата.
        Используется для проверки дублей при primary_source.
        """
        if not self.enabled or not message_ids:
            return set()
        try:
            client = self._get_client()
            placeholders = ",".join(str(mid) for mid in message_ids)
            rows = client.execute(
                f"SELECT message_id FROM messages WHERE chat_id = %(chat_id)s AND message_id IN ({placeholders})",
                {"chat_id": chat_id},
            )
            return {r[0] for r in rows}
        except Exception as e:
            logger.warning("Ошибка чтения message_id из ClickHouse: %s", e)
            return set()

    def get_messages_for_chat(self, chat_id: int) -> List[Dict[str, Any]]:
        """
        Вернуть все сообщения чата в формате, совместимом с JSONL/HTML
        (id, date, text, downloaded_file, media_type, has_media, ...).
        """
        if not self.enabled:
            return []
        try:
            client = self._get_client()
            rows = client.execute(
                "SELECT chat_id, message_id, date, text, media_type, file_path, file_size, sender_id, chat_title "
                "FROM messages WHERE chat_id = %(chat_id)s ORDER BY date, message_id",
                {"chat_id": chat_id},
            )
            result: List[Dict[str, Any]] = []
            for row in rows:
                (r_chat_id, msg_id, date_val, text_val, media_type_val, file_path_val, file_size_val, sender_val, title_val) = row
                date_iso = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
                media_type_str = "" if media_type_val is None else str(media_type_val)
                has_media = bool(media_type_str and media_type_str != "None")
                result.append({
                    "id": msg_id,
                    "chat_id": r_chat_id,
                    "date": date_iso,
                    "text": "" if text_val is None else str(text_val),
                    "sender_id": sender_val or 0,
                    "chat_title": "" if title_val is None else str(title_val),
                    "has_media": has_media,
                    "media_type": media_type_str,
                    "file_size": file_size_val or 0,
                    "downloaded_file": (file_path_val or "").strip() or None,
                })
            return result
        except Exception as e:
            logger.warning("Ошибка чтения сообщений из ClickHouse: %s", e)
            return []

    def get_chats_manifest(
        self,
    ) -> List[Tuple[int, str, int, Optional[datetime]]]:
        """
        Список чатов из БД: (chat_id, title, message_count, last_message_date).
        Источник истины — таблица messages (агрегация по chat_id).
        """
        if not self.enabled:
            return []
        try:
            client = self._get_client()
            rows = client.execute("""
                SELECT chat_id, any(chat_title) AS title, count() AS cnt, max(date) AS last_date
                FROM messages
                GROUP BY chat_id
                ORDER BY last_date DESC
            """)
            out: List[Tuple[int, str, int, Optional[datetime]]] = []
            for row in rows:
                (cid, title, cnt, last_dt) = row
                out.append((cid, (title or "").strip() or f"Chat {cid}", cnt, last_dt))
            return out
        except Exception as e:
            logger.warning("Ошибка чтения манифеста чатов из ClickHouse: %s", e)
            return []

    def get_messages_page(
        self, chat_id: int, offset: int = 0, limit: int = 100
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Пагинированное получение сообщений чата.

        Parameters
        ----------
        chat_id : int
            ID чата.
        offset : int
            Смещение (по date, message_id).
        limit : int
            Максимум сообщений.

        Returns
        -------
        Tuple[List[Dict], int]
            (список сообщений, общее количество).
        """
        if not self.enabled:
            return [], 0
        offset = max(0, min(offset, 10_000_000))
        limit = max(1, min(limit, 500))
        try:
            client = self._get_client()
            total_rows = client.execute(
                "SELECT count() FROM messages WHERE chat_id = %(chat_id)s",
                {"chat_id": chat_id},
            )
            total = int(total_rows[0][0]) if total_rows else 0

            rows = client.execute(
                "SELECT chat_id, message_id, date, text, media_type, file_path, file_size, sender_id, chat_title "
                "FROM messages WHERE chat_id = %(chat_id)s ORDER BY date, message_id "
                f"LIMIT {limit} OFFSET {offset}",
                {"chat_id": chat_id},
            )
            result: List[Dict[str, Any]] = []
            for row in rows:
                (
                    r_chat_id,
                    msg_id,
                    date_val,
                    text_val,
                    media_type_val,
                    file_path_val,
                    file_size_val,
                    sender_val,
                    title_val,
                ) = row
                date_iso = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
                media_type_str = "" if media_type_val is None else str(media_type_val)
                has_media = bool(media_type_str and media_type_str != "None")
                result.append({
                    "id": msg_id,
                    "chat_id": r_chat_id,
                    "date": date_iso,
                    "text": "" if text_val is None else str(text_val),
                    "sender_id": sender_val or 0,
                    "chat_title": "" if title_val is None else str(title_val),
                    "has_media": has_media,
                    "media_type": media_type_str,
                    "file_size": file_size_val or 0,
                    "downloaded_file": (file_path_val or "").strip() or None,
                })
            return result, total
        except Exception as e:
            logger.warning("Ошибка чтения страницы сообщений из ClickHouse: %s", e)
            return [], 0

    def get_message_file_path(
        self, chat_id: int, message_id: int
    ) -> Optional[str]:
        """
        Получить путь к файлу сообщения по chat_id и message_id.
        Возвращает None, если запись не найдена или file_path пустой.
        """
        if not self.enabled:
            return None
        try:
            client = self._get_client()
            rows = client.execute(
                "SELECT file_path FROM messages WHERE chat_id = %(chat_id)s AND message_id = %(message_id)s LIMIT 1",
                {"chat_id": chat_id, "message_id": message_id},
            )
            if not rows:
                return None
            path = (rows[0][0] or "").strip()
            return path if path else None
        except Exception as e:
            logger.warning("Ошибка чтения file_path из ClickHouse: %s", e)
            return None

    def get_message_file_info(
        self, chat_id: int, message_id: int
    ) -> Optional[Tuple[str, int, str]]:
        """
        Получить (file_path, file_size, file_hash) для верификации «уже скачано».
        file_hash может быть пустой строкой (нет хеша — нужна полная валидация).
        """
        if not self.enabled:
            return None
        try:
            client = self._get_client()
            rows = client.execute(
                "SELECT file_path, file_size, file_hash FROM messages "
                "WHERE chat_id = %(chat_id)s AND message_id = %(message_id)s LIMIT 1",
                {"chat_id": chat_id, "message_id": message_id},
            )
            if not rows:
                return None
            row = rows[0]
            path = (row[0] or "").strip()
            if not path:
                return None
            size = int(row[1]) if row[1] is not None else 0
            fhash = (row[2] or "").strip() if len(row) > 2 else ""
            return (path, size, fhash)
        except Exception as e:
            logger.warning("Ошибка чтения file_info из ClickHouse: %s", e)
            return None

    def get_chat_meta(self, chat_id: int) -> Optional[Tuple[str, int, Optional[datetime]]]:
        """
        Метаданные одного чата: (title, message_count, last_message_date).
        """
        if not self.enabled:
            return None
        try:
            client = self._get_client()
            rows = client.execute(
                "SELECT any(chat_title) AS title, count() AS cnt, max(date) AS last_date "
                "FROM messages WHERE chat_id = %(chat_id)s GROUP BY chat_id",
                {"chat_id": chat_id},
            )
            if not rows:
                return None
            row = rows[0]
            title = (row[0] or "").strip() or f"Chat {chat_id}"
            return (title, row[1], row[2])
        except Exception as e:
            logger.warning("Ошибка чтения мета чата из ClickHouse: %s", e)
            return None

    # --- Логи приложения ---

    def save_log(self, level: str, message: str, logger_name: str = "") -> None:
        """
        Добавить запись лога в буфер (пакетная запись при flush_logs).
        """
        if not self.enabled:
            return
        global _LOG_BUFFER  # pylint: disable=global-statement
        _LOG_BUFFER.append({
            "ts": datetime.now(),
            "level": (level or "INFO").upper(),
            "logger_name": (logger_name or "").strip()[:255],
            "message": (message or "")[: 65535],
        })
        if len(_LOG_BUFFER) >= _LOG_BUFFER_MAX:
            self._flush_log_buffer()

    def _flush_log_buffer(self) -> None:
        """Записать буфер логов в БД."""
        global _LOG_BUFFER  # pylint: disable=global-statement
        if not _LOG_BUFFER:
            return
        try:
            client = self._get_client()
            data = [
                (r["ts"], r["level"], r["logger_name"], r["message"])
                for r in _LOG_BUFFER
            ]
            client.execute(
                "INSERT INTO app_logs (ts, level, logger_name, message) VALUES",
                data,
            )
            _LOG_BUFFER.clear()
        except Exception as e:
            logger.warning("Ошибка записи логов в ClickHouse: %s", e)

    async def flush_logs(self) -> None:
        """Принудительно записать буфер логов (async)."""
        if not self.enabled:
            return
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._flush_log_buffer)
        except Exception as e:
            logger.warning("Ошибка flush логов: %s", e)

    def get_logs(
        self,
        q: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Поиск логов с пагинацией.
        q — поиск по полю message (LIKE %q%).
        level — фильтр по уровню (INFO, WARNING, ERROR и т.д.).
        Returns (список записей, общее количество).
        """
        if not self.enabled:
            return [], 0
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        try:
            client = self._get_client()
            conditions = []
            params: Dict[str, Any] = {"limit": limit, "offset": offset}
            if q and q.strip():
                conditions.append("positionCaseInsensitive(message, %(q)s) > 0")
                params["q"] = q.strip()
            if level and level.strip():
                conditions.append("level = %(level)s")
                params["level"] = level.strip().upper()
            where = " AND ".join(conditions) if conditions else "1=1"
            total_rows = client.execute(
                f"SELECT count() FROM app_logs WHERE {where}",
                params,
            )
            total = int(total_rows[0][0]) if total_rows else 0
            rows = client.execute(
                f"SELECT ts, level, logger_name, message FROM app_logs "
                f"WHERE {where} ORDER BY ts DESC LIMIT %(limit)s OFFSET %(offset)s",
                {**params, "limit": limit, "offset": offset},
            )
            result = [
                {
                    "ts": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                    "level": r[1] or "",
                    "logger_name": r[2] or "",
                    "message": r[3] or "",
                }
                for r in rows
            ]
            return result, total
        except Exception as e:
            logger.warning("Ошибка чтения логов из ClickHouse: %s", e)
            return [], 0

    # --- Файлы загрузки (статус и пути) ---

    def save_file_download(
        self,
        chat_id: int,
        message_id: int,
        status: str,
        chat_title: str = "",
        file_name: str = "",
        file_path: str = "",
        file_size: int = 0,
        error_message: str = "",
        file_hash: str = "",
    ) -> None:
        """
        Сохранить запись о файле (успех, неудача, пропуск).
        status: downloaded | failed | skipped
        """
        if not self.enabled:
            return
        try:
            client = self._get_client()
            client.execute(
                "INSERT INTO file_downloads "
                "(chat_id, message_id, chat_title, file_name, file_path, status, "
                "file_size, error_message, created_at, file_hash) VALUES",
                [(
                    chat_id,
                    message_id,
                    (chat_title or "")[: 1024],
                    (file_name or "")[: 1024],
                    (file_path or "")[: 4096],
                    (status or "downloaded").lower()[: 32],
                    file_size if file_size >= 0 else 0,
                    (error_message or "")[: 2048],
                    datetime.now(),
                    (file_hash or "")[: 64],
                )],
            )
        except Exception as e:
            logger.warning("Ошибка записи file_download в ClickHouse: %s", e)

    def get_files(
        self,
        chat_id: Optional[int] = None,
        status: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Список файлов с фильтрами и поиском.
        q — поиск по file_name и file_path (case-insensitive).
        Returns (список записей, общее количество).
        """
        if not self.enabled:
            return [], 0
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        try:
            client = self._get_client()
            conditions = []
            params: Dict[str, Any] = {"limit": limit, "offset": offset}
            if chat_id is not None:
                conditions.append("chat_id = %(chat_id)s")
                params["chat_id"] = chat_id
            if status and status.strip():
                conditions.append("status = %(status)s")
                params["status"] = status.strip().lower()
            if q and q.strip():
                conditions.append(
                    "(positionCaseInsensitive(file_name, %(q)s) > 0 OR positionCaseInsensitive(file_path, %(q)s) > 0)"
                )
                params["q"] = q.strip()
            where = " AND ".join(conditions) if conditions else "1=1"
            total_rows = client.execute(
                f"SELECT count() FROM file_downloads FINAL WHERE {where}",
                params,
            )
            total = int(total_rows[0][0]) if total_rows else 0
            rows = client.execute(
                f"SELECT chat_id, message_id, chat_title, file_name, file_path, status, file_size, error_message, created_at "
                f"FROM file_downloads FINAL WHERE {where} "
                f"ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s",
                {**params, "limit": limit, "offset": offset},
            )
            result = []
            for r in rows:
                result.append({
                    "chat_id": r[0],
                    "message_id": r[1],
                    "chat_title": (r[2] or "").strip(),
                    "file_name": (r[3] or "").strip(),
                    "file_path": (r[4] or "").strip(),
                    "status": (r[5] or "downloaded").strip(),
                    "file_size": r[6] or 0,
                    "error_message": (r[7] or "").strip(),
                    "created_at": r[8].isoformat() if hasattr(r[8], "isoformat") else str(r[8]),
                })
            return result, total
        except Exception as e:
            logger.warning("Ошибка чтения file_downloads из ClickHouse: %s", e)
            return [], 0

    def delete_chat_data(self, chat_id: int) -> None:
        """
        Удалить все данные чата из messages, file_downloads и chats.
        Используется при удалении чата из архива (remove_chat_from_archive).
        """
        if not self.enabled:
            return
        try:
            client = self._get_client()
            client.execute(
                "ALTER TABLE messages DELETE WHERE chat_id = %(chat_id)s",
                {"chat_id": chat_id},
            )
            client.execute(
                "ALTER TABLE file_downloads DELETE WHERE chat_id = %(chat_id)s",
                {"chat_id": chat_id},
            )
            client.execute(
                "ALTER TABLE chats DELETE WHERE chat_id = %(chat_id)s",
                {"chat_id": chat_id},
            )
        except Exception as e:
            logger.warning("Ошибка удаления данных чата %s из ClickHouse: %s", chat_id, e)

    def close(self):
        """Закрыть соединение."""
        if self._client:
            self._client.disconnect()
            self._client = None
