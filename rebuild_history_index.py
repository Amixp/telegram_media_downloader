#!/usr/bin/env python3
"""
Пересобрать history/index.html из уже скачанного архива.

Полезно когда загрузка прервалась или index.html/manifest рассинхронизировались.
Работает локально по `chat_*.jsonl` в history/.
"""

from __future__ import annotations

import argparse
import os
from typing import List, Optional


def _list_chat_ids_from_jsonl(history_path: str) -> List[int]:
    chat_ids: List[int] = []
    try:
        for name in os.listdir(history_path):
            if not (name.startswith("chat_") and name.endswith(".jsonl")):
                continue
            middle = name[len("chat_") : -len(".jsonl")]
            try:
                chat_ids.append(int(middle))
            except Exception:
                continue
    except Exception:
        return []
    return chat_ids


def main() -> int:
    p = argparse.ArgumentParser(description="Пересобрать history/index.html из архива (chat_*.jsonl)")
    p.add_argument(
        "--base-directory",
        default=None,
        help="download_settings.base_directory (где лежит history/). Если не указан, читается из config.yaml",
    )
    p.add_argument(
        "--history-directory",
        default=None,
        help="Имя папки истории внутри base-directory. Если не указан, читается из config.yaml (default: history)",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Путь к config.yaml (по умолчанию: config.yaml в директории скрипта)",
    )
    p.add_argument(
        "--regenerate-html",
        action="store_true",
        help="Также пересоздать chat_*.html для всех найденных chat_*.jsonl (может быть медленно)",
    )
    args = p.parse_args()

    # Локальный импорт: скрипт должен работать без установки пакета
    from utils.config import ConfigManager  # pylint: disable=import-outside-toplevel
    from utils.history import MessageHistory  # pylint: disable=import-outside-toplevel

    # Попытаться загрузить параметры из конфига, если не заданы
    base_directory: Optional[str] = args.base_directory
    history_directory: Optional[str] = args.history_directory

    if base_directory is None or history_directory is None:
        try:
            config_manager = ConfigManager(config_path=args.config)
            config = config_manager.load()
            download_settings = config.get("download_settings", {})
            
            if base_directory is None:
                base_directory = download_settings.get("base_directory")
                if base_directory is None or base_directory == "":
                    # Если base_directory пустой в конфиге, использовать директорию скрипта
                    base_directory = os.path.dirname(os.path.abspath(__file__))
            
            if history_directory is None:
                history_directory = download_settings.get("history_directory", "history")
        except FileNotFoundError:
            if base_directory is None:
                raise SystemExit(
                    "Ошибка: --base-directory не указан и config.yaml не найден.\n"
                    "Укажите --base-directory или создайте config.yaml с download_settings.base_directory"
                )
            # Если base_directory задан, но history_directory нет - используем дефолт
            if history_directory is None:
                history_directory = "history"
        except Exception as e:
            if base_directory is None:
                raise SystemExit(f"Ошибка при чтении конфига: {e}")
            # Если base_directory задан, но history_directory нет - используем дефолт
            if history_directory is None:
                history_directory = "history"

    if base_directory is None:
        raise SystemExit("Ошибка: --base-directory не указан и не найден в config.yaml")

    history_path = os.path.join(base_directory, history_directory)
    if not os.path.isdir(history_path):
        raise SystemExit(f"Нет папки истории: {history_path}")

    h = MessageHistory(base_directory=base_directory, history_format="html", history_directory=history_directory)

    chat_ids = _list_chat_ids_from_jsonl(history_path)
    if args.regenerate_html:
        for cid in chat_ids:
            try:
                h._generate_chat_html(cid)  # noqa: SLF001 - утилита администрирования
            except Exception:
                # Не валимся на одном чате — индекс всё равно соберём
                continue

    h._generate_index_html()  # noqa: SLF001 - утилита администрирования

    index_html = os.path.join(history_path, "index.html")
    manifest = os.path.join(history_path, "index.json")
    print(f"Готово: {index_html}")
    print(f"Манифест: {manifest}")
    print(f"Чатов (jsonl): {len(chat_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

