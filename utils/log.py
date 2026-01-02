"""Модуль для обработки логов."""
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional


class LogFilter(logging.Filter):
    """
    Пользовательский фильтр логов.

    Игнорирует логи из определенных функций.
    """

    # pylint: disable = W0221
    def filter(self, record):
        if record.funcName in ("invoke"):
            return False
        return True


def setup_file_logging(
    file_path: str = "downloads.log",
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> Optional[RotatingFileHandler]:
    """
    Настроить логирование в файл с ротацией.

    Parameters
    ----------
    file_path: str
        Путь к файлу логов.
    level: str
        Уровень логирования (DEBUG, INFO, WARNING, ERROR).
    max_bytes: int
        Максимальный размер файла перед ротацией в байтах.
    backup_count: int
        Количество резервных файлов для хранения.

    Returns
    -------
    Optional[RotatingFileHandler]
        Обработчик файлового логирования или None при ошибке.
    """
    try:
        # Создать директорию, если не существует
        log_dir = os.path.dirname(file_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # Преобразовать строку уровня в константу
        log_level = getattr(logging, level.upper(), logging.INFO)

        # Создать обработчик с ротацией
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)

        # Формат для файлового логирования
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)

        # Добавить обработчик к корневому логгеру
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)

        return file_handler
    except Exception as e:
        logging.error(f"Не удалось настроить файловое логирование: {e}")
        return None


def configure_logging(
    config: Optional[dict] = None,
) -> None:
    """
    Настроить логирование на основе конфигурации.

    Parameters
    ----------
    config: Optional[dict]
        Конфигурация с настройками логирования.
    """
    if config is None:
        return

    logging_config = config.get("logging", {})
    file_logging = logging_config.get("file_logging", {})

    if file_logging.get("enabled", False):
        file_path = file_logging.get("file_path", "downloads.log")
        level = file_logging.get("level", "INFO")
        max_bytes = file_logging.get("max_bytes", 10 * 1024 * 1024)
        backup_count = file_logging.get("backup_count", 5)
        setup_file_logging(file_path, level, max_bytes, backup_count)
