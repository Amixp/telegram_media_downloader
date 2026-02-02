import logging
from typing import Any, Dict, List, Optional
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
        query = "INSERT INTO messages (chat_id, message_id, date, text, media_type, file_path, file_size, downloaded, download_date, sender_id, chat_title) VALUES"
        data = [
            (
                m["chat_id"],
                m["message_id"],
                m["date"],
                m["text"],
                m["media_type"],
                m.get("file_path", ""),
                m.get("file_size", 0),
                1 if m.get("file_path") else 0,
                datetime.now(),
                m.get("sender_id", 0),
                m.get("chat_title", "")
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
        client.execute(query, [(chat_id, title, datetime.now(), message_count, total_size)])

    def close(self):
        """Закрыть соединение."""
        if self._client:
            self._client.disconnect()
            self._client = None
