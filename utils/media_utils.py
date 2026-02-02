"""Общие утилиты для работы с медиафайлами."""
from typing import Optional

from telethon.tl.types import Message, MessageMediaDocument, MessageMediaPhoto


def sanitize_filename(filename: str) -> str:
    """
    Очистить имя файла от недопустимых символов для Windows и других ОС.

    Parameters
    ----------
    filename: str
        Исходное имя файла.

    Returns
    -------
    str
        Безопасное имя файла.
    """
    replacements = {
        ':': '-',
        '<': '_',
        '>': '_',
        '"': "'",
        '/': '_',
        '\\': '_',
        '|': '_',
        '?': '_',
        '*': '_',
    }

    for char, replacement in replacements.items():
        filename = filename.replace(char, replacement)

    return filename


def get_media_type(message: Message) -> Optional[str]:
    """
    Определить тип медиа из атрибутов сообщения.

    Parameters
    ----------
    message: Message
        Объект сообщения Telethon.

    Returns
    -------
    Optional[str]
        Тип медиа ('photo', 'video', 'audio', 'voice', 'video_note', 'document')
        или None.
    """
    if not message.media:
        return None

    if isinstance(message.media, MessageMediaPhoto):
        return "photo"

    if isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        for attr in doc.attributes:
            if hasattr(attr, "voice") and isinstance(attr.voice, bool):
                return "voice" if attr.voice else "audio"
            if hasattr(attr, "round_message") and isinstance(attr.round_message, bool):
                return "video_note" if attr.round_message else "video"
        return "document"

    # Упрощенная проверка для остальных типов
    media_type = str(type(message.media).__name__)
    return media_type.replace("MessageMedia", "").lower()
