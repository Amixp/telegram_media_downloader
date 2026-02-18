"Модуль загрузчика медиа."
import asyncio
import json
import logging
import os
import shutil
from typing import AsyncIterator, Dict, List, Optional, Tuple, Union

from telethon import TelegramClient
from telethon.errors import FileMigrateError, FileReferenceExpiredError, FloodWaitError
from telethon.tl.types import (
    Document,
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    Photo,
)
from tqdm import tqdm
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TaskID,
)

from utils.config import ConfigManager
from utils.file_management import get_file_hash, get_next_name, manage_duplicate_file
from utils.clickhouse_db import ClickHouseMetadataDB
from utils.history import MessageHistory
from utils.i18n import get_i18n
from utils.archive_handler import ArchiveHandler
from utils.log import configure_logging
from utils.media_utils import get_media_type, sanitize_filename
from utils.validation import validate_archive_file, validate_downloaded_media
from utils.filter import MediaFilter

logger = logging.getLogger(__name__)

# Root directory of the project
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DownloadManager:
    """Класс для управления загрузкой медиа из Telegram."""

    def __init__(self, config_manager: ConfigManager, clickhouse_db: Optional["ClickHouseMetadataDB"] = None):
        """
        Инициализация DownloadManager.

        Parameters
        ----------
        config_manager: ConfigManager
            Менеджер конфигурации.
        clickhouse_db: Optional[ClickHouseMetadataDB]
            Опционально переданный экземпляр БД (для совместного использования с логгером).
        """
        self.config_manager = config_manager
        self.config = config_manager.config
        self.failed_ids: List[Tuple[int, int]] = []  # [(chat_id, message_id), ...]
        self.permanent_skip_ids: List[Tuple[int, int]] = []  # FileReferenceExpired после 3 попыток — не retry
        self.downloaded_ids: List[Tuple[int, int]] = []  # [(chat_id, message_id), ...]
        self.downloaded_files: Dict[Tuple[int, int], str] = {}  # {(chat_id, message_id): file_path}
        # ClickHouse (передан снаружи или создаём свой)
        self.clickhouse_db = (
            clickhouse_db
            if clickhouse_db is not None
            else ClickHouseMetadataDB(self.config.get("clickhouse", {}))
        )
        # Archive Extraction
        download_settings = self.config.get("download_settings", {})
        self.archive_handler = ArchiveHandler(download_settings.get("archive_settings", {}))
        self.i18n = get_i18n(self.config.get("language", "ru"))
        self.media_filter = MediaFilter(self.config)
        self.web_app = None # Will be set if web server is running
        # Кэш последнего description для web progress, чтобы не спамить одним и тем же
        self._web_last_description: Dict[str, str] = {}
        # Метаданные чата (description, username) для CH, ключ — chat_id
        self._chat_entity_meta: Dict[int, Dict[str, str]] = {}

        # Настроить логирование
        configure_logging(self.config)

        # Инициализировать сохранение истории, если включено
        self.history_manager: Optional[MessageHistory] = None
        download_settings = self.config.get("download_settings", {})
        if download_settings.get("download_message_history", False):
            base_dir = download_settings.get("base_directory") or PROJECT_ROOT
            history_format = download_settings.get("history_format", "json")
            history_dir = download_settings.get("history_directory", "history")
            ch_cfg = self.config.get("clickhouse", {})
            self.history_manager = MessageHistory(
                base_dir,
                history_format,
                history_dir,
                config_manager,
                clickhouse_db=self.clickhouse_db,
                clickhouse_primary_source=ch_cfg.get("primary_source", False),
            )

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
        message_id: Optional[int] = None,
    ) -> Optional[str]:
        """
        Проверить существующий файл ДО скачивания.

        При use_db_file_verification и ClickHouse: сначала запрос в БД,
        проверка хеша (быстро) или validate_downloaded_media при пустом хеше.
        Иначе: проверка по пути, затем поиск в JSONL архиве.

        Parameters
        ----------
        file_path: str
            Ожидаемый путь к файлу.
        media_type: str
            Тип медиа (video, audio, photo, document и т.д.).
        expected_size: Optional[int]
            Ожидаемый размер файла в байтах.
        chat_id: Optional[int]
            ID чата (для поиска в архиве / БД).
        file_name: Optional[str]
            Имя файла (для поиска в архиве по имени).
        message_id: Optional[int]
            ID сообщения (для запроса в ClickHouse).

        Returns
        -------
        Optional[str]
            Путь к существующему валидному файлу или None.
        """
        download_settings = self.config.get("download_settings", {})
        validate_downloads = download_settings.get("validate_downloads", True)
        use_db_verification = download_settings.get("use_db_file_verification", False)

        # БД-верификация: быстрый пропуск по хешу
        if (
            use_db_verification
            and self.clickhouse_db
            and getattr(self.clickhouse_db, "enabled", False)
            and chat_id is not None
            and message_id is not None
        ):
            info = self.clickhouse_db.get_message_file_info(chat_id, message_id)
            if info:
                db_path, db_size, db_hash = info
                if db_path and self._is_exist(db_path):
                    if db_hash:
                        try:
                            disk_hash = get_file_hash(db_path)
                            if disk_hash == db_hash:
                                return db_path
                            # Хеш не совпал — файл повреждён, fallback
                        except (OSError, IOError):
                            pass
                    else:
                        # Нет хеша в БД — полная валидация
                        if not validate_downloads or validate_downloaded_media(
                            db_path,
                            media_type,
                            db_size or expected_size,
                            check_signature=True,
                        ):
                            return db_path

        # Проверить файл по ожидаемому пути
        if self._is_exist(file_path):
            if validate_downloads:
                if not validate_downloaded_media(
                    file_path,
                    media_type,
                    expected_size,
                    check_signature=True,
                ):
                    pass
                else:
                    return file_path
            else:
                return file_path

        # Искать в архиве по имени и размеру
        if chat_id is not None and file_name is not None:
            archived_path = self._find_file_in_archive(
                chat_id, file_name, expected_size
            )
            if archived_path and self._is_exist(archived_path):
                if validate_downloads:
                    if not validate_downloaded_media(
                        archived_path,
                        media_type,
                        expected_size,
                        check_signature=True,
                    ):
                        return None
                return archived_path

        return None

    def _progress_callback(
        self,
        current,
        total,
        progress_bar=None,
        progress=None,
        task_id=None,
        web_task_id=None,
        web_description=None,
    ):
        """
        Callback для обновления прогресс-бара.

        Parameters
        ----------
        current: int
            Текущее количество загруженных байт.
        total: int
            Общее количество байт.
        progress_bar: Optional[tqdm]
            Экземпляр tqdm.
        progress: Optional[Progress]
            Экземпляр rich.Progress.
        task_id: Optional[TaskID]
            ID задачи rich.
        web_task_id: Optional[Any]
            ID задачи для веб-дашборда (может отличаться от rich task_id).
        web_description: Optional[str]
            Короткое описание/имя файла для отображения в веб-дашборде.
        """
        if progress and task_id:
            progress.update(task_id, completed=current, total=total)
        elif progress_bar:
            # Обновляем total без сброса прогресса, чтобы избежать дёрганья
            # при небольших изменениях размера файла от Telegram API
            if progress_bar.total != total:
                progress_bar.total = total
                # НЕ вызываем reset() - это сбрасывает текущий прогресс

            # Defensive check for mock objects in tests
            n = getattr(progress_bar, "n", 0)
            if not isinstance(n, (int, float)):
                n = 0
            progress_bar.update(current - n)

        # Web Update
        if self.web_app:
            # Не блокируем цикл ожиданиями обновлений веб-интерфейса
            tid = web_task_id if web_task_id is not None else (task_id if task_id is not None else "cli")
            tid_str = str(tid)
            desc_to_send = None
            if web_description is not None:
                prev = self._web_last_description.get(tid_str)
                if prev != web_description:
                    self._web_last_description[tid_str] = web_description
                    desc_to_send = web_description

            asyncio.create_task(
                self.web_app.update_download(
                    tid,
                    description=desc_to_send,
                    completed=current,
                    total=total,
                )
            )

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

    def _get_file_display_name_from_message(self, message: Message) -> str:
        """Короткое имя файла из сообщения для логов/БД."""
        media = getattr(message, "document", None) or getattr(message, "photo", None)
        if media and hasattr(media, "attributes"):
            for attr in media.attributes:
                if hasattr(attr, "file_name"):
                    return getattr(attr, "file_name", "") or ""
        return f"msg_{message.id}"

    def _record_failed(
        self, chat_id: int, message: Message, error_message: str = "", add_to_retry: bool = True
    ) -> None:
        """
        Записать неудачную загрузку в ClickHouse.
        add_to_retry: добавлять ли в failed_ids (и далее в ids_to_retry).
        Для FileReferenceExpiredError после 3 попыток — False: refetch не помогает,
        повтор при следующем запуске тоже не поможет.
        """
        if add_to_retry:
            self.failed_ids.append((chat_id, message.id))
        if self.clickhouse_db.enabled:
            self.clickhouse_db.save_file_download(
                chat_id,
                message.id,
                "failed",
                chat_title=str(self.config.get("chat_title", "")),
                file_name=self._get_file_display_name_from_message(message),
                file_path="",
                file_size=0,
                error_message=(error_message or "")[:2048],
            )

    def _get_file_size(self, message: Message) -> int:
        """Получить размер файла сообщения."""
        if not message.media:
            return 0

        if isinstance(message.media, MessageMediaPhoto):
            # Самый большой размер
            sizes = getattr(message.media.photo, "sizes", [])
            max_size = 0
            for s in sizes:
                s_size = getattr(s, "size", 0)
                if isinstance(s_size, int) and s_size > max_size:
                    max_size = s_size
            return max_size
        elif isinstance(message.media, MessageMediaDocument):
            return message.media.document.size
        return 0

    async def _iter_download_chunks(
        self,
        client: TelegramClient,
        media: Union[MessageMediaDocument, MessageMediaPhoto],
        offset: int,
        request_size: int,
        message_id: int,
    ) -> AsyncIterator[bytes]:
        """
        Итератор по чанкам загрузки с таймаутом на каждый чанк.

        При отсутствии данных дольше download_chunk_timeout выбрасывает TimeoutError,
        что даёт возможность выйти из зависшей загрузки и быстрее реагировать на Ctrl+C.
        """
        ds = self.config.get("download_settings", {}) or {}
        chunk_timeout = ds.get("download_chunk_timeout", 300)
        inter_chunk_delay = ds.get("inter_chunk_delay_sec") or 0
        it = client.iter_download(media, offset=offset, request_size=request_size)
        first_chunk = True
        while True:
            try:
                chunk = await asyncio.wait_for(anext(it), timeout=chunk_timeout)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                logger.warning(
                    "Загрузка сообщения %s зависла: нет данных более %s сек "
                    "(download_chunk_timeout). Возможные причины: сеть, ограничения Telegram, прокси.",
                    message_id,
                    chunk_timeout,
                )
                raise TimeoutError(
                    f"Download stalled for message {message_id} (no data for {chunk_timeout}s)"
                ) from None
            if not first_chunk and inter_chunk_delay > 0:
                await asyncio.sleep(inter_chunk_delay)
            first_chunk = False
            yield chunk

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



    async def _update_web_status(self):
        """Обновить состояние для веб-дашборда."""
        if not self.web_app:
            return

        from web.app import PROGRESS_STATE, manager
        # Здесь мы могли бы собирать данные из внутренних счетчиков
        # Для начала просто триггерим броадкаст текущего состояния
        try:
            await manager.broadcast(json.dumps(PROGRESS_STATE))
        except Exception as e:
            logger.debug(f"Web broadcast failed: {e}")

    async def download_media(  # pylint: disable=too-many-locals
        self,
        client: TelegramClient,
        message: Message,
        media_types: List[str],
        file_formats: dict,
        download_directory: Optional[str] = None,
        progress: Optional[Progress] = None,
        task_id: Optional[TaskID] = None,
    ) -> int:
        """
        Загрузить медиа в соответствии с типом медиа.

        Parameters
        ----------
        client: TelegramClient
            Клиент для взаимодействия с API Telegram.
        message: Message
            Объект сообщения Telegram.
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
        progress: Optional[Progress]
            Экземпляр Progress для отображения прогресса.
        task_id: Optional[TaskID]
            ID задачи для отображения прогресса.

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
                    if not file_size and hasattr(media_obj, "sizes"):
                        # Для фото берем размер самого крупного варианта
                        large_size = [s for s in media_obj.sizes if hasattr(s, "size")]
                        if large_size:
                            file_size = max(s.size for s in large_size)
                    # Использовать оригинальное имя файла, если доступно, иначе сгенерированное
                    display_name = getattr(
                        media_obj, "file_name", os.path.basename(file_name)
                    )
                    desc = self.i18n.t("downloading", file=display_name)
                    logger.info(desc)

                    # own_task_id будет создан непосредственно перед началом загрузки
                    own_task_id = None
                    web_task_id: Optional[str] = None
                    web_description: Optional[str] = None

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

                    # Для веб-дашборда: стабильный task_id и короткое описание (имя файла)
                    web_task_id = f"{chat_id}_{message.id}"
                    web_description = str(display_name)

                    # Умный skip ДО скачивания: проверяем существующий файл
                    # При use_db_file_verification — по БД (хеш), иначе по пути и JSONL
                    base_file_name = os.path.basename(file_name)
                    existing_file = self._check_existing_file(
                        file_name,
                        _type,
                        file_size if file_size else None,
                        chat_id=chat_id,
                        file_name=base_file_name,
                        message_id=message.id,
                    )
                    download_path = None
                    skipped_as_existing = False  # уже проверен в _check_existing_file, не дублировать валидацию
                    if existing_file:
                        # Файл уже существует и валиден - пропускаем скачивание
                        logger.info(
                            self.i18n.t("file_already_exists", path=existing_file, id=message.id)
                        )
                        download_path = existing_file
                        skipped_as_existing = True
                        self.downloaded_files[(chat_id, message.id)] = download_path
                    elif (
                        self.clickhouse_db.enabled
                        and self.clickhouse_db.is_message_skipped_stale(chat_id, message.id)
                    ):
                        # Ранее зависло/таймаут — пропускаем без попытки загрузки
                        logger.info(
                            "Сообщение %s ранее зависло/таймаут, пропуск (БД)",
                            message.id,
                        )
                        return message.id
                    else:
                        # Файл отсутствует или невалиден - скачиваем
                        if self._is_exist(file_name):
                            file_name = get_next_name(file_name)

                        # Настройки докачки
                        download_settings = self.config.get("download_settings", {})
                        resumable = download_settings.get("resumable_downloads", True)
                        cache_dir = download_settings.get("cache_directory", ".download_cache")

                        if resumable:
                            # Создать директорию кэша если её нет
                            if not os.path.isabs(cache_dir):
                                cache_dir = os.path.join(PROJECT_ROOT, cache_dir)
                            if not os.path.exists(cache_dir):
                                os.makedirs(cache_dir, exist_ok=True)

                            # Формируем имя временного файла (по ID сообщения и чата для уникальности)
                            part_file = os.path.join(cache_dir, f"{chat_id}_{message.id}.part")
                            current_size = os.path.getsize(part_file) if os.path.exists(part_file) else 0

                            # Проверка: если вдруг файл на диске больше чем в ТГ (ошибка?), начинаем заново
                            if current_size >= file_size and file_size > 0:
                                if current_size > file_size:
                                    logger.warning("Размер кэша (%s) больше размера файла (%s), сброс", current_size, file_size)
                                    if os.path.exists(part_file):
                                        os.remove(part_file)
                                    current_size = 0
                                else:
                                    # Файл уже полностью скачан в кэше, но не переименован
                                    logger.debug("Файл %s уже полностью в кэше", message.id)

                            mode = "ab" if current_size > 0 else "wb"
                            loop = asyncio.get_running_loop()

                            # Создать task непосредственно перед началом загрузки
                            if progress and own_task_id is None:
                                own_task_id = progress.add_task(desc, total=file_size, completed=current_size, visible=True)

                            if progress and own_task_id is not None:
                                progress.update(own_task_id, description=desc, total=file_size, completed=current_size, visible=True)
                                # Сразу показать текущий файл в веб-дашборде (даже если current_size=0)
                                self._progress_callback(
                                    current_size,
                                    file_size,
                                    progress=progress,
                                    task_id=own_task_id,
                                    web_task_id=web_task_id,
                                    web_description=web_description,
                                )
                                with open(part_file, mode) as f:
                                    async for chunk in self._iter_download_chunks(
                                        client,
                                        message.media,
                                        current_size,
                                        1024 * 1024,
                                        message.id,
                                    ):
                                        await loop.run_in_executor(
                                            None, (lambda c: lambda: f.write(c))(chunk)
                                        )
                                        current_size += len(chunk)
                                        self._progress_callback(
                                            current_size,
                                            file_size,
                                            progress=progress,
                                            task_id=own_task_id,
                                            web_task_id=web_task_id,
                                            web_description=web_description,
                                        )
                            else:
                                with tqdm(
                                    total=file_size, unit="B", unit_scale=True, desc=desc, initial=current_size
                                ) as pbar:
                                    # Сразу показать текущий файл в веб-дашборде (даже если current_size=0)
                                    self._progress_callback(
                                        current_size,
                                        file_size,
                                        progress_bar=pbar,
                                        web_task_id=web_task_id,
                                        web_description=web_description,
                                    )
                                    with open(part_file, mode) as f:
                                        async for chunk in self._iter_download_chunks(
                                            client,
                                            message.media,
                                            current_size,
                                            1024 * 1024,
                                            message.id,
                                        ):
                                            await loop.run_in_executor(
                                                None, (lambda c: lambda: f.write(c))(chunk)
                                            )
                                            current_size += len(chunk)
                                            self._progress_callback(
                                                current_size,
                                                file_size,
                                                progress_bar=pbar,
                                                web_task_id=web_task_id,
                                                web_description=web_description,
                                            )

                            # Переименовать после успешной загрузки (в пуле потоков, чтобы не блокировать event loop)
                            await loop.run_in_executor(None, lambda: shutil.move(part_file, file_name))
                            download_path = file_name
                            # Telethon/iter_download может не дать последнего тика ровно в total — добиваем явно
                            final_total = file_size if file_size > 0 else current_size
                            if final_total > 0:
                                self._progress_callback(
                                    final_total,
                                    final_total,
                                    progress=progress if (progress and own_task_id is not None) else None,
                                    task_id=own_task_id,
                                    web_task_id=web_task_id,
                                    web_description=web_description,
                                )
                        else:
                            # Скачать файл без докачки
                            # Создать task непосредственно перед началом загрузки
                            if progress and own_task_id is None:
                                own_task_id = progress.add_task(desc, total=file_size, completed=0, visible=True)

                            if progress and own_task_id is not None:
                                progress.update(own_task_id, description=desc, total=file_size, completed=0, visible=True)
                                # Сразу показать текущий файл в веб-дашборде
                                self._progress_callback(
                                    0,
                                    file_size,
                                    progress=progress,
                                    task_id=own_task_id,
                                    web_task_id=web_task_id,
                                    web_description=web_description,
                                )
                                download_path = await client.download_media(
                                    message,
                                    file=file_name,
                                    progress_callback=lambda c, t: self._progress_callback(
                                        c,
                                        t,
                                        progress=progress,
                                        task_id=own_task_id,
                                        web_task_id=web_task_id,
                                        web_description=web_description,
                                    ),
                                )
                            else:
                                with tqdm(
                                    total=file_size, unit="B", unit_scale=True, desc=desc
                                ) as pbar:
                                    # pylint: disable=cell-var-from-loop
                                    # Сразу показать текущий файл в веб-дашборде
                                    self._progress_callback(
                                        0,
                                        file_size,
                                        progress_bar=pbar,
                                        web_task_id=web_task_id,
                                        web_description=web_description,
                                    )
                                    download_path = await client.download_media(
                                        message,
                                        file=file_name,
                                        progress_callback=lambda c, t: self._progress_callback(
                                            c,
                                            t,
                                            progress_bar=pbar,
                                            web_task_id=web_task_id,
                                            web_description=web_description,
                                        ),
                                    )

                    # Telethon не всегда вызывает callback на 100% — добиваем явно при успешной загрузке
                    if download_path:
                        try:
                            final_total = file_size if file_size > 0 else os.path.getsize(download_path)
                        except Exception:
                            final_total = file_size
                        if final_total and final_total > 0:
                            self._progress_callback(
                                final_total,
                                final_total,
                                progress=progress if (progress and own_task_id is not None) else None,
                                task_id=own_task_id,
                                web_task_id=web_task_id,
                                web_description=web_description,
                            )

                    # Всегда проверять дубликаты после загрузки (если включено) — в пуле потоков (чтение/хеш файла)
                    if download_path and skip_duplicates:
                        _loop = asyncio.get_running_loop()
                        download_path = await _loop.run_in_executor(
                            None,
                            lambda p=download_path: manage_duplicate_file(p, enabled=True),
                        )

                    if download_path:

                        validate_downloads = self.config.get("download_settings", {}).get(
                            "validate_downloads", True
                        )
                        # Валидировать медиафайл после загрузки (если включено). Пропуск, если файл взят как существующий — уже проверен в _check_existing_file
                        if validate_downloads and not skipped_as_existing:
                            # Блокирующая валидация в пуле потоков
                            loop = asyncio.get_running_loop()
                            is_valid = await loop.run_in_executor(
                                None,
                                lambda: validate_downloaded_media(download_path, message)
                            )

                            if not is_valid:
                                logger.error(
                                    self.i18n.t("validation_failed_media", id=message.id, path=download_path)
                                )
                                if os.path.exists(download_path):
                                    await loop.run_in_executor(
                                        None, lambda p=download_path: os.remove(p)
                                    )
                                self._record_failed(chat_id, message, "validation_failed")
                                break

                            # Распаковка если это архив (теперь async)
                            await self.archive_handler.extract_if_archive(download_path)

                        logger.info(self.i18n.t("downloaded", path=download_path))
                        logger.debug("Успешно загружено сообщение %s", message.id)
                        self.downloaded_files[(chat_id, message.id)] = download_path
                        self.downloaded_ids.append((chat_id, message.id))
                        if self.clickhouse_db.enabled:
                            fhash = ""
                            try:
                                if download_path and os.path.isfile(download_path):
                                    fhash = get_file_hash(download_path)
                            except (OSError, IOError):
                                pass
                            self.clickhouse_db.save_file_download(
                                chat_id,
                                message.id,
                                "downloaded",
                                chat_title=str(self.config.get("chat_title", "")),
                                file_name=display_name,
                                file_path=download_path,
                                file_size=file_size or 0,
                                file_hash=fhash,
                            )

                        # Скрыть task после завершения загрузки
                        if progress and own_task_id is not None:
                            progress.update(own_task_id, visible=False)
                break
            except FileReferenceExpiredError:
                logger.warning(
                    self.i18n.t("file_reference_expired", id=message.id)
                )
                messages = await client.get_messages(message.chat.id, ids=message.id)
                if messages is not None:
                    message = messages[0] if isinstance(messages, list) else messages
                if retry == 2:
                    logger.error(
                        self.i18n.t("file_reference_expired_skip", id=message.id)
                    )
                    chat_id = self.config.get("chat_id", 0)
                    if chat_id == 0:
                        chat_id = message.chat.id if message.chat else 0
                    # Не добавлять в ids_to_retry: refetch 3 раза не помог — файл недоступен,
                    # повтор при следующем запуске не поможет (то же самое).
                    self.permanent_skip_ids.append((chat_id, message.id))
                    self._record_failed(
                        chat_id, message, "file_reference_expired", add_to_retry=False
                    )
                    if progress and own_task_id is not None:
                        progress.update(own_task_id, visible=False)
                    if self.web_app and web_task_id is not None and file_size:
                        self._progress_callback(
                            file_size, file_size,
                            progress=progress, task_id=own_task_id,
                            web_task_id=web_task_id, web_description=web_description,
                        )
            except FloodWaitError as e:
                logger.warning(
                    self.i18n.t("flood_wait_error", id=message.id, seconds=e.seconds)
                )
                await asyncio.sleep(e.seconds)
            except TimeoutError:
                logger.warning(
                    self.i18n.t("timeout_error", id=message.id)
                )
                await asyncio.sleep(5)
                if retry == 2:
                    logger.error(
                        self.i18n.t("timeout_skip", id=message.id)
                    )
                    chat_id = self.config.get("chat_id", 0)
                    if chat_id == 0:
                        chat_id = message.chat.id if message.chat else 0
                    self._record_failed(chat_id, message, "timeout", add_to_retry=False)
                    if progress and own_task_id is not None:
                        progress.update(own_task_id, visible=False)
                    if self.web_app and web_task_id is not None and file_size:
                        self._progress_callback(
                            file_size, file_size,
                            progress=progress, task_id=own_task_id,
                            web_task_id=web_task_id, web_description=web_description,
                        )
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
                    chat_id = self.config.get("chat_id", 0)
                    if chat_id == 0:
                        chat_id = message.chat.id if message.chat else 0
                    self._record_failed(chat_id, message, f"file_migrate dc={dc_num}")
                    if progress and own_task_id is not None:
                        progress.update(own_task_id, visible=False)
                    if self.web_app and web_task_id is not None and file_size:
                        self._progress_callback(
                            file_size, file_size,
                            progress=progress, task_id=own_task_id,
                            web_task_id=web_task_id, web_description=web_description,
                        )
            except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
                # Обрыв соединения при переключении DC или сетевые проблемы
                error_str = str(e)
                if "bytes read" in error_str or "connection" in error_str.lower():
                    logger.warning(
                        self.i18n.t("connection_error", id=message.id, error=error_str)
                    )
                    # Подсказка: уменьшение параллелизма часто устраняет повторяющиеся обрывы
                    max_p = self.config.get("download_settings", {}).get("max_parallel_downloads", 2)
                    if max_p is None or (isinstance(max_p, int) and max_p > 1):
                        logger.info(
                            "При повторяющихся обрывах установите download_settings.max_parallel_downloads: 1"
                        )
                    # Увеличенная задержка для восстановления соединения
                    await asyncio.sleep(10)
                    if retry == 2:
                        logger.error(
                            self.i18n.t("connection_error_skip", id=message.id, error=error_str)
                        )
                        chat_id = self.config.get("chat_id", 0)
                        if chat_id == 0:
                            chat_id = message.chat.id if message.chat else 0
                        self._record_failed(chat_id, message, error_str)
                        if progress and own_task_id is not None:
                            progress.update(own_task_id, visible=False)
                        if self.web_app and web_task_id is not None and file_size:
                            self._progress_callback(
                                file_size, file_size,
                                progress=progress, task_id=own_task_id,
                                web_task_id=web_task_id, web_description=web_description,
                            )
                else:
                    # Другие OSError/ConnectionError - пробрасываем в общий Exception handler
                    raise
            except ValueError as e:
                # Telethon: "Request was unsuccessful N time(s)" после исчерпания request_retries
                # при FloodWait GetFileRequest — нужен длинный cooldown
                err_msg = str(e)
                if "Request was unsuccessful" in err_msg:
                    logger.warning(
                        "GetFileRequest flood: исчерпаны ретраи Telethon (антифрод), "
                        "ожидание 60 сек перед повтором для сообщения[%s]...",
                        message.id,
                    )
                    await asyncio.sleep(60)
                    chat_id = self.config.get("chat_id", 0)
                    if chat_id == 0:
                        chat_id = message.chat.id if message.chat else 0
                    if retry < 2:
                        logger.warning(
                            self.i18n.t("download_exception_refetch", id=message.id)
                        )
                        try:
                            refetched = await client.get_messages(
                                message.chat.id if message.chat else chat_id,
                                ids=message.id,
                            )
                            if refetched is not None:
                                message = refetched[0] if isinstance(refetched, list) else refetched
                        except Exception:
                            pass
                        await asyncio.sleep(2)
                    else:
                        self._record_failed(chat_id, message, err_msg)
                        if progress and own_task_id is not None:
                            progress.update(own_task_id, visible=False)
                        if self.web_app and web_task_id is not None and file_size:
                            self._progress_callback(
                                file_size, file_size,
                                progress=progress, task_id=own_task_id,
                                web_task_id=web_task_id, web_description=web_description,
                            )
                        break
                else:
                    raise
            except Exception as e:
                logger.error(
                    self.i18n.t("download_exception", id=message.id, error=str(e)),
                    exc_info=True,
                )
                chat_id = self.config.get("chat_id", 0)
                if chat_id == 0:
                    chat_id = message.chat.id if message.chat else 0
                # При неизвестной ошибке — обновить сообщение (refetch) и повторить;
                # в клиенте файл часто грузится, т.к. там актуальная file reference.
                if retry < 2:
                    logger.warning(
                        self.i18n.t("download_exception_refetch", id=message.id)
                    )
                    try:
                        refetched = await client.get_messages(
                            message.chat.id if message.chat else chat_id,
                            ids=message.id,
                        )
                        if refetched is not None:
                            message = refetched[0] if isinstance(refetched, list) else refetched
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                else:
                    self._record_failed(chat_id, message, str(e))
                    if progress and own_task_id is not None:
                        progress.update(own_task_id, visible=False)
                    if self.web_app and web_task_id is not None and file_size:
                        self._progress_callback(
                            file_size, file_size,
                            progress=progress, task_id=own_task_id,
                            web_task_id=web_task_id, web_description=web_description,
                        )
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
        progress: Optional[Progress] = None,
        task_id: Optional[TaskID] = None,
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
        progress: Optional[Progress]
            Экземпляр Progress для отображения прогресса.
        task_id: Optional[TaskID]
            ID задачи для отображения прогресса.

        Returns
        -------
        int
            Максимальное значение из списка ID сообщений.
        """
        async def download_with_semaphore(message):
            if semaphore:
                async with semaphore:
                    return await self.download_media(
                        client, message, media_types, file_formats, download_directory,
                        progress=progress, task_id=task_id
                    )
            else:
                return await self.download_media(
                    client, message, media_types, file_formats, download_directory,
                    progress=progress, task_id=task_id
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

        # Сначала ClickHouse (flush): проверка дубликатов архива идёт по CH.
        # При прерывании до flush архив уже записан — дубли при следующем запуске.
        # Порядок: CH flush → архив.
        if self.clickhouse_db.enabled:
            chat_title = getattr(chat, "title", None) if chat else None
            for message in messages:
                file_path = self.downloaded_files.get((_chat_id, message.id), "")
                fhash = ""
                if file_path and os.path.isfile(file_path):
                    try:
                        fhash = get_file_hash(file_path)
                    except (OSError, IOError):
                        pass
                entities_json = ""
                if hasattr(message, "entities") and message.entities:
                    entities_list = []
                    for entity in message.entities:
                        ent = {
                            "offset": entity.offset,
                            "length": entity.length,
                            "type": type(entity).__name__,
                        }
                        if hasattr(entity, "url") and entity.url:
                            ent["url"] = entity.url
                        entities_list.append(ent)
                    entities_json = json.dumps(entities_list, ensure_ascii=False)
                msg_data = {
                    "chat_id": _chat_id,
                    "message_id": message.id,
                    "date": message.date,
                    "text": message.message or "",
                    "media_type": get_media_type(message),
                    "file_path": file_path,
                    "file_size": self._get_file_size(message),
                    "sender_id": message.sender_id or 0,
                    "chat_title": chat_title or "",
                    "file_hash": fhash,
                    "entities": entities_json,
                }
                await self.clickhouse_db.save_message(msg_data)

            # Обновить информацию о чате
            # Считаем общее количество сообщений и размер (приблизительно для текущего пакета)
            current_chat_size = sum(self._get_file_size(m) for m in messages)
            meta = self._chat_entity_meta.get(_chat_id, {})
            await self.clickhouse_db.update_chat_info(
                _chat_id,
                chat_title or f"Chat {_chat_id}",
                len(messages),
                current_chat_size,
                description=meta.get("description") or None,
                username=meta.get("username") or None,
            )
            await self.clickhouse_db.flush()

        # Сохранить историю ВСЕХ сообщений, если включено (после CH flush)
        if self.history_manager is not None:
            chat_title = getattr(chat, "title", None) if chat else None
            downloaded_files = {
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
                messages, _chat_id, chat_title, downloaded_files
            )

        last_message_id: int = max(message_ids)
        return last_message_id

    def update_config(self, chat_id: Optional[int] = None) -> None:
        """
        Обновить конфигурацию (last_read_message_id, ids_to_retry для чата).
        
        Примечание: При включенном ClickHouse состояние хранится в БД,
        запись в config.yaml не производится.

        Причины роста ids_to_retry (очередь повторов):
        - Нестабильное соединение: wrong session ID, обрывы, ConnectionError → сообщения
          попадают в failed_ids и в ids_to_retry.
        - Истёкшая file reference (FileReferenceExpiredError) при долгих загрузках.
        - Таймауты, FloodWait, миграция DC (FileMigrateError).
        - Один и тот же id при повторных сбоях добавлялся бы многократно — делаем
          дедупликацию и ограничение max_ids_to_retry.

        Parameters
        ----------
        chat_id: Optional[int]
            ID чата для обновления состояния. Если None, используется chat_id из конфига.
        """
        # Если ClickHouse включен - состояние хранится в БД, не в config.yaml
        if self.clickhouse_db and self.clickhouse_db.enabled:
            logger.debug(
                "Пропуск update_config для чата %s - состояние хранится в ClickHouse",
                chat_id
            )
            return
        
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
        chat_permanent_skip_ids = [
            msg_id for (cid, msg_id) in self.permanent_skip_ids if cid == chat_id
        ]

        # Обновить ids_to_retry: убрать успешно загруженные, permanent_skip (FileReferenceExpired
        # после 3 попыток), добавить неудачные.
        # Дедупликация (dict.fromkeys) — иначе одно и то же сообщение при повторных сбоях
        # добавляется каждый запуск и очередь растёт без ограничения.
        remaining = list(
            set(current_ids_to_retry)
            - set(chat_downloaded_ids)
            - set(chat_permanent_skip_ids)
        )
        ids_to_retry = list(dict.fromkeys(remaining + chat_failed_ids))

        # Ограничить размер очереди повторов, чтобы при нестабильной сети не раздувать партию
        max_ids_to_retry = self.config.get("download_settings", {}).get("max_ids_to_retry", 500)
        if isinstance(max_ids_to_retry, int) and max_ids_to_retry > 0 and len(ids_to_retry) > max_ids_to_retry:
            ids_to_retry = ids_to_retry[-max_ids_to_retry:]
            logger.warning(
                "ids_to_retry обрезан до %s записей (max_ids_to_retry). Старые будут пропущены.",
                max_ids_to_retry,
            )

        if len(ids_to_retry) > 200:
            logger.warning(
                "Большая очередь повторов для chat_id=%s: %s. Возможные причины: "
                "нестабильное соединение (wrong session ID, обрывы), истёкшие file reference, "
                "лимиты Telegram; уменьшите max_parallel_downloads или max_ids_to_retry.",
                chat_id,
                len(ids_to_retry),
            )

        # Обновить состояние чата в config.yaml
        self.config_manager.update_chat_state(
            chat_id, config.get("last_read_message_id", 0), ids_to_retry
        )
        self.config_manager.save()
        logger.info(self.i18n.t("updated_message_id"))

    def _get_chat_state_from_config(self, chat_id: int) -> Tuple[int, List[int]]:
        """Получить last_read_message_id и ids_to_retry из config.yaml.

        Parameters
        ----------
        chat_id : int
            ID чата

        Returns
        -------
        Tuple[int, List[int]]
            (last_read_message_id, ids_to_retry)
        """
        last_read_message_id = 0
        ids_to_retry = []

        if "chats" in self.config and isinstance(self.config["chats"], list):
            chat_config = next(
                (c for c in self.config["chats"] if c.get("chat_id") == chat_id), None
            )
            if chat_config:
                last_read_message_id = chat_config.get("last_read_message_id", 0)
                ids_to_retry = chat_config.get("ids_to_retry", [])
        else:
            # Старая структура - использовать общий chat_id
            if self.config.get("chat_id") == chat_id:
                last_read_message_id = self.config.get("last_read_message_id", 0)
                ids_to_retry = self.config.get("ids_to_retry", [])

        return last_read_message_id, ids_to_retry

    def _ensure_chat_in_db(self, chat_id: int, chat_title: str = "") -> None:
        """Убедиться, что чат есть в ClickHouse БД.

        Parameters
        ----------
        chat_id : int
            ID чата
        chat_title : str
            Название чата (опционально)
        """
        if not self.clickhouse_db or not self.clickhouse_db.enabled:
            return

        # Добавить чат в БД, если его там нет
        if not self.clickhouse_db.chat_exists(chat_id):
            # Попытаться взять title из config.yaml (legacy)
            config_title = chat_title
            if "chats" in self.config and isinstance(self.config["chats"], list):
                chat_config = next(
                    (c for c in self.config["chats"] if c.get("chat_id") == chat_id),
                    None
                )
                if chat_config and chat_config.get("title"):
                    config_title = chat_config["title"]

            self.clickhouse_db.ensure_chat_in_db(chat_id, config_title)
            logger.debug("Чат %s добавлен в БД", chat_id)

    async def begin_import_chat(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self,
        client: TelegramClient,
        chat_id: int,
        chat_title: Optional[str],
        pagination_limit: int,
        progress: Optional[Progress] = None,
        task_id: Optional[TaskID] = None,
    ):
        """
        Запустить импорт чата.

        Parameters
        ----------
        client: TelegramClient
            Клиент для взаимодействия с API Telegram.
        chat_id: int
            ID чата для импорта.
        chat_title: Optional[str]
            Название чата.
        pagination_limit: int
            Количество сообщений для обработки в одном пакете.
        progress: Optional[Progress]
            Экземпляр Progress для отображения прогресса.
        task_id: Optional[TaskID]
            ID задачи для отображения прогресса.
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

        # Настройка параллельных загрузок.
        # Один MTProto-клиент = одно TCP-соединение. Слишком много параллельных загрузок
        # приводит к обрывам ("Server closed the connection", "0 bytes read").
        # Если в конфиге нет ключа — используем 2 (безопасный дефолт), не None (без лимита).
        max_parallel = self.config.get("download_settings", {}).get(
            "max_parallel_downloads", 2
        )
        if max_parallel is not None and max_parallel < 1:
            max_parallel = 2
        semaphore = asyncio.Semaphore(max_parallel) if max_parallel else None

        # Получить типы медиа
        media_types = self.config.get("media_types", [])
        if "all" in media_types:
            media_types = ["audio", "document", "photo", "video", "voice", "video_note"]

        # Получить last_read_message_id из ClickHouse (приоритет) или config.yaml (fallback/миграция)
        last_read_message_id = 0
        ids_to_retry = []

        if self.clickhouse_db and self.clickhouse_db.enabled:
            # Убедиться, что чат есть в БД
            self._ensure_chat_in_db(chat_id, chat_title or "")

            # Приоритет: запросить из БД
            db_last_id = self.clickhouse_db.get_last_processed_message_id(chat_id)
            if db_last_id is not None:
                last_read_message_id = db_last_id
                logger.info(
                    "Продолжение загрузки чата %s с message_id=%s (из БД)",
                    chat_id,
                    last_read_message_id,
                )
            else:
                # Нет данных в БД - попытка миграции из config.yaml
                config_last_id, config_retry = self._get_chat_state_from_config(chat_id)
                if config_last_id > 0:
                    last_read_message_id = config_last_id
                    logger.info(
                        "Миграция: использование last_read_message_id=%s из config.yaml для чата %s",
                        last_read_message_id,
                        chat_id,
                    )

            # Получить ids_to_retry из file_downloads
            max_retry = self.config.get("download_settings", {}).get("max_ids_to_retry", 500)
            ids_to_retry = self.clickhouse_db.get_retry_message_ids(chat_id, limit=max_retry)
            if ids_to_retry:
                logger.info(
                    "Запланировано %s сообщений для повторной загрузки (из БД)",
                    len(ids_to_retry),
                )
        else:
            # Fallback: использовать config.yaml
            last_read_message_id, ids_to_retry = self._get_chat_state_from_config(chat_id)
            if last_read_message_id > 0:
                logger.info(
                    "Продолжение загрузки чата %s с message_id=%s (из config.yaml, ClickHouse отключен)",
                    chat_id,
                    last_read_message_id,
                )

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

        # Получить entity чата для description и username (каналы/супергруппы)
        try:
            entity = await client.get_entity(chat_id)
            desc = ""
            uname = ""
            from telethon.tl.types import Channel, Chat
            if isinstance(entity, Channel):
                desc = getattr(entity, "about", None) or ""
                uname = getattr(entity, "username", None) or ""
            elif isinstance(entity, Chat):
                desc = getattr(entity, "about", None) or ""
            if desc or uname:
                self._chat_entity_meta[chat_id] = {"description": desc or "", "username": uname or ""}
        except Exception as e:
            logger.debug("Не удалось получить entity чата %s: %s", chat_id, e)

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

        # Обрабатывать ids_to_retry партиями по pagination_limit, чтобы не создавать одну огромную партию
        if ids_to_retry:
            logger.info(self.i18n.t("retrying"))
            skipped_messages: list = await client.get_messages(  # type: ignore
                chat_id, ids=ids_to_retry
            )
            for chunk_start in range(0, len(skipped_messages), pagination_limit):
                batch = skipped_messages[chunk_start : chunk_start + pagination_limit]
                last_read_message_id = await self.process_messages(
                    client,
                    batch,
                    media_types,
                    self.config.get("file_formats", {}),
                    download_directory,
                    semaphore,
                    progress=progress,
                    task_id=task_id,
                )
                if self.web_app:
                    import web.app as web_app
                    await web_app.update_chat(chat_id, status="Processing")
                self.config["last_read_message_id"] = last_read_message_id
                self.update_config(chat_id)
                chat_downloaded_count = sum(
                    1 for (cid, _) in self.downloaded_ids if cid == chat_id
                )
                if max_messages and chat_downloaded_count >= max_messages:
                    # Достигнут лимит — выходим, остаток ids_to_retry останется в конфиге
                    self.config["last_read_message_id"] = last_read_message_id
                    self.update_config(chat_id)
                    keys_to_remove = [k for k in self.downloaded_files if k[0] == chat_id]
                    for k in keys_to_remove:
                        del self.downloaded_files[k]
                    self.downloaded_ids = [(c, m) for (c, m) in self.downloaded_ids if c != chat_id]
                    self.failed_ids = [(c, m) for (c, m) in self.failed_ids if c != chat_id]
                    self.permanent_skip_ids = [
                        (c, m) for (c, m) in self.permanent_skip_ids if c != chat_id
                    ]
                    return

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
                        progress=progress,
                        task_id=task_id,
                    )
                    # Web Update
                    if self.web_app:
                        import web.app as web_app
                        await web_app.update_chat(chat_id, status="Processing")

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
                progress=progress,
                task_id=task_id,
            )

        self.config["last_read_message_id"] = last_read_message_id
        self.update_config(chat_id)

        # Очистить downloaded_files для текущего чата для экономии памяти
        keys_to_remove = [key for key in self.downloaded_files.keys() if key[0] == chat_id]
        for key in keys_to_remove:
            del self.downloaded_files[key]

        # Очистить downloaded_ids, failed_ids, permanent_skip_ids для текущего чата
        self.downloaded_ids = [(cid, msg_id) for (cid, msg_id) in self.downloaded_ids if cid != chat_id]
        self.failed_ids = [(cid, msg_id) for (cid, msg_id) in self.failed_ids if cid != chat_id]
        self.permanent_skip_ids = [
            (cid, msg_id) for (cid, msg_id) in self.permanent_skip_ids if cid != chat_id
        ]

    async def begin_import_all_chats(
        self,
        client: TelegramClient,
        queue_entries: List[dict],
        pagination_limit: int
    ):
        """
        Запустить импорт всех чатов из очереди с общим прогрессом.

        Parameters
        ----------
        client: TelegramClient
            Клиент для взаимодействия с API Telegram.
        queue_entries: List[dict]
            Очередь чатов для загрузки.
        pagination_limit: int
            Лимит пагинации.
        """
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
        ) as progress:
            overall_task = progress.add_task(
                f"[cyan]{self.i18n.t('processing_chats', count=len(queue_entries))}",
                total=len(queue_entries)
            )

            # Web Update
            if self.web_app:
                import web.app as web_app
                await web_app.update_overall(total=len(queue_entries), completed=0, status="Processing")

            chats_done = 0
            for c in queue_entries:
                chat_id = c["chat_id"]
                chat_title = c.get("title", "")

                chat_task = progress.add_task(
                    f"[green]{chat_title or chat_id}",
                    total=None,
                    visible=False # Будет показан в download_media
                )

                logger.info(
                    "Начало загрузки для чата: %s (chat_id=%s)",
                    chat_title or chat_id,
                    chat_id,
                )

                # Временно установить chat_id и chat_title для этого чата (логи/БД)
                self.config["chat_id"] = chat_id
                self.config["chat_title"] = chat_title or ""

                try:
                    if self.web_app:
                        import web.app as web_app
                        await web_app.update_chat(chat_id, title=chat_title or str(chat_id), status="Processing")
                        await web_app.update_overall(completed=chats_done, status=f"Processing: {chat_title or chat_id}")

                    await self.begin_import_chat(
                        client, chat_id, chat_title, pagination_limit,
                        progress=progress, task_id=chat_task
                    )
                except Exception as e:
                    logger.error(f"Ошибка при обработке чата {chat_title or chat_id}: {e}", exc_info=True)
                    if self.web_app:
                        import web.app as web_app
                        await web_app.update_chat(chat_id, title=chat_title or str(chat_id), status="Error")
                finally:
                    progress.update(chat_task, visible=False)
                    progress.advance(overall_task)
                    chats_done += 1
                    if self.web_app:
                        import web.app as web_app
                        await web_app.update_chat(chat_id, title=chat_title or str(chat_id), status="Done")
                        await web_app.update_overall(completed=chats_done, status="Processing")

            if self.failed_ids:
                logger.info(
                    self.i18n.t("download_failed", count=len(set(self.failed_ids)))
                    + "\n"
                    + self.i18n.t("failed_ids_added")
                )

            # Финальный сброс буферов ClickHouse
            await self.flush()
            if self.web_app:
                import web.app as web_app
                await web_app.update_overall(status="Done")

    async def flush(self):
        """Принудительно записывает все буферы (ClickHouse и др.) в БД."""
        if self.clickhouse_db:
            await self.clickhouse_db.flush()

    def close(self):
        """Закрывает все открытые ресурсы (соединения БД и др.)."""
        if self.clickhouse_db:
            self.clickhouse_db.close()
