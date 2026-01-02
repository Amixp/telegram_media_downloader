"""Модуль для сохранения истории сообщений."""
import html
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from telethon.tl.types import Message, MessageMediaDocument, MessageMediaPhoto


class MessageHistory:
    """Класс для сохранения истории сообщений."""

    def __init__(
        self,
        base_directory: str,
        history_format: str = "json",
        history_directory: str = "history",
    ):
        """
        Инициализация MessageHistory.

        Parameters
        ----------
        base_directory: str
            Базовая директория для сохранения истории.
        history_format: str
            Формат сохранения ('json', 'txt' или 'html').
        history_directory: str
            Имя директории для истории внутри базовой директории.
        """
        self.base_directory = base_directory
        self.history_format = history_format.lower()
        self.history_directory = history_directory
        self.history_path = os.path.join(base_directory, history_directory)
        os.makedirs(self.history_path, exist_ok=True)
        self.chats_info: Dict[int, Dict[str, Any]] = {}  # Информация о чатах для индекса

    def save_message(
        self,
        message: Message,
        chat_id: int,
        chat_title: Optional[str] = None,
        downloaded_file_path: Optional[str] = None
    ) -> None:
        """
        Сохранить одно сообщение.

        Parameters
        ----------
        message: Message
            Сообщение для сохранения.
        chat_id: int
            ID чата.
        chat_title: Optional[str]
            Название чата.
        downloaded_file_path: Optional[str]
            Путь к скачанному файлу (если был скачан).
        """
        # Сохранить информацию о чате
        if chat_id not in self.chats_info:
            self.chats_info[chat_id] = {
                "title": chat_title or f"Chat {chat_id}",
                "message_count": 0,
                "last_message_date": None
            }

        self.chats_info[chat_id]["message_count"] += 1
        if message.date:
            self.chats_info[chat_id]["last_message_date"] = message.date

        if self.history_format == "json":
            self._save_json(message, chat_id, chat_title, downloaded_file_path)
        elif self.history_format == "html":
            self._save_html_message(message, chat_id, chat_title, downloaded_file_path)
        else:
            self._save_txt(message, chat_id, chat_title, downloaded_file_path)

    def _save_json(
        self,
        message: Message,
        chat_id: int,
        chat_title: Optional[str],
        downloaded_file_path: Optional[str] = None
    ) -> None:
        """
        Сохранить сообщение в JSON формате.

        Parameters
        ----------
        message: Message
            Сообщение для сохранения.
        chat_id: int
            ID чата.
        chat_title: Optional[str]
            Название чата.
        downloaded_file_path: Optional[str]
            Путь к скачанному файлу.
        """
        chat_file = os.path.join(self.history_path, f"chat_{chat_id}.jsonl")
        message_data: Dict[str, Any] = {
            "id": message.id,
            "date": message.date.isoformat() if message.date else None,
            "text": message.message or "",
            "sender_id": message.sender_id,
            "chat_id": chat_id,
            "chat_title": chat_title,
            "has_media": bool(message.media),
            "views": getattr(message, "views", None),
            "forwards": getattr(message, "forwards", None),
            "reply_to_msg_id": message.reply_to_msg_id if message.reply_to else None,
            "edit_date": message.edit_date.isoformat() if message.edit_date else None,
        }

        # Добавить информацию о медиа, если есть
        if message.media:
            media_info = self._extract_media_info(message)
            message_data.update(media_info)

        # Добавить путь к скачанному файлу
        if downloaded_file_path:
            message_data["downloaded_file"] = downloaded_file_path

        with open(chat_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(message_data, ensure_ascii=False) + "\n")

    def _sanitize_filename(self, filename: str) -> str:
        """
        Очистить имя файла от недопустимых символов для Windows.

        Parameters
        ----------
        filename: str
            Исходное имя файла.

        Returns
        -------
        str
            Безопасное имя файла.
        """
        # Недопустимые символы в Windows: < > : " / \ | ? *
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

    def _save_txt(
        self,
        message: Message,
        chat_id: int,
        chat_title: Optional[str],
        downloaded_file_path: Optional[str] = None
    ) -> None:
        """
        Сохранить сообщение в текстовом формате.

        Parameters
        ----------
        message: Message
            Сообщение для сохранения.
        chat_id: int
            ID чата.
        chat_title: Optional[str]
            Название чата.
        downloaded_file_path: Optional[str]
            Путь к скачанному файлу.
        """
        chat_file = os.path.join(self.history_path, f"chat_{chat_id}.txt")
        date_str = message.date.strftime("%Y-%m-%d %H-%M-%S") if message.date else "Unknown"
        text = message.message or "[Без текста]"
        media_info = ""

        if message.media:
            media_type = self._get_media_type(message)
            media_details = self._extract_media_info(message)
            media_info = f" [Медиа: {media_type}"
            if media_details.get("file_name"):
                media_info += f", файл: {media_details['file_name']}"
            media_info += "]"

        file_info = ""
        if downloaded_file_path:
            file_info = f"\n  Скачано: {downloaded_file_path}"

        with open(chat_file, "a", encoding="utf-8") as f:
            f.write(f"[{date_str}] ID:{message.id} {text}{media_info}{file_info}\n")

    def _get_media_type(self, message: Message) -> str:
        """
        Определить тип медиа в сообщении.

        Parameters
        ----------
        message: Message
            Сообщение.

        Returns
        -------
        str
            Тип медиа.
        """
        if not message.media:
            return "None"

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

    def _extract_media_info(self, message: Message) -> Dict[str, Any]:
        """
        Извлечь детальную информацию о медиа.

        Parameters
        ----------
        message: Message
            Сообщение.

        Returns
        -------
        Dict[str, Any]
            Словарь с информацией о медиа.
        """
        media_info: Dict[str, Any] = {
            "media_type": self._get_media_type(message)
        }

        if isinstance(message.media, MessageMediaPhoto):
            photo = message.media.photo
            if photo:
                media_info["photo_id"] = photo.id
                media_info["file_size"] = getattr(photo, "size", None)

        elif isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            if doc:
                media_info["document_id"] = doc.id
                media_info["file_size"] = doc.size
                media_info["mime_type"] = doc.mime_type

                # Извлечь имя файла и другие атрибуты
                for attr in doc.attributes:
                    if hasattr(attr, "file_name"):
                        media_info["file_name"] = attr.file_name
                    if hasattr(attr, "duration"):
                        media_info["duration"] = attr.duration
                    if hasattr(attr, "w") and hasattr(attr, "h"):
                        media_info["width"] = attr.w
                        media_info["height"] = attr.h

        return media_info

    def save_batch(
        self,
        messages: List[Message],
        chat_id: int,
        chat_title: Optional[str] = None,
        downloaded_files: Optional[Dict[int, str]] = None,
    ) -> None:
        """
        Сохранить пакет сообщений.

        Parameters
        ----------
        messages: List[Message]
            Список сообщений для сохранения.
        chat_id: int
            ID чата.
        chat_title: Optional[str]
            Название чата.
        downloaded_files: Optional[Dict[int, str]]
            Словарь {message_id: file_path} для скачанных файлов.
        """
        downloaded_files = downloaded_files or {}
        for message in messages:
            file_path = downloaded_files.get(message.id)
            self.save_message(message, chat_id, chat_title, file_path)

        # Создать/обновить индексный HTML файл после сохранения пакета
        if self.history_format == "html":
            self._generate_index_html()

    def _save_html_message(
        self,
        message: Message,
        chat_id: int,
        chat_title: Optional[str],
        downloaded_file_path: Optional[str] = None
    ) -> None:
        """
        Сохранить сообщение в HTML (буферизация).

        Parameters
        ----------
        message: Message
            Сообщение для сохранения.
        chat_id: int
            ID чата.
        chat_title: Optional[str]
            Название чата.
        downloaded_file_path: Optional[str]
            Путь к скачанному файлу.
        """
        # Сохраняем в JSON для последующей генерации HTML
        chat_file = os.path.join(self.history_path, f"chat_{chat_id}.jsonl")
        message_data: Dict[str, Any] = {
            "id": message.id,
            "date": message.date.isoformat() if message.date else None,
            "text": message.message or "",
            "sender_id": message.sender_id,
            "chat_id": chat_id,
            "chat_title": chat_title,
            "has_media": bool(message.media),
            "views": getattr(message, "views", None),
            "forwards": getattr(message, "forwards", None),
            "reply_to_msg_id": message.reply_to_msg_id if message.reply_to else None,
            "edit_date": message.edit_date.isoformat() if message.edit_date else None,
        }

        if message.media:
            media_info = self._extract_media_info(message)
            message_data.update(media_info)

        if downloaded_file_path:
            message_data["downloaded_file"] = downloaded_file_path

        with open(chat_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(message_data, ensure_ascii=False) + "\n")

    def _generate_chat_html(self, chat_id: int) -> None:
        """
        Сгенерировать HTML файл для конкретного чата.

        Parameters
        ----------
        chat_id: int
            ID чата.
        """
        jsonl_file = os.path.join(self.history_path, f"chat_{chat_id}.jsonl")
        if not os.path.exists(jsonl_file):
            return

        messages = []
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                messages.append(json.loads(line))

        if not messages:
            return

        chat_title = messages[0].get("chat_title", f"Чат {chat_id}")
        html_file = os.path.join(self.history_path, f"chat_{chat_id}.html")

        html_content = self._get_html_template(chat_title, messages)

        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)

    def _get_html_template(self, chat_title: str, messages: List[Dict[str, Any]]) -> str:
        """
        Создать HTML шаблон для чата.

        Parameters
        ----------
        chat_title: str
            Название чата.
        messages: List[Dict[str, Any]]
            Список сообщений.

        Returns
        -------
        str
            HTML контент.
        """
        messages_html = ""
        for msg in messages:
            messages_html += self._format_message_html(msg)

        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(chat_title)}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        .header h1 {{
            font-size: 28px;
            margin-bottom: 10px;
        }}
        .header .stats {{
            font-size: 14px;
            opacity: 0.9;
        }}
        .messages {{
            padding: 20px;
            max-height: 80vh;
            overflow-y: auto;
        }}
        .message {{
            background: #f8f9fa;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 16px;
            border-left: 4px solid #667eea;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .message:hover {{
            transform: translateX(4px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }}
        .message-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            font-size: 13px;
            color: #6c757d;
        }}
        .message-id {{
            font-weight: bold;
            color: #667eea;
        }}
        .message-date {{
            font-style: italic;
        }}
        .message-text {{
            color: #212529;
            line-height: 1.6;
            margin-bottom: 12px;
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        .message-text.empty {{
            color: #adb5bd;
            font-style: italic;
        }}
        .media-info {{
            background: #e3f2fd;
            border-radius: 8px;
            padding: 12px;
            margin-top: 12px;
            border-left: 3px solid #2196f3;
        }}
        .media-badge {{
            display: inline-block;
            background: #2196f3;
            color: white;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: bold;
            margin-bottom: 8px;
        }}
        .file-link {{
            display: inline-flex;
            align-items: center;
            background: #4caf50;
            color: white;
            padding: 8px 16px;
            border-radius: 6px;
            text-decoration: none;
            font-size: 14px;
            margin-top: 8px;
            transition: background 0.2s;
        }}
        .file-link:hover {{
            background: #45a049;
        }}
        .file-link::before {{
            content: "📎 ";
            margin-right: 6px;
        }}
        .meta-info {{
            display: flex;
            gap: 16px;
            margin-top: 12px;
            font-size: 12px;
            color: #6c757d;
        }}
        .meta-item {{
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        .search-box {{
            padding: 20px;
            background: #f8f9fa;
            border-bottom: 1px solid #dee2e6;
        }}
        .search-box input {{
            width: 100%;
            padding: 12px 20px;
            border: 2px solid #dee2e6;
            border-radius: 8px;
            font-size: 16px;
            transition: border-color 0.2s;
        }}
        .search-box input:focus {{
            outline: none;
            border-color: #667eea;
        }}
        .back-link {{
            display: inline-block;
            margin: 20px;
            padding: 10px 20px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 6px;
            transition: background 0.2s;
        }}
        .back-link:hover {{
            background: #5568d3;
        }}
    </style>
</head>
<body>
    <a href="index.html" class="back-link">← Назад к списку чатов</a>
    <div class="container">
        <div class="header">
            <h1>{html.escape(chat_title)}</h1>
            <div class="stats">Всего сообщений: {len(messages)}</div>
        </div>
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="🔍 Поиск по сообщениям..." onkeyup="filterMessages()">
        </div>
        <div class="messages" id="messagesContainer">
            {messages_html}
        </div>
    </div>
    <script>
        function filterMessages() {{
            const input = document.getElementById('searchInput');
            const filter = input.value.toLowerCase();
            const messages = document.querySelectorAll('.message');

            messages.forEach(message => {{
                const text = message.textContent.toLowerCase();
                message.style.display = text.includes(filter) ? '' : 'none';
            }});
        }}
    </script>
</body>
</html>"""

    def _format_message_html(self, msg: Dict[str, Any]) -> str:
        """
        Форматировать одно сообщение в HTML.

        Parameters
        ----------
        msg: Dict[str, Any]
            Данные сообщения.

        Returns
        -------
        str
            HTML фрагмент сообщения.
        """
        msg_id = msg.get("id", "?")
        date_str = msg.get("date", "")
        if date_str:
            try:
                date_obj = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                date_str = date_obj.strftime("%d.%m.%Y %H:%M:%S")
            except:
                pass

        text = msg.get("text", "")
        text_html = html.escape(text) if text else '<span class="empty">[Нет текста]</span>'

        media_html = ""
        if msg.get("has_media"):
            media_type = msg.get("media_type", "unknown")
            file_name = msg.get("file_name", "")
            file_size = msg.get("file_size", 0)
            duration = msg.get("duration")
            width = msg.get("width")
            height = msg.get("height")

            media_html = f'<div class="media-info">'
            media_html += f'<div class="media-badge">{media_type.upper()}</div>'

            details = []
            if file_name:
                details.append(f"<div>📄 Файл: {html.escape(file_name)}</div>")
            if file_size:
                size_mb = file_size / (1024 * 1024)
                details.append(f"<div>💾 Размер: {size_mb:.2f} МБ</div>")
            if duration:
                details.append(f"<div>⏱️ Длительность: {duration} сек</div>")
            if width and height:
                details.append(f"<div>📐 Разрешение: {width}×{height}</div>")

            media_html += "".join(details)
            media_html += '</div>'

        file_html = ""
        if msg.get("downloaded_file"):
            file_path = msg["downloaded_file"]
            # Создать относительный путь от папки history
            rel_path = os.path.relpath(file_path, self.history_path)
            file_html = f'<a href="{html.escape(rel_path)}" class="file-link" target="_blank">Открыть файл</a>'

        meta_html = '<div class="meta-info">'
        if msg.get("views"):
            meta_html += f'<div class="meta-item">👁️ {msg["views"]}</div>'
        if msg.get("forwards"):
            meta_html += f'<div class="meta-item">🔄 {msg["forwards"]}</div>'
        if msg.get("reply_to_msg_id"):
            meta_html += f'<div class="meta-item">↩️ Ответ на #{msg["reply_to_msg_id"]}</div>'
        if msg.get("edit_date"):
            meta_html += f'<div class="meta-item">✏️ Отредактировано</div>'
        meta_html += '</div>'

        return f"""
        <div class="message">
            <div class="message-header">
                <span class="message-id">#{msg_id}</span>
                <span class="message-date">{date_str}</span>
            </div>
            <div class="message-text">{text_html}</div>
            {media_html}
            {file_html}
            {meta_html}
        </div>
        """

    def _generate_index_html(self) -> None:
        """Сгенерировать индексный HTML файл со списком всех чатов."""
        # Сначала генерируем HTML для всех чатов
        for chat_id in self.chats_info.keys():
            self._generate_chat_html(chat_id)

        index_file = os.path.join(self.history_path, "index.html")

        chats_html = ""
        for chat_id, info in sorted(self.chats_info.items(),
                                    key=lambda x: x[1].get("last_message_date") or datetime.min,
                                    reverse=True):
            title = info["title"]
            count = info["message_count"]
            last_date = info.get("last_message_date")
            date_str = last_date.strftime("%d.%m.%Y %H:%M") if last_date else "Неизвестно"

            chats_html += f"""
            <div class="chat-card">
                <div class="chat-header">
                    <h3>{html.escape(title)}</h3>
                    <span class="chat-date">{date_str}</span>
                </div>
                <div class="chat-stats">
                    <span>💬 {count} сообщений</span>
                </div>
                <a href="chat_{chat_id}.html" class="chat-link">Открыть чат →</a>
            </div>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>История Telegram чатов</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 40px 20px;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .main-header {{
            text-align: center;
            color: white;
            margin-bottom: 40px;
        }}
        .main-header h1 {{
            font-size: 48px;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }}
        .main-header p {{
            font-size: 18px;
            opacity: 0.9;
        }}
        .chats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            gap: 24px;
        }}
        .chat-card {{
            background: white;
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            transition: transform 0.3s, box-shadow 0.3s;
            display: flex;
            flex-direction: column;
        }}
        .chat-card:hover {{
            transform: translateY(-8px);
            box-shadow: 0 15px 40px rgba(0,0,0,0.3);
        }}
        .chat-header {{
            display: flex;
            justify-content: space-between;
            align-items: start;
            margin-bottom: 16px;
        }}
        .chat-header h3 {{
            color: #212529;
            font-size: 20px;
            flex: 1;
            margin-right: 12px;
        }}
        .chat-date {{
            font-size: 12px;
            color: #6c757d;
            white-space: nowrap;
        }}
        .chat-stats {{
            color: #6c757d;
            font-size: 14px;
            margin-bottom: 16px;
        }}
        .chat-link {{
            display: inline-block;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 12px 24px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: bold;
            text-align: center;
            transition: opacity 0.2s;
            margin-top: auto;
        }}
        .chat-link:hover {{
            opacity: 0.9;
        }}
        .empty-state {{
            text-align: center;
            color: white;
            padding: 60px 20px;
            background: rgba(255,255,255,0.1);
            border-radius: 16px;
        }}
        .empty-state h2 {{
            font-size: 32px;
            margin-bottom: 16px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="main-header">
            <h1>📱 История Telegram</h1>
            <p>Просмотр загруженных чатов</p>
        </div>
        <div class="chats-grid">
            {chats_html if chats_html else '<div class="empty-state"><h2>Пока нет сохраненных чатов</h2><p>Начните загрузку, чтобы увидеть чаты здесь</p></div>'}
        </div>
    </div>
</body>
</html>"""

        with open(index_file, "w", encoding="utf-8") as f:
            f.write(html_content)
