#!/usr/bin/env python3
"""
Экспорт одного чата из архива history/ в отдельную папку.

Идея: JSONL рядом с HTML — источник истины. Мы берём `downloaded_file` из JSONL и
жёстко линкaем (или копируем) файлы в новую структуру, чтобы легко анализировать/шерить.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class ExportResult:
    exported: int
    missing: int
    skipped: int


def _iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def _safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _link_or_copy(src: str, dst: str, mode: str) -> None:
    _safe_mkdir(os.path.dirname(dst))
    if mode == "hardlink":
        try:
            if os.path.exists(dst):
                return
            os.link(src, dst)
            return
        except Exception:
            # Фолбэк: копия (например, другой FS / права / Windows)
            pass
    if os.path.exists(dst):
        return
    shutil.copy2(src, dst)


def _unique_name(base_dir: str, name: str) -> str:
    candidate = name
    root, ext = os.path.splitext(name)
    i = 2
    while os.path.exists(os.path.join(base_dir, candidate)):
        candidate = f"{root}__{i}{ext}"
        i += 1
    return candidate


def export_chat(
    *,
    base_directory: str,
    chat_id: int,
    out_directory: str,
    history_directory: str = "history",
    link_mode: str = "hardlink",
) -> Tuple[ExportResult, str]:
    """
    Экспортировать один чат в отдельную папку.

    Parameters
    ----------
    base_directory: str
        Та же директория, что и `download_settings.base_directory` (где лежит history/).
    chat_id: int
        ID чата (как в имени chat_{chat_id}.jsonl).
    out_directory: str
        Куда писать экспорт (будет создана подпапка chat_{chat_id}/).
    history_directory: str
        Имя папки истории внутри base_directory.
    link_mode: str
        "hardlink" (по умолчанию) или "copy".

    Returns
    -------
    (ExportResult, export_path)
    """
    if link_mode not in ("hardlink", "copy"):
        raise ValueError("link_mode must be 'hardlink' or 'copy'")

    history_path = os.path.join(base_directory, history_directory)
    jsonl_path = os.path.join(history_path, f"chat_{chat_id}.jsonl")
    html_path = os.path.join(history_path, f"chat_{chat_id}.html")

    if not os.path.exists(jsonl_path):
        raise FileNotFoundError(jsonl_path)

    export_path = os.path.join(out_directory, f"chat_{chat_id}")
    media_out = os.path.join(export_path, "media")
    _safe_mkdir(media_out)

    # Скопировать “контейнеры”
    shutil.copy2(jsonl_path, os.path.join(export_path, os.path.basename(jsonl_path)))
    if os.path.exists(html_path):
        shutil.copy2(html_path, os.path.join(export_path, os.path.basename(html_path)))

    exported = 0
    missing = 0
    skipped = 0
    manifest: List[Dict[str, Any]] = []

    for msg in _iter_jsonl(jsonl_path):
        msg_id = msg.get("id")
        src = msg.get("downloaded_file")
        if not src or not isinstance(src, str):
            skipped += 1
            continue
        if not os.path.exists(src):
            missing += 1
            manifest.append({"id": msg_id, "source": src, "status": "missing"})
            continue

        base_name = os.path.basename(src)
        prefix = f"{msg_id}__" if msg_id is not None else ""
        target_name = _unique_name(media_out, prefix + base_name)
        dst = os.path.join(media_out, target_name)
        _link_or_copy(src, dst, mode=link_mode)

        exported += 1
        manifest.append({"id": msg_id, "source": src, "exported_as": os.path.join("media", target_name), "status": "ok"})

    with open(os.path.join(export_path, "export_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "chat_id": chat_id,
                "exported": exported,
                "missing": missing,
                "skipped": skipped,
                "items": manifest,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    return ExportResult(exported=exported, missing=missing, skipped=skipped), export_path


def main() -> int:
    p = argparse.ArgumentParser(description="Экспорт чата из history/ в отдельную папку")
    p.add_argument("--base-directory", required=True, help="download_settings.base_directory (где лежит history/)")
    p.add_argument("--chat-id", required=True, type=int, help="ID чата (как в имени chat_{chat_id}.jsonl)")
    p.add_argument("--out", required=True, help="Папка, куда писать экспорт")
    p.add_argument("--history-directory", default="history", help="Имя папки истории внутри base-directory (default: history)")
    p.add_argument(
        "--link-mode",
        choices=["hardlink", "copy"],
        default="hardlink",
        help="hardlink быстрее/без дублей, copy — переносимая копия (default: hardlink)",
    )
    args = p.parse_args()

    result, export_path = export_chat(
        base_directory=args.base_directory,
        chat_id=args.chat_id,
        out_directory=args.out,
        history_directory=args.history_directory,
        link_mode=args.link_mode,
    )

    print(f"Экспортировано: {result.exported}, отсутствуют: {result.missing}, пропущено: {result.skipped}")
    print(f"Папка: {export_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

