"""Утилиты для обработки загруженных файлов."""

import glob
import os
import pathlib
from hashlib import md5
from typing import Dict, Optional


# Кеш для хешей файлов
_hash_cache: Dict[str, str] = {}


def get_next_name(file_path: str) -> str:
    """
    Get next available name to download file.

    Parameters
    ----------
    file_path: str
        Absolute path of the file for which next available name to
        be generated.

    Returns
    -------
    str
        Absolute path of the next available name for the file.
    """
    posix_path = pathlib.Path(file_path)
    counter: int = 1
    new_file_name: str = os.path.join("{0}", "{1}-copy{2}{3}")
    while os.path.isfile(
        new_file_name.format(
            posix_path.parent,
            posix_path.stem,
            counter,
            "".join(posix_path.suffixes),
        )
    ):
        counter += 1
    return new_file_name.format(
        posix_path.parent,
        posix_path.stem,
        counter,
        "".join(posix_path.suffixes),
    )


def _get_file_hash(file_path: str) -> str:
    """
    Получить MD5 хеш файла с использованием кеша.

    Parameters
    ----------
    file_path: str
        Путь к файлу.

    Returns
    -------
    str
        MD5 хеш файла.
    """
    if file_path in _hash_cache:
        return _hash_cache[file_path]

    # pylint: disable = R1732
    with open(file_path, "rb") as f:
        file_hash = md5(f.read()).hexdigest()
    _hash_cache[file_path] = file_hash
    return file_hash


def manage_duplicate_file(
    file_path: str, enabled: bool = True
) -> str:
    """
    Проверить, является ли файл дубликатом.

    Сравнивает MD5 хеш файлов с паттерном имени копии
    и удаляет, если MD5 хеш совпадает.

    Parameters
    ----------
    file_path: str
        Абсолютный путь к файлу, для которого нужно управлять дубликатами.
    enabled: bool
        Включена ли проверка дубликатов. По умолчанию True.

    Returns
    -------
    str
        Абсолютный путь к файлу после обработки дубликатов.
    """
    if not enabled:
        return file_path

    if not os.path.exists(file_path):
        return file_path

    posix_path = pathlib.Path(file_path)
    file_base_name: str = "".join(posix_path.stem.split("-copy")[0])
    name_pattern: str = f"{posix_path.parent}/{file_base_name}*"
    # Причина использования `str.translate()`
    # https://stackoverflow.com/q/22055500/6730439
    old_files: list = glob.glob(
        name_pattern.translate({ord("["): "[[]", ord("]"): "[]]"})
    )
    if file_path in old_files:
        old_files.remove(file_path)

    current_file_md5: str = _get_file_hash(file_path)
    for old_file_path in old_files:
        if not os.path.exists(old_file_path):
            continue
        old_file_md5: str = _get_file_hash(old_file_path)
        if current_file_md5 == old_file_md5:
            os.remove(file_path)
            return old_file_path
    return file_path


def clear_hash_cache() -> None:
    """Очистить кеш хешей файлов."""
    global _hash_cache
    _hash_cache.clear()
