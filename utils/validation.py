"""Жёсткая валидация скачанных медиа и архивных файлов."""
from __future__ import annotations

import json
import os
from typing import Literal, Optional

# Сигнатуры контейнеров (magic bytes)
# MP4/MOV/3GP: ...ftyp at 4:8
_MP4_FTYP = b"ftyp"
# MKV/WebM
_MKV_SIG = bytes([0x1A, 0x45, 0xDF, 0xA3])
# AVI: RIFF....AVI
_AVI_RIFF = b"RIFF"
_AVI_AVI = b"AVI "
# OGG (в т.ч. OGV)
_OGG_SIG = b"OggS"
# WAV
_WAV_RIFF = b"RIFF"
_WAV_WAVE = b"WAVE"


def _read_head(path: str, size: int = 32) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read(size)
    except OSError:
        return None


def _has_video_or_media_signature(head: bytes) -> bool:
    """Проверить, что файл похож на видео/аудио контейнер по magic bytes."""
    if len(head) < 12:
        return False
    # MP4/MOV/3GP
    if head[4:8] == _MP4_FTYP:
        return True
    # MKV/WebM
    if head[:4] == _MKV_SIG:
        return True
    # AVI
    if head[:4] == _AVI_RIFF and len(head) >= 12 and head[8:12] == _AVI_AVI:
        return True
    # OGG/OGV
    if head[:4] == _OGG_SIG:
        return True
    # WAV
    if head[:4] == _WAV_RIFF and len(head) >= 12 and head[8:12] == _WAV_WAVE:
        return True
    return False


def validate_downloaded_media(
    path: str,
    media_type: str,
    expected_size: Optional[int] = None,
    *,
    check_signature: bool = True,
) -> bool:
    """
    Жёсткая проверка скачанного медиафайла.

    - Существует, доступен для чтения, размер > 0.
    - Для video/video_note: ожидаемый размер (если задан) и magic bytes контейнера.
    - Для audio/voice: размер; опционально сигнатура (mp3 и т.д. не всегда по magic).

    Parameters
    ----------
    path : str
        Путь к файлу.
    media_type : str
        Тип медиа: video, video_note, audio, voice, document, photo.
    expected_size : Optional[int]
        Ожидаемый размер в байтах (от API).
    check_signature : bool
        Проверять ли magic bytes для video/video_note.

    Returns
    -------
    bool
        True, если файл прошёл проверку.
    """
    if not path or not os.path.isfile(path):
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size <= 0:
        return False
    if expected_size is not None and expected_size > 0:
        # Допуск: не менее 95% от ожидаемого (обрезка при загрузке)
        if size < int(expected_size * 0.95):
            return False

    if not check_signature:
        return True

    head = _read_head(path)
    if not head:
        return False

    if media_type in ("video", "video_note"):
        return _has_video_or_media_signature(head)
    if media_type in ("audio", "voice"):
        # Аудио: часто MP3 (ID3 или 0xFF 0xFB), OGG, WAV, M4A (ftyp). Проверяем только
        # контейнеры с однозначной сигнатурой; остальное считаем допустимым при size > 0.
        if _has_video_or_media_signature(head):
            return True
        # MP3: ID3v2 или frame sync
        if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
            return True
        return True  # иначе уже прошли size — не режем все подряд
    # document, photo: только существование и размер
    return True


def validate_archive_file(
    path: str,
    fmt: Literal["jsonl", "txt"],
    *,
    min_lines: int = 1,
    sample_lines: int = 5,
) -> bool:
    """
    Жёсткая проверка архивного файла истории (chat_*.jsonl или chat_*.txt).

    - Файл существует, читается, размер > 0.
    - JSONL: хотя бы min_lines валидных JSON-строк; проверяются первые sample_lines.
    - TXT: непустой и читаемый.

    Parameters
    ----------
    path : str
        Путь к архиву.
    fmt : Literal['jsonl', 'txt']
        Формат архива.
    min_lines : int
        Минимум валидных строк для JSONL.
    sample_lines : int
        Сколько строк с начала проверять на валидность JSON.

    Returns
    -------
    bool
        True, если архив прошёл проверку.
    """
    if not path or not os.path.isfile(path):
        return False
    try:
        st = os.stat(path)
        if st.st_size <= 0:
            return False
    except OSError:
        return False

    try:
        with open(path, "r", encoding="utf-8", errors="strict") as f:
            if fmt == "txt":
                first = f.read(1)
                return len(first) > 0
            # jsonl
            valid = 0
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        valid += 1
                except json.JSONDecodeError:
                    continue
                if i >= sample_lines - 1:
                    break
            return valid >= min_lines
    except (OSError, UnicodeDecodeError):
        return False
