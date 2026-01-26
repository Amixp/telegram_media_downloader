#!/usr/bin/env python3
"""
Удаление потерянных файлов из папок типов медиа.

Находит файлы в папках video/, photo/, document/, audio/, voice/, video_note/,
которые не упоминаются ни в одном архиве чата (JSONL файлах) и удаляет их.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Set

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Типы медиа папок для сканирования
MEDIA_TYPES = ["video", "photo", "document", "audio", "voice", "video_note"]

# Системные файлы для игнорирования
IGNORED_FILES = {".gitkeep", ".DS_Store", "Thumbs.db", ".gitignore"}


@dataclass(frozen=True)
class CleanupResult:
    """Результат очистки потерянных файлов."""

    archived_files_count: int
    media_files_count: int
    orphaned_files_count: int
    removed_files_count: int
    errors_count: int


def _iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    """
    Итератор по строкам JSONL файла.

    Parameters
    ----------
    path: str
        Путь к JSONL файлу.

    Yields
    ------
    Dict[str, Any]
        Словарь с данными сообщения из JSONL.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception as e:
        logger.warning("Ошибка при чтении JSONL файла %s: %s", path, e)


def _collect_archived_files(base_directory: str, history_directory: str) -> Set[str]:
    """
    Собрать все пути к файлам, упомянутые в архивах чатов.

    Parameters
    ----------
    base_directory: str
        Базовая директория (где лежит history/).
    history_directory: str
        Имя папки истории внутри base_directory.

    Returns
    -------
    Set[str]
        Множество абсолютных путей к файлам из архивов.
    """
    history_path = os.path.join(base_directory, history_directory)
    if not os.path.exists(history_path):
        logger.warning("Папка истории не найдена: %s", history_path)
        return set()

    archived_files: Set[str] = set()
    jsonl_pattern = os.path.join(history_path, "chat_*.jsonl")
    jsonl_files = glob.glob(jsonl_pattern)

    logger.info("Найдено JSONL архивов: %d", len(jsonl_files))

    for jsonl_path in jsonl_files:
        file_count = 0
        for msg in _iter_jsonl(jsonl_path):
            downloaded_file = msg.get("downloaded_file")
            if downloaded_file and isinstance(downloaded_file, str):
                # Нормализовать путь (убрать лишние слеши, разрешить относительные пути)
                normalized_path = os.path.normpath(downloaded_file)
                # Если путь относительный, сделать абсолютным относительно base_directory
                if not os.path.isabs(normalized_path):
                    normalized_path = os.path.join(base_directory, normalized_path)
                archived_files.add(normalized_path)
                file_count += 1

        if file_count > 0:
            logger.debug("Архив %s: найдено %d файлов", os.path.basename(jsonl_path), file_count)

    logger.info("Всего файлов в архивах: %d", len(archived_files))
    return archived_files


def _scan_media_directories(base_directory: str) -> Set[str]:
    """
    Просканировать папки типов медиа и собрать все файлы.

    Parameters
    ----------
    base_directory: str
        Базовая директория для сканирования.

    Returns
    -------
    Set[str]
        Множество абсолютных путей к файлам в папках медиа.
    """
    if not os.path.exists(base_directory):
        logger.warning("Базовая директория не найдена: %s", base_directory)
        return set()

    media_files: Set[str] = set()

    for media_type in MEDIA_TYPES:
        media_dir = os.path.join(base_directory, media_type)
        if not os.path.exists(media_dir):
            logger.debug("Папка %s не найдена, пропуск", media_dir)
            continue

        # Рекурсивно найти все файлы
        pattern = os.path.join(media_dir, "**", "*")
        found_files = glob.glob(pattern, recursive=True)

        for file_path in found_files:
            # Пропустить директории и системные файлы
            if os.path.isdir(file_path):
                continue
            if os.path.basename(file_path) in IGNORED_FILES:
                continue
            # Пропустить символические ссылки
            if os.path.islink(file_path):
                logger.debug("Пропущена символическая ссылка: %s", file_path)
                continue

            media_files.add(os.path.normpath(file_path))

        logger.debug("Папка %s: найдено %d файлов", media_type, len([f for f in found_files if os.path.isfile(f)]))

    logger.info("Всего файлов в папках медиа: %d", len(media_files))
    return media_files


def _find_orphaned_files(archived_files: Set[str], media_files: Set[str]) -> Set[str]:
    """
    Найти потерянные файлы (есть в папках медиа, но нет в архивах).

    Parameters
    ----------
    archived_files: Set[str]
        Множество файлов из архивов.
    media_files: Set[str]
        Множество файлов в папках медиа.

    Returns
    -------
    Set[str]
        Множество потерянных файлов.
    """
    # Нормализовать пути для сравнения
    archived_normalized = {os.path.normpath(f) for f in archived_files}
    media_normalized = {os.path.normpath(f) for f in media_files}

    orphaned = media_normalized - archived_normalized
    logger.info("Найдено потерянных файлов: %d", len(orphaned))
    return orphaned


def _remove_files(file_paths: Set[str], dry_run: bool = True) -> tuple[int, int]:
    """
    Удалить список файлов.

    Parameters
    ----------
    file_paths: Set[str]
        Множество путей к файлам для удаления.
    dry_run: bool
        Если True, только показать что будет удалено, не удалять реально.

    Returns
    -------
    tuple[int, int]
        (количество удалённых файлов, количество ошибок).
    """
    removed_count = 0
    errors_count = 0

    if dry_run:
        logger.info("[DRY RUN] Будет удалено %d файлов:", len(file_paths))
        for file_path in sorted(file_paths):
            logger.info("  %s", file_path)
        logger.info("Для реального удаления запустите без --dry-run")
        return 0, 0

    logger.info("Удаление %d файлов...", len(file_paths))

    for file_path in sorted(file_paths):
        try:
            if not os.path.exists(file_path):
                logger.debug("Файл уже не существует: %s", file_path)
                continue

            os.remove(file_path)
            removed_count += 1
            logger.debug("Удалён: %s", file_path)

        except PermissionError as e:
            errors_count += 1
            logger.error("Ошибка прав доступа при удалении %s: %s", file_path, e)
        except OSError as e:
            errors_count += 1
            logger.error("Ошибка при удалении %s: %s", file_path, e)
        except Exception as e:
            errors_count += 1
            logger.error("Неожиданная ошибка при удалении %s: %s", file_path, e)

    logger.info("Удалено файлов: %d, ошибок: %d", removed_count, errors_count)
    return removed_count, errors_count


def cleanup_orphaned_files(
    base_directory: str,
    history_directory: str = "history",
    dry_run: bool = True,
) -> CleanupResult:
    """
    Найти и удалить потерянные файлы.

    Parameters
    ----------
    base_directory: str
        Базовая директория (где лежит history/ и папки медиа).
    history_directory: str
        Имя папки истории внутри base_directory.
    dry_run: bool
        Если True, только показать что будет удалено, не удалять реально.

    Returns
    -------
    CleanupResult
        Результат операции очистки.
    """
    # Проверка существования базовой директории
    if not os.path.exists(base_directory):
        raise ValueError(f"Базовая директория не существует: {base_directory}")

    if not os.path.isdir(base_directory):
        raise ValueError(f"Базовая директория не является папкой: {base_directory}")

    logger.info("Начало сканирования: base_directory=%s, history_directory=%s", base_directory, history_directory)

    # Собрать файлы из архивов
    archived_files = _collect_archived_files(base_directory, history_directory)

    # Просканировать папки медиа
    media_files = _scan_media_directories(base_directory)

    # Найти потерянные файлы
    orphaned_files = _find_orphaned_files(archived_files, media_files)

    # Удалить потерянные файлы
    removed_count, errors_count = _remove_files(orphaned_files, dry_run=dry_run)

    return CleanupResult(
        archived_files_count=len(archived_files),
        media_files_count=len(media_files),
        orphaned_files_count=len(orphaned_files),
        removed_files_count=removed_count,
        errors_count=errors_count,
    )


def main() -> int:
    """Главная функция скрипта."""
    parser = argparse.ArgumentParser(
        description="Удаление потерянных файлов из папок типов медиа (не упомянутых в архивах чатов)"
    )
    parser.add_argument(
        "--base-directory",
        required=True,
        help="Базовая директория (download_settings.base_directory, где лежит history/)",
    )
    parser.add_argument(
        "--history-directory",
        default="history",
        help="Имя папки истории внутри base-directory (default: history)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Режим проверки без удаления (по умолчанию включен)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Реальное удаление файлов (отключает --dry-run)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Подробный вывод (уровень DEBUG)",
    )

    args = parser.parse_args()

    # Настроить уровень логирования
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Определить режим работы
    dry_run = not args.force
    if not dry_run:
        logger.warning("ВНИМАНИЕ: Режим реального удаления включен!")

    try:
        result = cleanup_orphaned_files(
            base_directory=args.base_directory,
            history_directory=args.history_directory,
            dry_run=dry_run,
        )

        # Вывести итоговую статистику
        print("\n" + "=" * 60)
        print("Статистика:")
        print(f"  Файлов в архивах: {result.archived_files_count}")
        print(f"  Файлов в папках медиа: {result.media_files_count}")
        print(f"  Потерянных файлов: {result.orphaned_files_count}")
        if not dry_run:
            print(f"  Удалено файлов: {result.removed_files_count}")
            print(f"  Ошибок: {result.errors_count}")
        print("=" * 60)

        return 0

    except ValueError as e:
        logger.error("Ошибка конфигурации: %s", e)
        return 1
    except Exception as e:
        logger.exception("Неожиданная ошибка: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
