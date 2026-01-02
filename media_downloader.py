"""Загрузка медиа из Telegram."""
import asyncio
import logging
import os
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple, Union

from rich.logging import RichHandler
from telethon import TelegramClient
from telethon.errors import FileReferenceExpiredError
from telethon.tl.types import (
    Document,
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    Photo,
)
from tqdm import tqdm

from utils.chat_selector import ChatSelector
from utils.config import ConfigManager
from utils.file_management import get_next_name, manage_duplicate_file
from utils.filter import MediaFilter
from utils.history import MessageHistory
from utils.i18n import get_i18n
from utils.log import LogFilter, configure_logging
from utils.meta import APP_VERSION, DEVICE_MODEL, LANG_CODE, SYSTEM_VERSION, print_meta
from utils.updates import check_for_updates

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler()],
)
logging.getLogger("telethon.client.downloads").addFilter(LogFilter())
logging.getLogger("telethon.network").addFilter(LogFilter())
logger = logging.getLogger("media_downloader")

THIS_DIR = os.path.dirname(os.path.abspath(__file__))


class DownloadManager:
    """Класс для управления загрузкой медиа из Telegram."""

    def __init__(self, config_manager: ConfigManager):
        """
        Инициализация DownloadManager.

        Parameters
        ----------
        config_manager: ConfigManager
            Менеджер конфигурации.
        """
        self.config_manager = config_manager
        self.config = config_manager.config
        self.failed_ids: List[int] = []
        self.downloaded_ids: List[int] = []
        self.downloaded_files: Dict[int, str] = {}  # {message_id: file_path}
        self.i18n = get_i18n(self.config.get("language", "ru"))
        self.media_filter = MediaFilter(self.config)

        # Настроить логирование
        configure_logging(self.config)

        # Инициализировать сохранение истории, если включено
        self.history_manager: Optional[MessageHistory] = None
        download_settings = self.config.get("download_settings", {})
        if download_settings.get("download_message_history", False):
            base_dir = download_settings.get("base_directory") or THIS_DIR
            history_format = download_settings.get("history_format", "json")
            history_dir = download_settings.get("history_directory", "history")
            self.history_manager = MessageHistory(base_dir, history_format, history_dir)

    def _can_download(
        self, _type: str, file_formats: dict, file_format: Optional[str]
    ) -> bool:
        """
        Проверить, можно ли загрузить файл данного формата.

        Parameters
        ----------
        _type: str
            Тип медиа объекта.
        file_formats: dict
            Словарь со списком форматов файлов для загрузки.
        file_format: str
            Формат текущего файла для загрузки.

        Returns
        -------
        bool
            True, если файл можно загрузить, иначе False.
        """
        if _type in ["audio", "document", "video"]:
            allowed_formats: list = file_formats.get(_type, ["all"])
            if file_format not in allowed_formats and allowed_formats[0] != "all":
                return False
        return True

    def _is_exist(self, file_path: str) -> bool:
        """
        Проверить, существует ли файл и это не директория.

        Parameters
        ----------
        file_path: str
            Абсолютный путь к файлу для проверки.

        Returns
        -------
        bool
            True, если файл существует, иначе False.
        """
        return not os.path.isdir(file_path) and os.path.exists(file_path)

    def _progress_callback(self, current: int, total: int, pbar: tqdm) -> None:
        """
        Обновить прогресс-бар для загрузки файлов.

        Parameters
        ----------
        current: int
            Текущее количество загруженных байт.
        total: int
            Общее количество байт для загрузки.
        pbar: tqdm
            Экземпляр прогресс-бара для обновления.
        """
        if pbar.total != total:
            pbar.total = total
            pbar.reset()
        pbar.update(current - pbar.n)

    def _sanitize_filename(self, filename: str) -> str:
        """
        Очистить имя файла от недопустимых символов для Windows и других ОС.

        Parameters
        ----------
        filename: str
            Исходное имя файла.

        Returns
        -------
        str
            Безопасное имя файла.
        """
        # Недопустимые символы в Windows: < > : " / \ | ? *
        # Заменяем на безопасные альтернативы
        replacements = {
            ':': '-',
            '<': '_',
            '>': '_',
            '"': "'",
            '/': '_',
            '\\': '_',
            '|': '_',
            '?': '_',
            '*': '_',
            '+': '_',  # Для часовых поясов
        }

        for char, replacement in replacements.items():
            filename = filename.replace(char, replacement)

        return filename

    async def _get_media_meta(
        self,
        media_obj: Union[Document, Photo],
        _type: str,
        download_directory: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """
        Извлечь имя файла и формат из медиа объекта.

        Parameters
        ----------
        media_obj: Union[Document, Photo]
            Медиа объект для извлечения.
        _type: str
            Тип медиа объекта.
        download_directory: Optional[str]
            Кастомная директория для загрузок. Если None, используется структура по умолчанию.

        Returns
        -------
        Tuple[str, Optional[str]]
            file_name, file_format
        """
        file_format: Optional[str] = None
        if hasattr(media_obj, "mime_type") and media_obj.mime_type:
            file_format = media_obj.mime_type.split("/")[-1]
        elif _type == "photo":
            file_format = "jpg"

        # Определить базовую директорию для загрузок
        base_dir = download_directory if download_directory else THIS_DIR

        if _type in ["voice", "video_note"]:
            # Форматировать дату безопасно для Windows
            # Вместо 2025-12-23T08:04:10+00:00 → 2025-12-23_08-04-10
            date_str = media_obj.date.strftime("%Y-%m-%d_%H-%M-%S")
            file_name: str = os.path.join(
                base_dir,
                _type,
                f"{_type}_{date_str}.{file_format}",
            )
        else:
            file_name_base = ""
            if hasattr(media_obj, "attributes"):
                for attr in media_obj.attributes:
                    if hasattr(attr, "file_name"):
                        # Очистить имя файла от недопустимых символов
                        file_name_base = self._sanitize_filename(attr.file_name)
                        break
            if file_name_base == "":
                if hasattr(media_obj, "id"):
                    file_name_base = f"{_type}_{media_obj.id}"
            file_name = os.path.join(base_dir, _type, file_name_base)
        return file_name, file_format

    def get_media_type(self, message: Message) -> Optional[str]:
        """
        Определить тип медиа из атрибутов сообщения.

        Parameters
        ----------
        message: Message
            Объект сообщения Telethon.

        Returns
        -------
        Optional[str]
            Тип медиа ('photo', 'video', 'audio', 'voice', 'video_note', 'document')
            или None.
        """
        if not message.media:
            return None
        if isinstance(message.media, MessageMediaPhoto):
            return "photo"
        if isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            for attr in doc.attributes:
                if hasattr(attr, "voice") and isinstance(attr.voice, bool):
                    return "voice" if attr.voice else "audio"
                if hasattr(attr, "round_message") and isinstance(attr.round_message, bool):
                    return "video_note" if attr.round_message else "video"
            return "document"
        return None

    async def download_media(  # pylint: disable=too-many-locals
        self,
        client: TelegramClient,
        message: Message,
        media_types: List[str],
        file_formats: dict,
        download_directory: Optional[str] = None,
    ) -> int:
        """
        Загрузить медиа из Telegram.

        Каждый файл загружается с 3 попытками с задержкой 5 секунд между попытками.

        Parameters
        ----------
        client: TelegramClient
            Клиент для взаимодействия с API Telegram.
        message: Message
            Объект сообщения из Telegram.
        media_types: list
            Список строк типов медиа для загрузки.
            Пример: ["audio", "photo"]
            Поддерживаемые форматы:
                * audio
                * document
                * photo
                * video
                * video_note
                * voice
        file_formats: dict
            Словарь со списком форматов файлов для загрузки
            для типов медиа `audio`, `document` & `video`.
        download_directory: Optional[str]
            Кастомная директория для загрузок. Если None, используется структура по умолчанию.

        Returns
        -------
        int
            ID текущего сообщения.
        """
        for retry in range(3):
            try:
                _type = self.get_media_type(message)
                logger.debug("Обработка сообщения %s типа %s", message.id, _type)
                if not _type or _type not in media_types:
                    return message.id

                # Применить фильтры
                if not self.media_filter.filter_message(message):
                    return message.id

                media_obj = message.photo if _type == "photo" else message.document
                if not media_obj:
                    return message.id
                file_name, file_format = await self._get_media_meta(
                    media_obj, _type, download_directory
                )
                if self._can_download(_type, file_formats, file_format):
                    # Создать прогресс-бар для загрузки
                    file_size = getattr(media_obj, "size", 0)
                    # Использовать оригинальное имя файла, если доступно, иначе сгенерированное
                    display_name = getattr(
                        media_obj, "file_name", os.path.basename(file_name)
                    )
                    desc = self.i18n.t("downloading", file=display_name)
                    logger.info(desc)

                    # Проверить, нужно ли пропускать дубликаты
                    skip_duplicates = self.config.get("download_settings", {}).get(
                        "skip_duplicates", True
                    )

                    if self._is_exist(file_name):
                        file_name = get_next_name(file_name)

                    # Скачать файл
                    with tqdm(
                        total=file_size, unit="B", unit_scale=True, desc=desc
                    ) as pbar:
                        # pylint: disable=cell-var-from-loop
                        download_path = await client.download_media(
                            message,
                            file=file_name,
                            progress_callback=lambda c, t: self._progress_callback(
                                c, t, pbar
                            ),
                        )

                    # Всегда проверять дубликаты после загрузки (если включено)
                    if download_path and skip_duplicates:
                        download_path = manage_duplicate_file(
                            download_path, enabled=True
                        )  # type: ignore

                    if download_path:
                        logger.info(self.i18n.t("downloaded", path=download_path))
                        logger.debug("Успешно загружено сообщение %s", message.id)
                        # Сохранить путь к файлу
                        self.downloaded_files[message.id] = download_path
                    self.downloaded_ids.append(message.id)
                break
            except FileReferenceExpiredError:
                logger.warning(
                    self.i18n.t("file_reference_expired", id=message.id)
                )
                messages = await client.get_messages(message.chat.id, ids=message.id)
                message = messages[0] if messages else message
                if retry == 2:
                    logger.error(
                        self.i18n.t("file_reference_expired_skip", id=message.id)
                    )
                    self.failed_ids.append(message.id)
            except TimeoutError:
                logger.warning(
                    self.i18n.t("timeout_error", id=message.id)
                )
                await asyncio.sleep(5)
                if retry == 2:
                    logger.error(
                        self.i18n.t("timeout_skip", id=message.id)
                    )
                    self.failed_ids.append(message.id)
            except Exception as e:
                logger.error(
                    self.i18n.t("download_exception", id=message.id, error=str(e)),
                    exc_info=True,
                )
                self.failed_ids.append(message.id)
                break
        return message.id

    async def process_messages(
        self,
        client: TelegramClient,
        messages: List[Message],
        media_types: List[str],
        file_formats: dict,
        download_directory: Optional[str] = None,
        semaphore: Optional[asyncio.Semaphore] = None,
    ) -> int:
        """
        Загрузить медиа из Telegram.

        Parameters
        ----------
        client: TelegramClient
            Клиент для взаимодействия с API Telegram.
        messages: list
            Список сообщений Telegram.
        media_types: list
            Список строк типов медиа для загрузки.
            Пример: `["audio", "photo"]`
            Поддерживаемые форматы:
                * audio
                * document
                * photo
                * video
                * video_note
                * voice
        file_formats: dict
            Словарь со списком форматов файлов для загрузки
            для типов медиа `audio`, `document` & `video`.
        download_directory: Optional[str]
            Кастомная директория для загрузок. Если None, используется структура по умолчанию.
        semaphore: Optional[asyncio.Semaphore]
            Семафор для ограничения параллельных загрузок.

        Returns
        -------
        int
            Максимальное значение из списка ID сообщений.
        """
        async def download_with_semaphore(message):
            if semaphore:
                async with semaphore:
                    return await self.download_media(
                        client, message, media_types, file_formats, download_directory
                    )
            else:
                return await self.download_media(
                    client, message, media_types, file_formats, download_directory
                )

        message_ids = await asyncio.gather(
            *[download_with_semaphore(message) for message in messages]
        )
        logger.info(self.i18n.t("processed_batch", count=len(messages)))

        # Сохранить историю ВСЕХ сообщений, если включено
        if self.history_manager and messages:
            chat_id = messages[0].chat.id if messages else 0
            chat_title = getattr(messages[0].chat, "title", None)
            # Сохранить ВСЕ сообщения с информацией о скачанных файлах
            self.history_manager.save_batch(
                messages, chat_id, chat_title, self.downloaded_files
            )

        last_message_id: int = max(message_ids)
        return last_message_id

    def update_config(self, chat_id: Optional[int] = None) -> None:
        """
        Обновить конфигурацию.

        Parameters
        ----------
        chat_id: Optional[int]
            ID чата для обновления состояния. Если None, используется chat_id из конфига.
        """
        config = self.config.copy()
        if chat_id is None:
            chat_id = config.get("chat_id")

        # Обновить ids_to_retry
        ids_to_retry = config.get("ids_to_retry", [])
        ids_to_retry = list(set(ids_to_retry) - set(self.downloaded_ids)) + self.failed_ids

        # Обновить состояние чата
        self.config_manager.update_chat_state(
            chat_id, config.get("last_read_message_id", 0), ids_to_retry
        )
        self.config_manager.save()
        logger.info(self.i18n.t("updated_message_id"))

    async def begin_import_chat(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self, client: TelegramClient, chat_id: int, chat_title: Optional[str] = None, pagination_limit: int = 100
    ) -> None:
        """
        Инициировать загрузку для конкретного чата.

        Parameters
        ----------
        client: TelegramClient
            Клиент Telethon (уже подключенный).
        chat_id: int
            ID чата для загрузки.
        chat_title: Optional[str]
            Название чата.
        pagination_limit: int
            Количество сообщений для загрузки асинхронно как пакет.
        """
        last_read_message_id: int = self.config.get("last_read_message_id", 0)
        start_date_val = self.config.get("start_date")
        if isinstance(start_date_val, str) and start_date_val.strip():
            start_date = datetime.fromisoformat(start_date_val)
            if start_date.tzinfo is None:
                start_date = start_date.replace(tzinfo=timezone.utc)
        elif isinstance(start_date_val, date):
            start_date = datetime.combine(
                start_date_val, datetime.min.time(), tzinfo=timezone.utc
            )
        else:
            start_date = None
        logger.info(self.i18n.t("start_date_filter", date=start_date or "None"))
        end_date_val = self.config.get("end_date")
        if isinstance(end_date_val, str) and end_date_val.strip():
            end_date = datetime.fromisoformat(end_date_val)
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
        elif isinstance(end_date_val, date):
            end_date = datetime.combine(
                end_date_val, datetime.min.time(), tzinfo=timezone.utc
            )
        else:
            end_date = None
        logger.info(self.i18n.t("end_date_filter", date=end_date or "None"))
        max_messages_val = self.config.get("max_messages")
        if isinstance(max_messages_val, int):
            max_messages = max_messages_val
        elif isinstance(max_messages_val, str) and max_messages_val.strip():
            max_messages = int(max_messages_val)
        else:
            max_messages = None
        logger.info(
            self.i18n.t("max_messages", count=max_messages or "Unlimited")
        )
        download_directory_val = self.config.get("download_settings", {}).get("base_directory")
        if isinstance(download_directory_val, str) and download_directory_val.strip():
            download_directory = download_directory_val.strip()
            # Преобразовать в абсолютный путь, если относительный
            if not os.path.isabs(download_directory):
                download_directory = os.path.abspath(download_directory)
            # Создать директорию, если не существует
            os.makedirs(download_directory, exist_ok=True)
            logger.info(self.i18n.t("download_directory", dir=download_directory))
        else:
            download_directory = None
            logger.info(self.i18n.t("download_directory_default"))

        # Настройка параллельных загрузок
        max_parallel = self.config.get("download_settings", {}).get(
            "max_parallel_downloads", None
        )
        semaphore = asyncio.Semaphore(max_parallel) if max_parallel else None

        # Получить типы медиа
        media_types = self.config.get("media_types", [])
        if "all" in media_types:
            media_types = ["audio", "document", "photo", "video", "voice", "video_note"]

        # Получить last_read_message_id для этого чата
        if "chats" in self.config and isinstance(self.config["chats"], list):
            chat_config = next(
                (c for c in self.config["chats"] if c.get("chat_id") == chat_id), None
            )
            if chat_config:
                last_read_message_id = chat_config.get("last_read_message_id", 0)
                ids_to_retry = chat_config.get("ids_to_retry", [])
            else:
                last_read_message_id = 0
                ids_to_retry = []
        else:
            # Старая структура - использовать общий chat_id
            if self.config.get("chat_id") == chat_id:
                last_read_message_id = self.config.get("last_read_message_id", 0)
                ids_to_retry = self.config.get("ids_to_retry", [])
            else:
                last_read_message_id = 0
                ids_to_retry = []

        try:
            messages_iter = client.iter_messages(
                chat_id, min_id=last_read_message_id + 1, reverse=True
            )
        except ValueError as e:
            logger.error(f"Ошибка при получении сообщений для чата {chat_id}: {e}")
            logger.error("Проверьте правильность chat_id в конфигурации")
            return
        messages_list: list = []
        pagination_count: int = 0
        if ids_to_retry:
            logger.info(self.i18n.t("retrying"))
            skipped_messages: list = await client.get_messages(  # type: ignore
                chat_id, ids=ids_to_retry
            )
            for message in skipped_messages:
                pagination_count += 1
                messages_list.append(message)

        async for message in messages_iter:  # type: ignore
            if end_date and message.date > end_date:
                continue
            if start_date and message.date < start_date:
                break
            if pagination_count != pagination_limit:
                pagination_count += 1
                messages_list.append(message)
            else:
                last_read_message_id = await self.process_messages(
                    client,
                    messages_list,
                    media_types,
                    self.config.get("file_formats", {}),
                    download_directory,
                    semaphore,
                )
                if max_messages and len(self.downloaded_ids) >= max_messages:
                    break
                pagination_count = 0
                messages_list = []
                messages_list.append(message)
                self.config["last_read_message_id"] = last_read_message_id
                self.update_config(chat_id)
        if messages_list:
            last_read_message_id = await self.process_messages(
                client,
                messages_list,
                media_types,
                self.config.get("file_formats", {}),
                download_directory,
                semaphore,
            )

        self.config["last_read_message_id"] = last_read_message_id
        self.update_config(chat_id)


async def main_async():
    """Асинхронная главная функция загрузчика."""
    config_manager = ConfigManager()
    try:
        config = config_manager.load()
    except FileNotFoundError as e:
        logger.error(str(e))
        return
    except ValueError as e:
        logger.error(f"Ошибка валидации конфигурации: {e}")
        return

    # Создать клиент для выбора чатов
    proxy = config.get("proxy")
    proxy_dict = None
    if proxy:
        proxy_dict = {
            "proxy_type": proxy["scheme"],
            "addr": proxy["hostname"],
            "port": proxy["port"],
            "username": proxy.get("username"),
            "password": proxy.get("password"),
        }
    client = TelegramClient(
        "media_downloader",
        api_id=config["api_id"],
        api_hash=config["api_hash"],
        proxy=proxy_dict,
        device_model=DEVICE_MODEL,
        system_version=SYSTEM_VERSION,
        app_version=APP_VERSION,
        lang_code=LANG_CODE,
    )
    await client.start()

    # Выбор чатов
    language = config.get("language", "ru")
    chat_selector = ChatSelector(client, language)

    # Проверить, есть ли сохраненные чаты в конфиге
    selected_chats = []
    if "chats" in config and isinstance(config["chats"], list):
        # Использовать сохраненные чаты
        enabled_chats = [
            (c["chat_id"], c.get("title", ""), "saved")
            for c in config["chats"]
            if c.get("enabled", True)
        ]
        if enabled_chats:
            use_saved = True
            if config.get("interactive_chat_selection", True):
                from rich.prompt import Confirm
                use_saved = Confirm.ask(
                    "Использовать сохраненные чаты?", default=True
                )
            if use_saved:
                selected_chats = enabled_chats
            else:
                selected_chats = await chat_selector.select_chats(allow_multiple=True)
        else:
            selected_chats = await chat_selector.select_chats(allow_multiple=True)
    elif config.get("chat_id"):
        # Старая структура - один чат
        selected_chats = [(config["chat_id"], "", "single")]
    else:
        # Интерактивный выбор
        selected_chats = await chat_selector.select_chats(allow_multiple=True)

    if not selected_chats:
        logger.warning("Не выбрано ни одного чата для загрузки")
        await client.disconnect()
        return

    # Сохранить выбранные чаты в конфиг
    if not ("chats" in config and isinstance(config["chats"], list)):
        config["chats"] = []

    for chat_id, title, _ in selected_chats:
        # Проверить, есть ли уже этот чат в конфиге
        existing_chat = next(
            (c for c in config["chats"] if c.get("chat_id") == chat_id), None
        )
        if not existing_chat:
            config["chats"].append({
                "chat_id": chat_id,
                "title": title,
                "last_read_message_id": 0,
                "ids_to_retry": [],
                "enabled": True,
            })
    config_manager.save(config)

    # Загрузить для каждого выбранного чата
    download_manager = DownloadManager(config_manager)
    pagination_limit = config.get("download_settings", {}).get("pagination_limit", 100)

    for chat_id, chat_title, _ in selected_chats:
        logger.info(f"Начало загрузки для чата: {chat_title or chat_id}")
        # Временно установить chat_id для этого чата
        download_manager.config["chat_id"] = chat_id
        await download_manager.begin_import_chat(
            client, chat_id, chat_title, pagination_limit
        )

    await client.disconnect()

    if download_manager.failed_ids:
        i18n = get_i18n()
        logger.info(
            i18n.t("download_failed", count=len(set(download_manager.failed_ids)))
            + "\n"
            + i18n.t("failed_ids_added")
        )
    check_for_updates()


def main():
    """Главная функция загрузчика."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(main_async())


if __name__ == "__main__":
    print_meta(logger)
    main()
