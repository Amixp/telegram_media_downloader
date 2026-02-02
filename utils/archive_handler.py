import os
import shutil
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

class ArchiveHandler:
    """Утилита для автоматической распаковки архивов."""

    def __init__(self, settings: Dict[str, Any]):
        """
        Инициализация обработчика архивов.

        Parameters
        ----------
        settings: Dict[str, Any]
            Настройки распаковки из конфигурации.
        """
        self.enabled = settings.get("extract_archives", False)
        self.delete_after = settings.get("delete_after_extraction", False)
        self.extraction_dir = settings.get("extraction_directory", "")
        self.supported_exts = settings.get("supported_extensions", ["zip", "tar", "gz", "bz2", "xz"])

    async def extract_if_archive(self, file_path: str) -> bool:
        """
        Проверить, является ли файл архивом и распаковать его, если это так.
        Выполняется асинхронно в отдельном потоке.

        Parameters
        ----------
        file_path: str
            Путь к файлу.

        Returns
        -------
        bool
            True, если файл был успешно распакован, иначе False.
        """
        if not self.enabled or not os.path.exists(file_path):
            return False

        if not self._is_supported_archive(file_path):
            return False

        try:
            # Определяем путь для распаковки
            extract_to = self._get_extraction_path(file_path)
            os.makedirs(extract_to, exist_ok=True)

            logger.info("Распаковка архива %s в %s...", file_path, extract_to)

            import asyncio
            loop = asyncio.get_running_loop()
            # Выполняем блокирующую распаковку в пуле потоков
            await loop.run_in_executor(None, lambda: shutil.unpack_archive(file_path, extract_to))

            if self.delete_after:
                logger.debug("Удаление архива %s после распаковки", file_path)
                os.remove(file_path)

            return True
        except Exception as e:
            logger.error("Ошибка при распаковке архива %s: %s", file_path, e)
            return False

    def _is_supported_archive(self, file_path: str) -> bool:
        """Проверить, поддерживается ли расширение файла."""
        ext = file_path.lower().split('.')[-1]
        # Проверяем на составные расширения типа .tar.gz
        if file_path.lower().endswith('.tar.gz'):
            ext = 'gz'
        elif file_path.lower().endswith('.tar.bz2'):
            ext = 'bz2'
        elif file_path.lower().endswith('.tar.xz'):
            ext = 'xz'

        return ext in self.supported_exts

    def _get_extraction_path(self, file_path: str) -> str:
        """Определить директорию для распаковки."""
        if self.extraction_dir:
            # Если задана общая директория для распаковки,
            # создаем в ней подпапку с именем архива для каждого файла
            base_name = os.path.basename(file_path)
            # Убираем расширение (включая сложные типа .tar.gz)
            for ext in ['.tar.gz', '.tar.bz2', '.tar.xz', '.zip', '.tar', '.gz', '.bz2', '.xz']:
                if base_name.lower().endswith(ext):
                    folder_name = base_name[:-len(ext)]
                    break
            else:
                folder_name = os.path.splitext(base_name)[0]

            return os.path.join(self.extraction_dir, folder_name)
        else:
            # По умолчанию распаковываем в подпапку рядом с архивом
            # Убираем расширение для имени папки
            for ext in ['.tar.gz', '.tar.bz2', '.tar.xz', '.zip', '.tar', '.gz', '.bz2', '.xz']:
                if file_path.lower().endswith(ext):
                    return file_path[:-len(ext)]
            return os.path.splitext(file_path)[0]
