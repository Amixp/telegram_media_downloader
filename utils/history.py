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
        :root {{
            --bg-color: #0f0f0f;
            --chat-bg: #212121;
            --message-bg: #2b2b2b;
            --text-color: #e4e4e4;
            --text-secondary: #8e8e93;
            --accent-color: #8774e1;
            --header-bg: #17212b;
            --border-color: #2f2f2f;
        }}
        
        [data-theme="light"] {{
            --bg-color: #f4f4f5;
            --chat-bg: #ffffff;
            --message-bg: #ffffff;
            --text-color: #000000;
            --text-secondary: #707579;
            --accent-color: #3390ec;
            --header-bg: #ffffff;
            --border-color: #e4e4e5;
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: var(--bg-color);
            color: var(--text-color);
            min-height: 100vh;
        }}
        
        .container {{
            max-width: 900px;
            margin: 0 auto;
            background: var(--chat-bg);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        
        .header {{
            background: var(--header-bg);
            border-bottom: 1px solid var(--border-color);
            padding: 12px 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 100;
            backdrop-filter: blur(10px);
        }}
        
        .header-left {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        
        .back-btn {{
            color: var(--text-color);
            text-decoration: none;
            font-size: 24px;
            transition: opacity 0.2s;
        }}
        
        .back-btn:hover {{
            opacity: 0.7;
        }}
        
        .chat-info {{
            display: flex;
            flex-direction: column;
        }}
        
        .chat-title {{
            font-size: 15px;
            font-weight: 500;
            color: var(--text-color);
        }}
        
        .chat-subtitle {{
            font-size: 13px;
            color: var(--text-secondary);
        }}
        
        .theme-toggle {{
            background: none;
            border: none;
            font-size: 24px;
            cursor: pointer;
            padding: 8px;
            transition: transform 0.2s;
        }}
        
        .theme-toggle:hover {{
            transform: scale(1.1);
        }}
        
        .search-box {{
            padding: 12px 20px;
            background: var(--chat-bg);
            border-bottom: 1px solid var(--border-color);
            position: sticky;
            top: 60px;
            z-index: 99;
            backdrop-filter: blur(10px);
        }}
        
        .search-box input {{
            width: 100%;
            padding: 10px 16px;
            border: 1px solid var(--border-color);
            border-radius: 20px;
            font-size: 14px;
            background: var(--message-bg);
            color: var(--text-color);
            transition: border-color 0.2s;
        }}
        
        .search-box input:focus {{
            outline: none;
            border-color: var(--accent-color);
        }}
        
        .messages {{
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        
        .message-bubble {{
            max-width: 70%;
            background: var(--message-bg);
            border-radius: 12px;
            padding: 8px 12px;
            position: relative;
            box-shadow: 0 1px 2px rgba(0,0,0,0.1);
            animation: fadeIn 0.2s ease-in;
            align-self: flex-start;
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        .message-reply {{
            background: var(--accent-color);
            background: linear-gradient(90deg, var(--accent-color) 3px, transparent 3px);
            padding: 6px 10px;
            padding-left: 14px;
            border-radius: 6px;
            font-size: 13px;
            color: var(--text-secondary);
            margin-bottom: 6px;
        }}
        
        .message-text {{
            font-size: 15px;
            line-height: 1.5;
            word-wrap: break-word;
            white-space: pre-wrap;
            margin: 4px 0;
        }}
        
        .message-link {{
            color: var(--accent-color);
            text-decoration: none;
        }}
        
        .message-link:hover {{
            text-decoration: underline;
        }}
        
        .media-preview {{
            margin: 4px 0;
            border-radius: 8px;
            overflow: hidden;
            max-width: 100%;
        }}
        
        .photo-preview img {{
            display: block;
            max-width: 100%;
            max-height: 500px;
            width: auto;
            height: auto;
            border-radius: 8px;
            cursor: pointer;
            transition: transform 0.2s;
        }}
        
        .photo-preview img:hover {{
            transform: scale(1.02);
        }}
        
        .video-preview {{
            position: relative;
        }}
        
        .video-preview video {{
            display: block;
            max-width: 100%;
            max-height: 500px;
            width: auto;
            border-radius: 8px;
        }}
        
        .video-duration {{
            position: absolute;
            bottom: 8px;
            right: 8px;
            background: rgba(0,0,0,0.7);
            color: white;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
        }}
        
        .media-file {{
            background: var(--message-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            margin: 4px 0;
        }}
        
        .file-download {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px;
            text-decoration: none;
            color: var(--text-color);
            transition: background 0.2s;
        }}
        
        .file-download:hover {{
            background: var(--border-color);
        }}
        
        .file-icon {{
            font-size: 32px;
            flex-shrink: 0;
        }}
        
        .file-info {{
            flex: 1;
            min-width: 0;
        }}
        
        .file-name {{
            font-size: 14px;
            font-weight: 500;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        
        .file-size {{
            font-size: 13px;
            color: var(--text-secondary);
            margin-top: 2px;
        }}
        
        .download-icon {{
            font-size: 20px;
            flex-shrink: 0;
        }}
        
        .not-downloaded {{
            opacity: 0.6;
        }}
        
        .media-error {{
            padding: 20px;
            text-align: center;
            background: var(--border-color);
            border-radius: 8px;
            color: var(--text-secondary);
        }}
        
        .message-footer {{
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 6px;
            margin-top: 4px;
            font-size: 12px;
            color: var(--text-secondary);
        }}
        
        .message-time {{
            font-size: 11px;
        }}
        
        .message-meta {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        
        .meta-views, .meta-forwards {{
            display: flex;
            align-items: center;
            gap: 2px;
        }}
        
        .meta-edited {{
            font-style: italic;
            font-size: 11px;
        }}
        
        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: var(--text-secondary);
        }}
        
        @media (max-width: 768px) {{
            .message-bubble {{
                max-width: 85%;
            }}
        }}
    </style>
</head>
<body data-theme="dark">
    <div class="container">
        <div class="header">
            <div class="header-left">
                <a href="index.html" class="back-btn">←</a>
                <div class="chat-info">
                    <div class="chat-title">{html.escape(chat_title)}</div>
                    <div class="chat-subtitle">{len(messages)} сообщений</div>
                </div>
            </div>
            <button class="theme-toggle" onclick="toggleTheme()" title="Переключить тему">🌓</button>
        </div>
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="🔍 Поиск в чате..." onkeyup="filterMessages()">
        </div>
        <div class="messages" id="messagesContainer">
            {messages_html if messages_html else '<div class="empty-state">Нет сообщений</div>'}
        </div>
    </div>
    <script>
        // Восстановить тему из localStorage
        const savedTheme = localStorage.getItem('theme') || 'dark';
        document.body.setAttribute('data-theme', savedTheme);
        
        function toggleTheme() {{
            const body = document.body;
            const currentTheme = body.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            body.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
        }}
        
        function filterMessages() {{
            const input = document.getElementById('searchInput');
            const filter = input.value.toLowerCase();
            const messages = document.querySelectorAll('.message-bubble');
            
            let visibleCount = 0;
            messages.forEach(message => {{
                const text = message.textContent.toLowerCase();
                const isVisible = text.includes(filter);
                message.style.display = isVisible ? 'flex' : 'none';
                if (isVisible) visibleCount++;
            }});
        }}
        
        // Автоматическая прокрутка вниз при загрузке
        window.addEventListener('load', () => {{
            const container = document.querySelector('.messages');
            container.scrollTop = container.scrollHeight;
        }});
    </script>
</body>
</html>"""

    def _format_message_html(self, msg: Dict[str, Any]) -> str:
        """
        Форматировать одно сообщение в HTML (стиль Telegram Web).

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
        time_str = ""
        if date_str:
            try:
                date_obj = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                date_str = date_obj.strftime("%d.%m.%Y")
                time_str = date_obj.strftime("%H:%M")
            except:
                pass
        
        text = msg.get("text", "")
        
        # Обработать медиа с превью
        media_html = ""
        if msg.get("downloaded_file"):
            file_path = msg["downloaded_file"]
            # Использовать абсолютный путь с file:// протоколом
            abs_path = os.path.abspath(file_path) if not os.path.isabs(file_path) else file_path
            file_url = f"file://{abs_path}"
            
            media_type = msg.get("media_type", "unknown")
            file_name = msg.get("file_name", os.path.basename(file_path))
            file_size = msg.get("file_size", 0)
            
            # Превью для изображений
            if media_type == "photo":
                media_html = f'''
                <div class="media-preview photo-preview">
                    <a href="{html.escape(file_url)}" target="_blank">
                        <img src="{html.escape(file_url)}" alt="Фото" loading="lazy" 
                             onerror="this.parentElement.innerHTML='<div class=\\'media-error\\'>❌ Не удалось загрузить фото</div>'">
                    </a>
                </div>'''
            
            # Превью для видео
            elif media_type in ["video", "video_note"]:
                duration = msg.get("duration", 0)
                duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else ""
                media_html = f'''
                <div class="media-preview video-preview">
                    <video controls preload="metadata" 
                           onerror="this.outerHTML='<div class=\\'media-error\\'>❌ Не удалось загрузить видео</div>'">
                        <source src="{html.escape(file_url)}" type="video/mp4">
                        Ваш браузер не поддерживает видео
                    </video>
                    {f'<div class="video-duration">{duration_str}</div>' if duration_str else ''}
                </div>'''
            
            # Файлы (документы, аудио)
            else:
                size_str = self._format_file_size(file_size)
                icon = self._get_file_icon(media_type)
                media_html = f'''
                <div class="media-file">
                    <a href="{html.escape(file_url)}" target="_blank" class="file-download">
                        <div class="file-icon">{icon}</div>
                        <div class="file-info">
                            <div class="file-name">{html.escape(file_name)}</div>
                            <div class="file-size">{size_str} • {media_type.upper()}</div>
                        </div>
                        <div class="download-icon">⬇️</div>
                    </a>
                </div>'''
        
        elif msg.get("has_media"):
            # Медиа есть, но файл не скачан
            media_type = msg.get("media_type", "unknown")
            file_name = msg.get("file_name", "")
            file_size = msg.get("file_size", 0)
            size_str = self._format_file_size(file_size)
            icon = self._get_file_icon(media_type)
            
            media_html = f'''
            <div class="media-file not-downloaded">
                <div class="file-icon">{icon}</div>
                <div class="file-info">
                    <div class="file-name">{html.escape(file_name) if file_name else f'{media_type.upper()}'}</div>
                    <div class="file-size">{size_str} • Не скачано</div>
                </div>
            </div>'''
        
        # Текст сообщения
        text_html = ""
        if text:
            # Конвертировать ссылки в кликабельные
            text_escaped = html.escape(text)
            import re
            # Простая замена URL
            url_pattern = r'(https?://[^\s]+)'
            text_escaped = re.sub(url_pattern, r'<a href="\1" target="_blank" class="message-link">\1</a>', text_escaped)
            text_html = f'<div class="message-text">{text_escaped}</div>'
        
        # Мета информация
        meta_parts = []
        if msg.get("views"):
            meta_parts.append(f'<span class="meta-views">👁 {msg["views"]}</span>')
        if msg.get("forwards"):
            meta_parts.append(f'<span class="meta-forwards">🔄 {msg["forwards"]}</span>')
        if msg.get("edit_date"):
            meta_parts.append(f'<span class="meta-edited">edited</span>')
        
        meta_html = ""
        if meta_parts:
            meta_html = f'<div class="message-meta">{" ".join(meta_parts)}</div>'
        
        # Ответ на сообщение
        reply_html = ""
        if msg.get("reply_to_msg_id"):
            reply_html = f'<div class="message-reply">↩️ Ответ на сообщение #{msg["reply_to_msg_id"]}</div>'
        
        return f'''
        <div class="message-bubble" data-message-id="{msg_id}">
            {reply_html}
            {media_html}
            {text_html}
            <div class="message-footer">
                <span class="message-time">{time_str}</span>
                {meta_html}
            </div>
        </div>
        '''
    
    def _format_file_size(self, size_bytes: int) -> str:
        """Форматировать размер файла."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
    
    def _get_file_icon(self, media_type: str) -> str:
        """Получить иконку для типа файла."""
        icons = {
            "photo": "🖼️",
            "video": "🎬",
            "video_note": "🎥",
            "audio": "🎵",
            "voice": "🎤",
            "document": "📄",
        }
        return icons.get(media_type, "📎")

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

            # Получить первую букву для аватара
            first_letter = title[0].upper() if title else "?"
            
            chats_html += f"""
            <a href="chat_{chat_id}.html" class="chat-card">
                <div class="chat-avatar">{first_letter}</div>
                <div class="chat-name">{html.escape(title)}</div>
                <div class="chat-info">
                    <span>💬 {count}</span>
                    <span>{date_str}</span>
                </div>
                <div class="chat-stats">
                    <div class="stat-item">
                        <span>📊</span>
                        <span>{count} сообщений</span>
                    </div>
                </div>
            </a>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram History Viewer</title>
    <style>
        :root {{
            --bg-color: #0f0f0f;
            --card-bg: #212121;
            --text-color: #e4e4e4;
            --text-secondary: #8e8e93;
            --accent-color: #8774e1;
            --border-color: #2f2f2f;
        }}
        
        [data-theme="light"] {{
            --bg-color: #f4f4f5;
            --card-bg: #ffffff;
            --text-color: #000000;
            --text-secondary: #707579;
            --accent-color: #3390ec;
            --border-color: #e4e4e5;
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: var(--bg-color);
            color: var(--text-color);
            padding: 20px;
            min-height: 100vh;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        .header {{
            text-align: center;
            margin-bottom: 40px;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 20px;
        }}
        
        .header h1 {{
            font-size: 42px;
            font-weight: 600;
        }}
        
        .header p {{
            font-size: 16px;
            color: var(--text-secondary);
        }}
        
        .theme-toggle {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 10px 20px;
            font-size: 20px;
            cursor: pointer;
            transition: transform 0.2s;
        }}
        
        .theme-toggle:hover {{
            transform: scale(1.05);
        }}
        
        .chats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 20px;
        }}
        
        .chat-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            transition: transform 0.2s, border-color 0.2s;
            cursor: pointer;
            text-decoration: none;
            color: var(--text-color);
            display: flex;
            flex-direction: column;
        }}
        
        .chat-card:hover {{
            transform: translateY(-4px);
            border-color: var(--accent-color);
        }}
        
        .chat-avatar {{
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: linear-gradient(135deg, var(--accent-color), #6b5ce7);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            margin-bottom: 16px;
        }}
        
        .chat-name {{
            font-size: 16px;
            font-weight: 500;
            margin-bottom: 8px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        
        .chat-info {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 13px;
            color: var(--text-secondary);
            margin-bottom: 12px;
        }}
        
        .chat-stats {{
            display: flex;
            gap: 16px;
            font-size: 13px;
            color: var(--text-secondary);
            padding-top: 12px;
            border-top: 1px solid var(--border-color);
            margin-top: auto;
        }}
        
        .stat-item {{
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        
        .empty-state {{
            text-align: center;
            padding: 80px 20px;
            color: var(--text-secondary);
            background: var(--card-bg);
            border: 2px dashed var(--border-color);
            border-radius: 16px;
        }}
        
        .empty-state-icon {{
            font-size: 64px;
            margin-bottom: 20px;
        }}
        
        .empty-state h2 {{
            font-size: 24px;
            margin-bottom: 12px;
            color: var(--text-color);
        }}
        
        @media (max-width: 768px) {{
            .chats-grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body data-theme="dark">
    <div class="container">
        <div class="header">
            <h1>📱 Telegram History</h1>
            <p>Просмотр сохранённых чатов</p>
            <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
        </div>
        <div class="chats-grid">
            {chats_html if chats_html else '''
            <div class="empty-state" style="grid-column: 1 / -1;">
                <div class="empty-state-icon">💬</div>
                <h2>Пока нет сохранённых чатов</h2>
                <p>Запустите загрузку медиа, чтобы увидеть историю чатов здесь</p>
            </div>
            '''}
        </div>
    </div>
    <script>
        const savedTheme = localStorage.getItem('theme') || 'dark';
        document.body.setAttribute('data-theme', savedTheme);
        
        function toggleTheme() {{
            const body = document.body;
            const currentTheme = body.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            body.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
        }}
    </script>
</body>
</html>"""

        with open(index_file, "w", encoding="utf-8") as f:
            f.write(html_content)
