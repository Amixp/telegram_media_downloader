"""Модуль управления сессией Telegram."""
import logging
from typing import Any, Dict, Optional

from telethon import TelegramClient

from utils.meta import APP_VERSION, DEVICE_MODEL, LANG_CODE, SYSTEM_VERSION
from utils.proxy import get_proxy_config

logger = logging.getLogger(__name__)


class SessionManager:
    """Класс для управления сессией TelegramClient."""

    def __init__(self, config: Dict[str, Any]):
        """
        Инициализация SessionManager.

        Parameters
        ----------
        config: Dict[str, Any]
            Конфигурация приложения.
        """
        self.config = config
        self.client: Optional[TelegramClient] = None

    async def create_client(self) -> TelegramClient:
        """
        Создать и инициализировать клиент.

        Returns
        -------
        TelegramClient
            Настроенный клиент Telethon.
        """
        proxy_config = get_proxy_config(self.config)

        session_name = "media_downloader"

        self.client = TelegramClient(
            session_name,
            api_id=self.config["api_id"],
            api_hash=self.config["api_hash"],
            proxy=proxy_config,
            device_model=DEVICE_MODEL,
            system_version=SYSTEM_VERSION,
            app_version=APP_VERSION,
            lang_code=LANG_CODE,
        )

        await self.client.start()
        return self.client

    async def stop(self) -> None:
        """Остановить клиент."""
        if self.client:
            await self.client.disconnect()
