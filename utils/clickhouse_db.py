import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime
import asyncio

try:
    from clickhouse_driver import Client
except ImportError:
    Client = None

logger = logging.getLogger(__name__)

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
        self.insert_settings = config.get("insert_settings", {}) or {}

        self._client = None
        self._message_buffer = []  # legacy; flush path keeps backward compatibility

    def _get_insert_settings(self, table: str) -> Dict[str, Any]:
        """Получить settings для асинхронной вставки с перезаписью per-table."""
        default_settings = {
            "async_insert": 1,
            "wait_for_async_insert": 0,
            "async_insert_busy_timeout_ms": 200,
            "async_insert_stale_timeout_ms": 2000,
            "max_insert_threads": 4,
        }
        table_defaults = {
            "chats": {"wait_for_async_insert": 1},
            "messages": {"wait_for_async_insert": 0},
            "file_downloads": {"wait_for_async_insert": 0},
            "app_logs": {"wait_for_async_insert": 0},
        }
        cfg_default = self.insert_settings.get("default", {}) if isinstance(self.insert_settings, dict) else {}
        cfg_tables = self.insert_settings.get("tables", {}) if isinstance(self.insert_settings, dict) else {}
        cfg_table = cfg_tables.get(table, {}) if isinstance(cfg_tables, dict) else {}
        merged = {**default_settings, **table_defaults.get(table, {}), **cfg_default, **cfg_table}
        return {k: v for k, v in merged.items() if v is not None}

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

        # Миграция: колонка entities для форматирования текста
        try:
            client.execute(
                "ALTER TABLE messages ADD COLUMN IF NOT EXISTS entities String DEFAULT ''"
            )
        except Exception as e:
            logger.warning("Миграция entities (возможно уже применена): %s", e)

        # Миграция: описание и username чата
        try:
            client.execute(
                "ALTER TABLE chats ADD COLUMN IF NOT EXISTS description String DEFAULT ''"
            )
            client.execute(
                "ALTER TABLE chats ADD COLUMN IF NOT EXISTS username String DEFAULT ''"
            )
        except Exception as e:
            logger.warning("Миграция chats description/username (возможно уже применена): %s", e)

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

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._insert_messages, [data])
        except Exception as e:
            logger.error("Ошибка при записи сообщения в ClickHouse: %s", e)

    async def flush(self):
        """Принудительно записать буфер в БД."""
        if not self.enabled:
            return
        # Batch flush больше не используется; метод оставлен для обратной совместимости.
        return

    def _insert_messages(self, messages: List[Dict[str, Any]]):
        """Вставка сообщений (синхронно для executor)."""
        client = self._get_client()
        query = (
            "INSERT INTO messages (chat_id, message_id, date, text, media_type, "
            "file_path, file_size, downloaded, download_date, sender_id, chat_title, file_hash, entities) VALUES"
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
                _str(m.get("entities", "")),
            )
            for m in messages
        ]
        client.execute(query, data, settings=self._get_insert_settings("messages"))

    async def update_chat_info(
        self,
        chat_id: int,
        title: str,
        message_count: int,
        total_size: int = 0,
        description: Optional[str] = None,
        username: Optional[str] = None,
    ):
        """Обновить информацию о чате."""
        if not self.enabled:
            return

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._insert_chat,
                chat_id, title, message_count, total_size, description, username
            )
        except Exception as e:
            logger.error(f"Ошибка при обновлении информации о чате в ClickHouse: {e}")

    def _insert_chat(
        self,
        chat_id: int,
        title: str,
        message_count: int,
        total_size: int,
        description: Optional[str] = None,
        username: Optional[str] = None,
    ):
        client = self._get_client()
        desc = "" if description is None else str(description)
        uname = "" if username is None else str(username)
        query = (
            "INSERT INTO chats (chat_id, title, last_sync, message_count, total_size, description, username) VALUES"
        )
        client.execute(
            query,
            [(chat_id, "" if title is None else str(title), datetime.now(), message_count, total_size, desc, uname)],
            settings=self._get_insert_settings("chats"),
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
        При дублях (chat_id, message_id) оставляется запись с file_path.
        """
        if not self.enabled:
            return []
        try:
            client = self._get_client()
            rows = client.execute(
                """
                SELECT chat_id, message_id, date, text, media_type, file_path, file_size,
                    sender_id, chat_title, entities
                FROM (
                    SELECT chat_id, message_id, date, text, media_type, file_path, file_size,
                        sender_id, chat_title, entities,
                        row_number() OVER (
                            PARTITION BY chat_id, message_id
                            ORDER BY if(file_path != '', 1, 0) DESC, date DESC
                        ) AS rn
                    FROM messages
                    WHERE chat_id = %(chat_id)s
                )
                WHERE rn = 1
                ORDER BY date, message_id
                """,
                {"chat_id": chat_id},
            )
            result: List[Dict[str, Any]] = []
            for row in rows:
                entities_val = row[9] if len(row) > 9 else ""
                (r_chat_id, msg_id, date_val, text_val, media_type_val, file_path_val, file_size_val, sender_val, title_val) = row[:9]
                date_iso = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
                media_type_str = "" if media_type_val is None else str(media_type_val)
                has_media = bool(media_type_str and media_type_str != "None")
                entities_parsed = []
                if entities_val and isinstance(entities_val, str) and entities_val.strip():
                    try:
                        entities_parsed = json.loads(entities_val)
                    except Exception:
                        pass
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
                    "entities": entities_parsed,
                })
            return result
        except Exception as e:
            logger.warning("Ошибка чтения сообщений из ClickHouse: %s", e)
            return []

    def get_chat_meta(self, chat_id: int) -> Dict[str, Any]:
        """
        Получить метаданные чата (description, username, profile_link) из таблицы chats.
        """
        if not self.enabled:
            return {"description": "", "username": "", "profile_link": ""}
        try:
            client = self._get_client()
            rows = client.execute(
                "SELECT description, username FROM chats WHERE chat_id = %(chat_id)s LIMIT 1",
                {"chat_id": chat_id},
            )
            desc = ""
            username = ""
            if rows:
                desc = (rows[0][0] or "").strip()
                username = (rows[0][1] or "").strip() if len(rows[0]) > 1 else ""
            if username:
                profile_link = f"https://t.me/{username}"
            elif chat_id < -100:
                cid = str(chat_id).replace("-100", "")
                profile_link = f"https://t.me/c/{cid}"
            else:
                profile_link = ""
            return {"description": desc, "username": username, "profile_link": profile_link}
        except Exception as e:
            logger.warning("Ошибка чтения chat_meta из ClickHouse: %s", e)
            return {"description": "", "username": "", "profile_link": ""}

    def get_chat_id_by_username(self, username: str) -> Optional[int]:
        """
        Получить chat_id по username.

        Parameters
        ----------
        username : str
            Username чата (без @).

        Returns
        -------
        Optional[int]
            chat_id если найден, иначе None.
        """
        if not self.enabled:
            return None
        try:
            client = self._get_client()
            rows = client.execute(
                "SELECT chat_id FROM chats WHERE username = %(username)s LIMIT 1",
                {"username": username},
            )
            if rows:
                return int(rows[0][0])
            return None
        except Exception as e:
            logger.warning("Ошибка поиска чата по username %s: %s", username, e)
            return None

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
                SELECT chat_id, any(chat_title) AS title, count() AS cnt, max(d) AS last_date
                FROM (
                    SELECT chat_id, message_id, any(chat_title) AS chat_title, max(date) AS d
                    FROM messages
                    GROUP BY chat_id, message_id
                )
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
                "SELECT count() FROM (SELECT 1 FROM messages WHERE chat_id = %(chat_id)s GROUP BY chat_id, message_id)",
                {"chat_id": chat_id},
            )
            total = int(total_rows[0][0]) if total_rows else 0

            rows = client.execute(
                f"""
                SELECT chat_id, message_id, date, text, media_type, file_path, file_size,
                    sender_id, chat_title, entities
                FROM (
                    SELECT chat_id, message_id, date, text, media_type, file_path, file_size,
                        sender_id, chat_title, entities,
                        row_number() OVER (
                            PARTITION BY chat_id, message_id
                            ORDER BY if(file_path != '', 1, 0) DESC, date DESC
                        ) AS rn
                    FROM messages
                    WHERE chat_id = %(chat_id)s
                )
                WHERE rn = 1
                ORDER BY date, message_id
                LIMIT {limit} OFFSET {offset}
                """,
                {"chat_id": chat_id},
            )
            result: List[Dict[str, Any]] = []
            for row in rows:
                entities_val = row[9] if len(row) > 9 else ""
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
                ) = row[:9]
                date_iso = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
                media_type_str = "" if media_type_val is None else str(media_type_val)
                has_media = bool(media_type_str and media_type_str != "None")
                entities_parsed = []
                if entities_val and isinstance(entities_val, str) and entities_val.strip():
                    try:
                        entities_parsed = json.loads(entities_val)
                    except Exception:
                        pass
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
                    "entities": entities_parsed,
                })
            return result, total
        except Exception as e:
            logger.warning("Ошибка чтения страницы сообщений из ClickHouse: %s", e)
            return [], 0

    def search_messages_all_chats(
        self, q: str, offset: int = 0, limit: int = 100
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Нечеткий поиск сообщений по тексту во всех чатах.
        Разбивает запрос на слова и ищет сообщения, содержащие любое из слов
        (positionCaseInsensitive). Возвращает сообщения с chat_id, chat_title, message_id.
        """
        if not self.enabled:
            return [], 0
        q = (q or "").strip()
        if not q:
            return [], 0
        words = [w.strip() for w in q.split() if w.strip()]
        if not words:
            return [], 0
        offset = max(0, min(offset, 10_000_000))
        limit = max(1, min(limit, 500))
        try:
            client = self._get_client()
            params: Dict[str, Any] = {"limit": limit, "offset": offset}
            conditions = []
            for i, w in enumerate(words):
                key = f"w{i}"
                params[key] = w
                conditions.append(f"positionCaseInsensitive(text, %({key})s) > 0")
            where_clause = " OR ".join(conditions)

            total_rows = client.execute(
                f"""
                SELECT count() FROM (
                    SELECT 1 FROM messages
                    WHERE {where_clause}
                    GROUP BY chat_id, message_id
                )""",
                params,
            )
            total = int(total_rows[0][0]) if total_rows else 0

            rows = client.execute(
                f"""
                SELECT chat_id, message_id, date, text, media_type, file_path, file_size,
                    sender_id, chat_title, entities
                FROM (
                    SELECT chat_id, message_id, date, text, media_type, file_path, file_size,
                        sender_id, chat_title, entities,
                        row_number() OVER (
                            PARTITION BY chat_id, message_id
                            ORDER BY if(file_path != '', 1, 0) DESC, date DESC
                        ) AS rn
                    FROM messages
                    WHERE {where_clause}
                )
                WHERE rn = 1
                ORDER BY date DESC, message_id DESC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            result: List[Dict[str, Any]] = []
            for row in rows:
                entities_val = row[9] if len(row) > 9 else ""
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
                ) = row[:9]
                date_iso = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
                media_type_str = "" if media_type_val is None else str(media_type_val)
                has_media = bool(media_type_str and media_type_str != "None")
                entities_parsed = []
                if entities_val and isinstance(entities_val, str) and entities_val.strip():
                    try:
                        entities_parsed = json.loads(entities_val)
                    except Exception:
                        pass
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
                    "entities": entities_parsed,
                })
            return result, total
        except Exception as e:
            logger.warning("Ошибка поиска сообщений в ClickHouse: %s", e)
            return [], 0

    def message_exists(self, chat_id: int, message_id: int) -> bool:
        """Проверить, есть ли сообщение в архиве (любая запись с таким chat_id и message_id)."""
        if not self.enabled:
            return False
        try:
            client = self._get_client()
            rows = client.execute(
                "SELECT 1 FROM messages WHERE chat_id = %(chat_id)s AND message_id = %(message_id)s LIMIT 1",
                {"chat_id": chat_id, "message_id": message_id},
            )
            return len(rows) > 0
        except Exception as e:
            logger.warning("Ошибка проверки сообщения в ClickHouse: %s", e)
            return False

    def get_message_file_path(
        self, chat_id: int, message_id: int
    ) -> Optional[str]:
        """
        Получить путь к файлу сообщения по chat_id и message_id.
        Возвращает None, если запись не найдена или file_path пустой.
        """
        path, _ = self.get_message_file_path_and_media_type(chat_id, message_id)
        return path

    def get_message_file_path_and_media_type(
        self, chat_id: int, message_id: int
    ) -> Tuple[Optional[str], str]:
        """
        Получить (путь к файлу, media_type) по chat_id и message_id.
        Возвращает (None, "") если запись не найдена или file_path пустой.
        """
        if not self.enabled:
            return None, ""
        try:
            client = self._get_client()
            rows = client.execute(
                "SELECT file_path, media_type FROM messages WHERE chat_id = %(chat_id)s AND message_id = %(message_id)s "
                "ORDER BY if(file_path != '', 1, 0) DESC LIMIT 1",
                {"chat_id": chat_id, "message_id": message_id},
            )
            if not rows:
                return None, ""
            path = (rows[0][0] or "").strip()
            media_type = (rows[0][1] or "").strip() if len(rows[0]) > 1 else ""
            return (path if path else None), media_type
        except Exception as e:
            logger.warning("Ошибка чтения file_path/media_type из ClickHouse: %s", e)
            return None, ""

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

    def get_chat_stats(self, chat_id: int) -> Optional[Tuple[str, int, Optional[datetime]]]:
        """
        Статистика одного чата: (title, message_count, last_message_date).
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
            logger.warning("Ошибка чтения статистики чата из ClickHouse: %s", e)
            return None

    # --- Логи приложения ---

    def save_log(self, level: str, message: str, logger_name: str = "") -> None:
        """
        Сохранить запись лога в ClickHouse (асинхронная вставка на стороне CH).
        """
        if not self.enabled:
            return
        try:
            client = self._get_client()
            client.execute(
                "INSERT INTO app_logs (ts, level, logger_name, message) VALUES",
                [(
                    datetime.now(),
                    (level or "INFO").upper(),
                    (logger_name or "").strip()[:255],
                    (message or "")[: 65535],
                )],
                settings=self._get_insert_settings("app_logs"),
            )
        except Exception as e:
            logger.warning("Ошибка записи логов в ClickHouse: %s", e)

    def _flush_log_buffer(self) -> None:
        """Legacy no-op: пакетный буфер логов больше не используется."""
        return

    async def flush_logs(self) -> None:
        """Legacy no-op: логи пишутся сразу."""
        return

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

    def is_message_skipped_stale(self, chat_id: int, message_id: int) -> bool:
        """
        Проверить, помечено ли сообщение как зависшее/таймаут для пропуска.
        Если в file_downloads есть status=failed и error_message содержит timeout/stale —
        пропускать загрузку при следующей попытке.
        """
        if not self.enabled:
            return False
        try:
            client = self._get_client()
            rows = client.execute(
                "SELECT status, error_message FROM file_downloads "
                "WHERE chat_id = %(chat_id)s AND message_id = %(message_id)s "
                "ORDER BY created_at DESC LIMIT 1",
                {"chat_id": chat_id, "message_id": message_id},
            )
            if not rows:
                return False
            status = (rows[0][0] or "").lower()
            err = (rows[0][1] or "").lower() if len(rows[0]) > 1 else ""
            if status != "failed":
                return False
            skip_markers = ("timeout", "stale", "завис", "нет данных")
            return any(m in err for m in skip_markers)
        except Exception as e:
            logger.warning("Ошибка проверки is_message_skipped_stale: %s", e)
            return False

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
                settings=self._get_insert_settings("file_downloads"),
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

    def clear_chat_messages(self, chat_id: int) -> None:
        """
        Удалить все сообщения чата из таблицы messages (метаданные чата и file_downloads не трогает).
        """
        if not self.enabled:
            return
        try:
            client = self._get_client()
            client.execute(
                "ALTER TABLE messages DELETE WHERE chat_id = %(chat_id)s",
                {"chat_id": chat_id},
            )
        except Exception as e:
            logger.warning("Ошибка очистки сообщений чата %s из ClickHouse: %s", chat_id, e)

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

    def chat_exists(self, chat_id: int) -> bool:
        """Проверить существование чата в таблице chats.

        Parameters
        ----------
        chat_id : int
            ID чата

        Returns
        -------
        bool
            True, если чат существует в БД
        """
        if not self.enabled:
            return False
        try:
            client = self._get_client()
            rows = client.execute(
                "SELECT 1 FROM chats WHERE chat_id = %(chat_id)s LIMIT 1",
                {"chat_id": chat_id},
            )
            return len(rows) > 0
        except Exception as e:
            logger.warning("Ошибка проверки существования чата %s: %s", chat_id, e)
            return False

    def ensure_chat_in_db(self, chat_id: int, title: str = "") -> None:
        """Добавить чат в таблицу chats, если его там нет.

        Parameters
        ----------
        chat_id : int
            ID чата
        title : str
            Название чата (опционально)
        """
        if not self.enabled:
            return

        # Проверить существование чата перед вставкой
        if self.chat_exists(chat_id):
            logger.debug("Чат %s уже существует в БД", chat_id)
            return

        try:
            client = self._get_client()
            # INSERT для ReplacingMergeTree
            client.execute(
                """
                INSERT INTO chats (chat_id, title, last_sync, message_count, total_size)
                VALUES (%(chat_id)s, %(title)s, now(), 0, 0)
                """,
                {"chat_id": chat_id, "title": title or f"Chat {chat_id}"},
                settings=self._get_insert_settings("chats"),
            )
            logger.info("Чат %s добавлен в БД", chat_id)
        except Exception as e:
            # ReplacingMergeTree может вызвать конфликт при параллельной вставке
            logger.debug("Чат %s: ошибка вставки (возможно уже существует): %s", chat_id, e)

    def get_all_chats(self) -> List[Tuple[int, str]]:
        """Получить список всех чатов из таблицы chats.

        Returns
        -------
        List[Tuple[int, str]]
            Список кортежей (chat_id, title)
        """
        if not self.enabled:
            return []
        try:
            client = self._get_client()
            rows = client.execute(
                """
                SELECT chat_id, any(title) as title
                FROM chats
                GROUP BY chat_id
                ORDER BY chat_id
                """
            )
            return [(int(row[0]), str(row[1])) for row in rows]
        except Exception as e:
            logger.warning("Ошибка получения списка чатов из БД: %s", e)
            return []

    def save_selected_chats(self, chats: List[Tuple[int, str]]) -> None:
        """Сохранить выбранные чаты в БД.

        Parameters
        ----------
        chats : List[Tuple[int, str]]
            Список кортежей (chat_id, title)
        """
        if not self.enabled:
            return
        try:
            for chat_id, title in chats:
                self.ensure_chat_in_db(chat_id, title)
            logger.info("Сохранено %s чатов в БД", len(chats))
        except Exception as e:
            logger.warning("Ошибка сохранения чатов в БД: %s", e)

    def get_last_processed_message_id(self, chat_id: int) -> Optional[int]:
        """Получить максимальный message_id для чата из БД.

        Parameters
        ----------
        chat_id : int
            ID чата

        Returns
        -------
        Optional[int]
            Максимальный message_id или None, если сообщений нет
        """
        if not self.enabled:
            return None
        try:
            client = self._get_client()
            rows = client.execute(
                "SELECT max(message_id) FROM messages WHERE chat_id = %(chat_id)s",
                {"chat_id": chat_id},
            )
            if rows and rows[0][0] is not None:
                return int(rows[0][0])
            return None
        except Exception as e:
            logger.warning("Ошибка получения последнего message_id для чата %s: %s", chat_id, e)
            return None

    def get_retry_message_ids(self, chat_id: int, limit: int = 1000) -> List[int]:
        """Получить список message_id для повторной загрузки из file_downloads.

        Parameters
        ----------
        chat_id : int
            ID чата
        limit : int
            Максимальное количество записей (по умолчанию 1000)

        Returns
        -------
        List[int]
            Список message_id со статусом failed или skipped (исключая downloaded/existing)
        """
        if not self.enabled:
            return []
        try:
            client = self._get_client()
            rows = client.execute(
                """
                SELECT message_id
                FROM file_downloads FINAL
                WHERE chat_id = %(chat_id)s
                  AND status IN ('failed', 'skipped')
                ORDER BY created_at DESC
                LIMIT %(limit)s
                """,
                {"chat_id": chat_id, "limit": limit},
            )
            return [int(row[0]) for row in rows]
        except Exception as e:
            logger.warning("Ошибка получения списка retry для чата %s: %s", chat_id, e)
            return []

    def close(self):
        """Закрыть соединение."""
        if self._client:
            self._client.disconnect()
            self._client = None
