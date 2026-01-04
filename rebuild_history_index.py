#!/usr/bin/env python3
"""
Пересобрать history/index.html из уже скачанного архива.

Полезно когда загрузка прервалась или index.html/manifest рассинхронизировались.
Работает локально по `chat_*.jsonl` в history/.
"""

from __future__ import annotations

import argparse
import os
from typing import List


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
    p.add_argument("--base-directory", required=True, help="download_settings.base_directory (где лежит history/)")
    p.add_argument("--history-directory", default="history", help="Имя папки истории внутри base-directory (default: history)")
    p.add_argument(
        "--regenerate-html",
        action="store_true",
        help="Также пересоздать chat_*.html для всех найденных chat_*.jsonl (может быть медленно)",
    )
    args = p.parse_args()

    # Локальный импорт: скрипт должен работать без установки пакета
    from utils.history import MessageHistory  # pylint: disable=import-outside-toplevel

    history_path = os.path.join(args.base_directory, args.history_directory)
    if not os.path.isdir(history_path):
        raise SystemExit(f"Нет папки истории: {history_path}")

    h = MessageHistory(base_directory=args.base_directory, history_format="html", history_directory=args.history_directory)

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

