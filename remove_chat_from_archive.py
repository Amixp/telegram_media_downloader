#!/usr/bin/env python3
"""
Удаление чата(ов) из архива для повторной загрузки и парсинга.

Удаляет: config (сброс last_read_message_id, ids_to_retry), JSONL, HTML,
ClickHouse, опционально медиафайлы.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any, Dict, List, Set

from rich.prompt import Confirm

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _archive_chat_id_for_path(chat_id: int) -> int:
    """ID чата для путей архива (abs)."""
    return abs(chat_id)


def _collect_file_paths_from_jsonl(jsonl_path: str, base_directory: str) -> Set[str]:
    """Собрать file_path из JSONL, нормализованные абсолютные пути."""
    paths: Set[str] = set()
    if not os.path.exists(jsonl_path):
        return paths
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        continue
                    fp = obj.get("downloaded_file") or obj.get("file_path")
                    if not fp or not isinstance(fp, str):
                        continue
                    norm = os.path.normpath(fp)
                    if not os.path.isabs(norm):
                        norm = os.path.join(base_directory, norm)
                    paths.add(norm)
                except Exception:
                    continue
    except Exception as e:
        logger.warning("Ошибка чтения JSONL %s: %s", jsonl_path, e)
    return paths


def _collect_file_paths_from_clickhouse(
    clickhouse_db: Any, chat_id: int
) -> Set[str]:
    """Собрать file_path из ClickHouse для чата."""
    paths: Set[str] = set()
    if not clickhouse_db or not getattr(clickhouse_db, "enabled", False):
        return paths
    try:
        rows = clickhouse_db.get_messages_for_chat(chat_id)
        for msg in rows:
            fp = msg.get("downloaded_file") or msg.get("file_path")
            if fp and isinstance(fp, str) and fp.strip():
                paths.add(os.path.normpath(fp.strip()))
    except Exception as e:
        logger.warning("Ошибка чтения путей из ClickHouse для chat_id=%s: %s", chat_id, e)
    return paths


def _is_under_base(path: str, base_directory: str) -> bool:
    """Проверить, что путь внутри base_directory."""
    base = os.path.realpath(base_directory)
    try:
        return os.path.commonpath([base, os.path.realpath(path)]) == base
    except ValueError:
        return False


def remove_chat_from_archive(
    *,
    config_path: str,
    chat_ids: List[int],
    delete_media: bool = False,
    dry_run: bool = False,
    yes: bool = False,
) -> int:
    """
    Удалить чаты из архива.

    Returns
    -------
    int
        0 — успех, 1 — ошибка.
    """
    try:
        from utils.config import ConfigManager
        from utils.history import MessageHistory
    except ImportError as e:
        logger.error("Ошибка импорта: %s", e)
        return 1

    try:
        manager = ConfigManager(config_path=config_path)
        config = manager.load()
    except FileNotFoundError:
        logger.error("Файл конфига не найден: %s", config_path)
        return 1
    except Exception as e:
        logger.error("Ошибка загрузки конфига: %s", e)
        return 1

    ds = config.get("download_settings") or {}
    base_directory = (ds.get("base_directory") or "").strip()
    history_directory = ds.get("history_directory", "history")
    if not base_directory:
        logger.error("download_settings.base_directory не задан в конфиге")
        return 1
    if not os.path.isabs(base_directory):
        config_dir = os.path.dirname(config_path)
        base_directory = os.path.abspath(os.path.join(config_dir, base_directory))
    history_path = os.path.join(base_directory, history_directory)

    ch_cfg = config.get("clickhouse", {})
    clickhouse_db = None
    if ch_cfg.get("enabled"):
        from utils.clickhouse_db import ClickHouseMetadataDB
        clickhouse_db = ClickHouseMetadataDB(ch_cfg)
        if not clickhouse_db.enabled:
            clickhouse_db = None

    if not yes and len(chat_ids) > 1:
        ok = Confirm.ask(
            f"Удалить из архива {len(chat_ids)} чатов? "
            "Будут сброшены last_read_message_id, ids_to_retry; удалены JSONL, HTML, данные в ClickHouse."
        )
        if not ok:
            logger.info("Отменено пользователем")
            return 0

    for chat_id in chat_ids:
        path_id = _archive_chat_id_for_path(chat_id)
        jsonl_name = f"chat_{path_id}.jsonl"
        html_name = f"chat_{path_id}.html"
        jsonl_path = os.path.join(history_path, jsonl_name)
        html_path = os.path.join(history_path, html_name)

        if dry_run:
            logger.info(
                "[DRY-RUN] Удалить чат %s: config, %s, %s, ClickHouse",
                chat_id,
                jsonl_path,
                html_path,
            )
            if delete_media:
                paths: Set[str] = set()
                if os.path.exists(jsonl_path):
                    paths |= _collect_file_paths_from_jsonl(jsonl_path, base_directory)
                if clickhouse_db:
                    paths |= _collect_file_paths_from_clickhouse(clickhouse_db, chat_id)
                for p in sorted(paths):
                    if _is_under_base(p, base_directory):
                        logger.info("[DRY-RUN] Удалить медиа: %s", p)
            continue

        # Собрать пути медиа ДО удаления JSONL (если delete_media)
        media_paths: Set[str] = set()
        if delete_media:
            if os.path.exists(jsonl_path):
                media_paths |= _collect_file_paths_from_jsonl(jsonl_path, base_directory)
            if clickhouse_db:
                media_paths |= _collect_file_paths_from_clickhouse(clickhouse_db, chat_id)

        # Сбросить config
        chats = config.get("chats") or []
        for c in chats:
            if isinstance(c, dict) and c.get("chat_id") == chat_id:
                c["last_read_message_id"] = 0
                c["ids_to_retry"] = []
                break

        # Удалить JSONL и HTML
        for p in (jsonl_path, html_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                    logger.info("Удалён: %s", p)
                except OSError as e:
                    logger.warning("Не удалось удалить %s: %s", p, e)

        # Удалить из ClickHouse
        if clickhouse_db and clickhouse_db.enabled:
            try:
                clickhouse_db.delete_chat_data(chat_id)
                logger.info("Удалены данные чата %s из ClickHouse", chat_id)
            except Exception as e:
                logger.warning("Ошибка удаления из ClickHouse для %s: %s", chat_id, e)

        # Удалить медиа (опционально)
        if delete_media and media_paths:
            removed = 0
            for p in media_paths:
                if _is_under_base(p, base_directory) and os.path.isfile(p):
                    try:
                        os.remove(p)
                        removed += 1
                    except OSError:
                        pass
            if removed:
                logger.info("Удалено медиафайлов для чата %s: %d", chat_id, removed)

    if dry_run:
        logger.info("[DRY-RUN] Регенерация index.html не выполнена")
        return 0

    # Обновить index.json: убрать удалённые чаты
    index_manifest_path = os.path.join(history_path, "index.json")
    manifest: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(index_manifest_path):
        try:
            with open(index_manifest_path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            for k, v in raw.items():
                try:
                    cid = int(k)
                    if cid not in chat_ids:
                        manifest[k] = v
                except (ValueError, TypeError):
                    manifest[k] = v
        except Exception as e:
            logger.warning("Ошибка чтения index.json: %s", e)
    with open(index_manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # Регенерировать index.html
    try:
        use_ch = bool(ch_cfg.get("primary_source") and clickhouse_db and getattr(clickhouse_db, "enabled", False))
        history = MessageHistory(
            base_directory,
            history_format="html",
            history_directory=history_directory,
            config_manager=manager,
            clickhouse_db=clickhouse_db,
            clickhouse_primary_source=use_ch,
        )
        history.chats_info = {}
        history.saver.chats_info = {}
        list_fn = history._list_chat_ids_from_clickhouse if use_ch else history._list_chat_ids_from_jsonl
        meta_fn = history._try_get_chat_meta_from_clickhouse if use_ch else history._try_get_chat_meta_from_jsonl
        history.saver._generate_index_html(
            history._load_index_manifest,
            history._save_index_manifest,
            list_fn,
            meta_fn,
        )
        logger.info("Обновлён index.html")
    except Exception as e:
        logger.warning("Ошибка регенерации index.html: %s", e)

    # Сохранить конфиг
    try:
        manager.save()
        logger.info("Конфиг сохранён")
    except Exception as e:
        logger.warning("Ошибка сохранения конфига: %s", e)

    return 0


def main() -> int:
    """Точка входа."""
    parser = argparse.ArgumentParser(
        description="Удаление чата(ов) из архива для повторной загрузки"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        metavar="PATH",
        help="Путь к config.yaml",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--chat-id", type=int, help="ID одного чата для удаления")
    group.add_argument("--all", action="store_true", help="Удалить все чаты из архива")
    parser.add_argument(
        "--delete-media",
        action="store_true",
        help="Также удалять медиафайлы, упомянутые в архиве",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет удалено, без изменений",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Не спрашивать подтверждение при --all",
    )
    args = parser.parse_args()

    config_path = os.path.abspath(args.config)
    if not os.path.exists(config_path):
        logger.error("Файл конфига не найден: %s", config_path)
        return 1

    chat_ids: List[int] = []
    if args.chat_id is not None:
        chat_ids = [args.chat_id]
    else:
        try:
            from utils.config import ConfigManager
            from utils.clickhouse_db import ClickHouseMetadataDB
            manager = ConfigManager(config_path=config_path)
            config = manager.load()
            ch_cfg = config.get("clickhouse", {})
            ch_db = ClickHouseMetadataDB(ch_cfg) if ch_cfg.get("enabled") else None
            if ch_cfg.get("primary_source") and ch_db and ch_db.enabled:
                manifest = ch_db.get_chats_manifest()
                chat_ids = [row[0] for row in manifest]
            else:
                ds = config.get("download_settings") or {}
                base_dir = (ds.get("base_directory") or "").strip()
                hist_dir = ds.get("history_directory", "history")
                if not base_dir:
                    base_dir = os.path.dirname(config_path)
                if not os.path.isabs(base_dir):
                    base_dir = os.path.abspath(os.path.join(os.path.dirname(config_path), base_dir))
                hist_path = os.path.join(base_dir, hist_dir)
                for name in os.listdir(hist_path) if os.path.isdir(hist_path) else []:
                    if name.startswith("chat_") and name.endswith(".jsonl"):
                        mid = name[len("chat_"):-len(".jsonl")]
                        try:
                            chat_ids.append(int(mid))
                        except ValueError:
                            pass
                chats = config.get("chats") or []
                for c in chats:
                    if isinstance(c, dict) and "chat_id" in c:
                        cid = c["chat_id"]
                        if cid not in chat_ids:
                            chat_ids.append(cid)
        except Exception as e:
            logger.error("Ошибка получения списка чатов: %s", e)
            return 1
        if not chat_ids:
            logger.warning("Не найдено чатов для удаления")
            return 0

    return remove_chat_from_archive(
        config_path=config_path,
        chat_ids=chat_ids,
        delete_media=args.delete_media,
        dry_run=args.dry_run,
        yes=args.yes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
