#!/usr/bin/env python3
"""
Перезаписать ссылки на скачанные файлы с base_directory на media_links_base.

Обновляет поле downloaded_file в chat_*.jsonl и колонку file_path в ClickHouse
(messages, file_downloads). Опционально пересобирает chat_*.html.
Читает base_directory и media_links_base из config.yaml.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from typing import List, Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _list_chat_ids_from_jsonl(history_path: str) -> List[int]:
    chat_ids: List[int] = []
    try:
        for name in os.listdir(history_path):
            if not (name.startswith("chat_") and name.endswith(".jsonl")):
                continue
            middle = name[len("chat_") : -len(".jsonl")]
            try:
                chat_ids.append(int(middle))
            except ValueError:
                continue
    except OSError:
        return []
    return chat_ids


def _rewrite_jsonl_file(
    jsonl_path: str,
    base_directory: str,
    media_links_base: str,
    dry_run: bool,
) -> int:
    from utils.path_rewrite import to_display_path  # pylint: disable=import-outside-toplevel

    count = 0
    lines_out: List[str] = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                lines_out.append(line)
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                lines_out.append(line)
                continue
            path = msg.get("downloaded_file")
            if path and isinstance(path, str):
                new_path = to_display_path(path.strip(), base_directory, media_links_base)
                if new_path != path:
                    count += 1
                    msg["downloaded_file"] = new_path
            lines_out.append(json.dumps(msg, ensure_ascii=False))

    if count and not dry_run:
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for line in lines_out:
                f.write(line + "\n")
    return count


def _rewrite_clickhouse(
    ch_config: dict,
    base_directory: str,
    media_links_base: str,
    dry_run: bool,
) -> int:
    try:
        from utils.clickhouse_db import ClickHouseMetadataDB  # pylint: disable=import-outside-toplevel
    except ImportError:
        logger.warning("ClickHouse недоступен, пропуск обновления БД")
        return 0

    if not ch_config.get("enabled"):
        return 0

    db = ClickHouseMetadataDB(ch_config)
    if not db.enabled:
        return 0

    client = db._get_client()
    old_base = os.path.normpath(os.path.abspath(base_directory))
    new_base = os.path.normpath(os.path.abspath(media_links_base))
    old_len = len(old_base)
    total = 0

    for table in ("messages", "file_downloads"):
        try:
            if dry_run:
                n = client.execute(
                    "SELECT count() FROM {} WHERE file_path != '' AND substring(file_path, 1, %(len)s) = %(old_base)s".format(
                        table
                    ),
                    {"len": old_len, "old_base": old_base},
                )
                cnt = n[0][0] if n else 0
            else:
                client.execute(
                    (
                        "ALTER TABLE {} UPDATE file_path = concat(%(new_base)s, substring(file_path, %(old_len)s + 1)) "
                        "WHERE file_path != '' AND length(file_path) >= %(old_len)s AND substring(file_path, 1, %(old_len)s) = %(old_base)s"
                    ).format(table),
                    {"new_base": new_base, "old_len": old_len, "old_base": old_base},
                )
                cnt = 1
            total += cnt
        except Exception as e:
            logger.warning("Ошибка обновления таблицы %s: %s", table, e)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Перезаписать пути к файлам в истории и ClickHouse на media_links_base из конфига"
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Путь к config.yaml (по умолчанию: config.yaml в директории скрипта)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет заменено, без записи",
    )
    parser.add_argument(
        "--regenerate-html",
        action="store_true",
        help="После перезаписи пересоздать chat_*.html и index.html",
    )
    args = parser.parse_args()

    from utils.config import ConfigManager  # pylint: disable=import-outside-toplevel

    try:
        config_manager = ConfigManager(config_path=args.config)
        config = config_manager.load()
    except FileNotFoundError as e:
        logger.error("Конфиг не найден: %s", e)
        return 1

    ds = config.get("download_settings", {})
    base_directory = (ds.get("base_directory") or "").strip()
    media_links_base = (ds.get("media_links_base") or "").strip()
    history_directory = (ds.get("history_directory") or "history").strip()

    if not media_links_base:
        logger.error("В конфиге задан пустой media_links_base. Укажите папку для ссылок.")
        return 1

    script_dir = os.path.dirname(os.path.abspath(__file__))
    if not base_directory:
        base_directory = script_dir
    if not os.path.isabs(base_directory):
        config_dir = os.path.dirname(config_manager.config_path)
        base_directory = os.path.abspath(os.path.join(config_dir, base_directory))
    else:
        base_directory = os.path.abspath(base_directory)

    if not os.path.isabs(media_links_base):
        config_dir = os.path.dirname(config_manager.config_path)
        media_links_base = os.path.abspath(os.path.join(config_dir, media_links_base))
    else:
        media_links_base = os.path.abspath(media_links_base)

    if args.dry_run:
        logger.info("Режим --dry-run: изменения не записываются")

    history_path = os.path.join(base_directory, history_directory)
    if not os.path.isdir(history_path):
        logger.error("Нет папки истории: %s", history_path)
        return 1

    jsonl_count = 0
    for cid in _list_chat_ids_from_jsonl(history_path):
        jsonl_name = f"chat_{abs(cid)}.jsonl"
        jsonl_path = os.path.join(history_path, jsonl_name)
        if not os.path.isfile(jsonl_path):
            continue
        n = _rewrite_jsonl_file(jsonl_path, base_directory, media_links_base, args.dry_run)
        if n:
            jsonl_count += n
            logger.info("%s: обновлено записей: %s", jsonl_name, n)

    if jsonl_count or args.dry_run:
        logger.info("JSONL: всего обновлено записей с путями: %s", jsonl_count)

    ch_updated = _rewrite_clickhouse(
        config.get("clickhouse", {}),
        base_directory,
        media_links_base,
        args.dry_run,
    )
    if ch_updated and not args.dry_run:
        logger.info("ClickHouse: выполнена мутация обновления file_path")
    elif args.dry_run and ch_updated:
        logger.info("ClickHouse: в режиме dry-run мутация не выполняется")

    if args.regenerate_html and not args.dry_run:
        from utils.history import MessageHistory  # pylint: disable=import-outside-toplevel

        h = MessageHistory(
            base_directory=base_directory,
            history_format="html",
            history_directory=history_directory,
        )
        chat_ids = _list_chat_ids_from_jsonl(history_path)
        for cid in chat_ids:
            try:
                h._generate_chat_html(cid)  # noqa: SLF001
            except Exception as e:
                logger.warning("Ошибка пересборки HTML для чата %s: %s", cid, e)
        h._generate_index_html()  # noqa: SLF001
        logger.info("Пересобраны chat_*.html и index.html")

    logger.info("Готово")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
