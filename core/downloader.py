"Модуль загрузчика медиа."
import asyncio
import logging
import os
from typing import Dict, List, Optional, Tuple, Union

from telethon import TelegramClient
from telethon.errors import FileMigrateError, FileReferenceExpiredError
from telethon.tl.types import (
    Document,
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    Photo,
)
from tqdm import tqdm

from utils.config import ConfigManager
from utils.file_management import get_next_name, manage_duplicate_file
from utils.filter import MediaFilter
from utils.history import MessageHistory
from utils.i18n import get_i18n
from utils.log import configure_logging
from utils.media_utils import get_media_type, sanitize_filename
from utils.validation import validate_archive_file, validate_downloaded_media

logger = logging.getLogger(__name__)

# Root directory of the project
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        self.failed_ids: List[Tuple[int, int]] = []  # [(chat_id, message_id), ...]
        self.downloaded_ids: List[Tuple[int, int]] = []  # [(chat_id, message_id), ...]
        self.downloaded_files: Dict[Tuple[int, int], str] = {}  # {(chat_id, message_id): file_path}
        self.i18n = get_i18n(self.config.get("language", "ru"))
        self.media_filter = MediaFilter(self.config)

        # Настроить логирование
        configure_logging(self.config)

        # Инициализировать сохранение истории, если включено
        self.history_manager: Optional[MessageHistory] = None
        download_settings = self.config.get("download_settings", {})
        if download_settings.get("download_message_history", False):
            base_dir = download_settings.get("base_directory") or PROJECT_ROOT
            history_format = download_settings.get("history_format", "json")
            history_dir = download_settings.get("history_directory", "history")
            self.history_manager = MessageHistory(base_dir, history_format, history_dir, config_manager)

    def _can_download(
        self,
        _type: str,
        file_formats: dict,
        file_format: Optional[str]
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

    def _find_file_in_archive(
        self,
        chat_id: int,
        file_name: str,
        file_size: Optional[int] = None
    ) -> Optional[str]:
        """
        Найти файл в архиве чата по имени и размеру.

        Parameters
        ----------
        chat_id: int
            ID чата.
        file_name: str
            Имя файла (базовое имя, без пути).
        file_size: Optional[int]
            Размер файла в байтах.

        Returns
        -------
        Optional[str]
            Путь к найденному файлу из архива или None.
        """
        if not self.history_manager:
            return None

        try:
            import json
            from utils.history import _archive_chat_id_for_path

            path_id = _archive_chat_id_for_path(chat_id)
            ext = "txt" if self.history_manager.history_format == "txt" else "jsonl"
            archive_path = os.path.join(
                self.history_manager.history_path, f"chat_{path_id}.{ext}"
            )

            if not os.path.exists(archive_path):
                return None

            # Искать в JSONL архиве
            if ext == "jsonl":
                base_name = os.path.basename(file_name)
                with open(archive_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            if not isinstance(obj, dict):
                                continue

                            # Проверить имя файла
                            archived_name = obj.get("file_name") or ""
                            archived_path = obj.get("downloaded_file") or ""
                            archived_size = obj.get("file_size")

                            # Сравнить базовое имя файла
                            archived_base = (
                                os.path.basename(archived_path)
                                if archived_path
                                else os.path.basename(archived_name)
                            )

                            if archived_base and archived_base == base_name:
                                # Если размер указан - проверить совпадение
                                if file_size is not None and archived_size is not None:
                                    if archived_size != file_size:
                                        continue

                                # Если есть путь к файлу - проверить существование
                                if archived_path and os.path.exists(archived_path):
                                    return archived_path

                        except Exception:
                            continue

        except Exception:
            pass

        return None

    def _check_existing_file(
        self,
        file_path: str,
        media_type: str,
        expected_size: Optional[int] = None,
        chat_id: Optional[int] = None,
        file_name: Optional[str] = None,
    ) -> Optional[str]:
        """
        Проверить существующий файл ДО скачивания.

        Сначала проверяет файл по ожидаемому пути, затем ищет в архиве чата
        по имени и размеру.

        Если файл существует и валиден (размер, сигнатуры) - возвращает путь к нему,
        иначе None (нужно скачивать).

        Parameters
        ----------
        file_path: str
            Ожидаемый путь к файлу.
        media_type: str
            Тип медиа (video, audio, photo, document и т.д.).
        expected_size: Optional[int]
            Ожидаемый размер файла в байтах.
        chat_id: Optional[int]
            ID чата (для поиска в архиве).
        file_name: Optional[str]
            Имя файла (для поиска в архиве по имени).

        Returns
        -------
        Optional[str]
            Путь к существующему валидному файлу или None.
        """
        # Сначала проверить файл по ожидаемому пути
        if self._is_exist(file_path):
            validate_downloads = self.config.get("download_settings", {}).get(
                "validate_downloads", True
            )
            if validate_downloads:
                if not validate_downloaded_media(
                    file_path,
                    media_type,
                    expected_size,
                    check_signature=True,
                ):
                    # Файл существует, но невалидный - нужно перескачать
                    pass  # Продолжить поиск в архиве
                else:
                    # Файл существует и валиден - можно использовать
                    return file_path

        # Если файл не найден по пути, искать в архиве по имени и размеру
        if chat_id is not None and file_name is not None:
            archived_path = self._find_file_in_archive(
                chat_id, file_name, expected_size
            )
            if archived_path and self._is_exist(archived_path):
                validate_downloads = self.config.get("download_settings", {}).get(
                    "validate_downloads", True
                )
                if validate_downloads:
                    if not validate_downloaded_media(
                        archived_path,
                        media_type,
                        expected_size,
                        check_signature=True,
                    ):
                        # Файл из архива невалидный
                        return None
                # Файл из архива существует и валиден
                return archived_path

        return None

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
        base_dir = download_directory if download_directory else PROJECT_ROOT

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
                        file_name_base = sanitize_filename(attr.file_name)
                        break
            if file_name_base == "":
                if hasattr(media_obj, "id"):
                    file_name_base = f"{_type}_{media_obj.id}"
            file_name = os.path.join(base_dir, _type, file_name_base)
        return file_name, file_format



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
                _type = get_media_type(message)
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

                    # Использовать chat_id из конфига (установлен в begin_import_chat),
                    # а не из message.chat.id, т.к. message.chat.id может быть без префикса -100
                    # для супергрупп/каналов, а в конфиге хранится правильный chat_id с префиксом
                    chat_id = self.config.get("chat_id", 0)
                    if chat_id == 0:
                        # Fallback: если chat_id не установлен в конфиге, использовать из сообщения
                        chat_id = message.chat.id if message.chat else 0

                    # Умный skip ДО скачивания: проверяем существующий файл
                    # Сначала по пути, затем в архиве по имени и размеру
                    base_file_name = os.path.basename(file_name)
                    existing_file = self._check_existing_file(
                        file_name,
                        _type,
                        file_size if file_size else None,
                        chat_id=chat_id,
                        file_name=base_file_name,
                    )
                    if existing_file:
                        # Файл уже существует и валиден - пропускаем скачивание
                        logger.info(
                            self.i18n.t("file_already_exists", path=existing_file, id=message.id)
                        )
                        download_path = existing_file
                    else:
                        # Файл отсутствует или невалиден - скачиваем
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

                        validate_downloads = self.config.get("download_settings", {}).get(
                            "validate_downloads", True
                        )
                        if validate_downloads and not validate_downloaded_media(
                            download_path,
                            _type,
                            file_size if file_size else None,
                            check_signature=True,
                        ):
                            logger.warning(
                                self.i18n.t(
                                    "validation_failed_media",
                                    path=download_path,
                                    id=message.id,
                                )
                            )
                            self.failed_ids.append((chat_id, message.id))
                        else:
                            logger.info(self.i18n.t("downloaded", path=download_path))
                            logger.debug("Успешно загружено сообщение %s", message.id)
                            self.downloaded_files[(chat_id, message.id)] = download_path
                            self.downloaded_ids.append((chat_id, message.id))
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
                    # Использовать chat_id из конфига для консистентности
                    chat_id = self.config.get("chat_id", 0)
                    if chat_id == 0:
                        chat_id = message.chat.id if message.chat else 0
                    self.failed_ids.append((chat_id, message.id))
            except TimeoutError:
                logger.warning(
                    self.i18n.t("timeout_error", id=message.id)
                )
                await asyncio.sleep(5)
                if retry == 2:
                    logger.error(
                        self.i18n.t("timeout_skip", id=message.id)
                    )
                    # Использовать chat_id из конфига для консистентности
                    chat_id = self.config.get("chat_id", 0)
                    if chat_id == 0:
                        chat_id = message.chat.id if message.chat else 0
                    self.failed_ids.append((chat_id, message.id))
            except FileMigrateError as e:
                # Файл в другом DC, переключение может занять время
                dc_num = getattr(e, "new_dc", "?")
                logger.warning(
                    self.i18n.t("file_migrate_error", id=message.id, dc=dc_num)
                )
                # Увеличенная задержка для переключения DC (10 сек)
                await asyncio.sleep(10)
                if retry == 2:
                    logger.error(
                        self.i18n.t("file_migrate_error_skip", id=message.id, dc=dc_num)
                    )
                    # Использовать chat_id из конфига для консистентности
                    chat_id = self.config.get("chat_id", 0)
                    if chat_id == 0:
                        chat_id = message.chat.id if message.chat else 0
                    self.failed_ids.append((chat_id, message.id))
            except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
                # Обрыв соединения при переключении DC или сетевые проблемы
                error_str = str(e)
                if "bytes read" in error_str or "connection" in error_str.lower():
                    logger.warning(
                        self.i18n.t("connection_error", id=message.id, error=error_str)
                    )
                    # Увеличенная задержка для восстановления соединения
                    await asyncio.sleep(10)
                    if retry == 2:
                        logger.error(
                            self.i18n.t("connection_error_skip", id=message.id, error=error_str)
                        )
                        # Использовать chat_id из конфига для консистентности
                        chat_id = self.config.get("chat_id", 0)
                        if chat_id == 0:
                            chat_id = message.chat.id if message.chat else 0
                        self.failed_ids.append((chat_id, message.id))
                else:
                    # Другие OSError/ConnectionError - пробрасываем в общий Exception handler
                    raise
            except Exception as e:
                logger.error(
                    self.i18n.t("download_exception", id=message.id, error=str(e)),
                    exc_info=True,
                )
                # Использовать chat_id из конфига для консистентности
                chat_id = self.config.get("chat_id", 0)
                if chat_id == 0:
                    chat_id = message.chat.id if message.chat else 0
                self.failed_ids.append((chat_id, message.id))
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
        first_message = messages[0] if messages else None
        chat = getattr(first_message, "chat", None) if first_message else None

        # Использовать chat_id из конфига (установлен в begin_import_chat),
        # а не из message.chat.id, т.к. message.chat.id может быть без префикса -100
        # для супергрупп/каналов, а в конфиге хранится правильный chat_id с префиксом
        _chat_id = self.config.get("chat_id", 0)
        if _chat_id == 0 and chat:
            # Fallback: если chat_id не установлен в конфиге, использовать из сообщения
            _chat_id = chat.id if chat else 0

        logger.info(
            "Обработана партия: chat_id=%s, сообщений=%s",
            _chat_id,
            len(messages),
        )

        # Сохранить историю ВСЕХ сообщений, если включено
        if self.history_manager and messages:
            chat_title = getattr(chat, "title", None) if chat else None
            # Создать словарь только для текущего чата
            # Используем _chat_id из конфига для правильного сопоставления
            chat_files = {
                msg_id: path
                for (cid, msg_id), path in self.downloaded_files.items()
                if cid == _chat_id
            }
            logger.info(
                "Сохранение истории в архив: chat_id=%s, сообщений=%s",
                _chat_id,
                len(messages),
            )
            self.history_manager.save_batch(
                messages, _chat_id, chat_title, chat_files
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

        # Получить текущие ids_to_retry для этого чата
        current_ids_to_retry = []
        if "chats" in config and isinstance(config["chats"], list):
            for chat in config["chats"]:
                if chat.get("chat_id") == chat_id:
                    current_ids_to_retry = chat.get("ids_to_retry", [])
                    break
        else:
            # Старая структура конфига
            current_ids_to_retry = config.get("ids_to_retry", [])

        # Фильтровать downloaded_ids и failed_ids только для текущего чата
        chat_downloaded_ids = [msg_id for (cid, msg_id) in self.downloaded_ids if cid == chat_id]
        chat_failed_ids = [msg_id for (cid, msg_id) in self.failed_ids if cid == chat_id]

        # Обновить ids_to_retry: убрать успешно загруженные, добавить неудачные
        ids_to_retry = list(set(current_ids_to_retry) - set(chat_downloaded_ids)) + chat_failed_ids

        # Обновить состояние чата
        self.config_manager.update_chat_state(
            chat_id, config.get("last_read_message_id", 0), ids_to_retry
        )
        self.config_manager.save()
        logger.info(self.i18n.t("updated_message_id"))

    async def begin_import_chat(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self,
        client: TelegramClient,
        chat_id: int,
        chat_title: Optional[str] = None,
        pagination_limit: int = 100,
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
        from datetime import date, datetime, timezone

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

        # Если история включена, но файл архива отсутствует или не проходит жёсткую проверку,
        # сбрасываем last_read_message_id и пересоздаём архив.
        # Приоритет: путь без минуса (abs). Затем — с минусом (для совместимости со старыми архивами).
        download_settings = self.config.get("download_settings", {})
        if (
            self.history_manager is not None
            and download_settings.get("history_rebuild_if_missing", False)
            and isinstance(last_read_message_id, int)
            and last_read_message_id > 0
        ):
            try:
                from utils.history import _archive_chat_id_for_path

                ext = "txt" if self.history_manager.history_format == "txt" else "jsonl"
                base = self.history_manager.history_path
                fmt = "txt" if ext == "txt" else "jsonl"
                path_id = _archive_chat_id_for_path(chat_id)
                candidates = [os.path.join(base, f"chat_{path_id}.{ext}")]
                # Для совместимости со старыми архивами: если chat_id отрицательный,
                # проверяем также путь с минусом
                if chat_id != 0 and path_id != chat_id:
                    candidates.append(os.path.join(base, f"chat_{chat_id}.{ext}"))
                logger.debug(
                    "Проверка архива чата: chat_id=%s, кандидаты=%s",
                    chat_id,
                    candidates,
                )
                validate_archives = download_settings.get("validate_archives", True)
                archive_ok = False
                found_path: Optional[str] = None
                for path in candidates:
                    if not os.path.exists(path):
                        continue
                    found_path = path
                    if validate_archives:
                        archive_ok = validate_archive_file(path, fmt)
                    else:
                        archive_ok = True
                    break
                if archive_ok and found_path:
                    logger.info(
                        "Архив чата найден: chat_id=%s, path=%s",
                        chat_id,
                        found_path,
                    )
                if not archive_ok:
                    display_path = found_path or candidates[0]
                    logger.warning(
                        "История включена, но архив чата отсутствует или не прошёл проверку: "
                        "chat_id=%s, path=%s. Сбрасываю last_read_message_id и пересоздаю архив.",
                        chat_id,
                        display_path,
                    )
                    last_read_message_id = 0
            except Exception:
                # Фолбэк: не ломаем загрузку из-за проблем с FS
                pass

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

        try:
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
                    # Проверка max_messages только для текущего чата
                    chat_downloaded_count = sum(
                        1 for (cid, _) in self.downloaded_ids if cid == chat_id
                    )
                    if max_messages and chat_downloaded_count >= max_messages:
                        break
                    pagination_count = 0
                    messages_list = []
                    messages_list.append(message)
                    self.config["last_read_message_id"] = last_read_message_id
                    self.update_config(chat_id)
        except ValueError as e:
            logger.error(f"Ошибка при получении сообщений для чата {chat_id}: {e}")
            logger.error("Проверьте правильность chat_id в конфигурации")
            return
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

        # Очистить downloaded_files для текущего чата для экономии памяти
        keys_to_remove = [key for key in self.downloaded_files.keys() if key[0] == chat_id]
        for key in keys_to_remove:
            del self.downloaded_files[key]

        # Очистить downloaded_ids и failed_ids для текущего чата
        self.downloaded_ids = [(cid, msg_id) for (cid, msg_id) in self.downloaded_ids if cid != chat_id]
        self.failed_ids = [(cid, msg_id) for (cid, msg_id) in self.failed_ids if cid != chat_id]
