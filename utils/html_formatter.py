"Модуль для форматирования сообщений в HTML."
import html
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse

class HtmlFormatter:
    """Класс для форматирования сообщений в HTML."""

    def __init__(self, found_chat_ids: Optional[Set[int]] = None):
        """
        Инициализация HtmlFormatter.

        Parameters
        ----------
        found_chat_ids: Optional[Set[int]]
            Множество для сбора ID чатов, найденных в ссылках.
        """
        self.found_chat_ids = found_chat_ids if found_chat_ids is not None else set()

    def format_message(self, msg: Dict[str, Any]) -> str:
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
        date_iso = msg.get("date", "")
        time_str = ""
        if date_iso:
            try:
                date_obj = datetime.fromisoformat(str(date_iso).replace("Z", "+00:00"))
                # Внизу сообщения показываем и дату, и время
                time_str = date_obj.strftime("%d.%m.%Y %H:%M")
            except Exception:
                pass

        text = msg.get("text", "")

        # Обработать медиа с превью
        media_html = ""
        if msg.get("downloaded_file"):
            file_path = msg["downloaded_file"]
            # Использовать абсолютный путь с file:// протоколом
            import os
            abs_path = os.path.abspath(file_path) if not os.path.isabs(file_path) else file_path
            file_url = f"file://{abs_path}"

            media_type = msg.get("media_type", "unknown")
            file_name = msg.get("file_name", os.path.basename(file_path))
            file_size = self._coerce_file_size(msg.get("file_size"))

            # Превью для изображений
            if media_type == "photo":
                media_html = f'''
                <div class="media-preview photo-preview">
                    <a href="{html.escape(file_url)}" target="_blank">
                        <img src="{html.escape(file_url)}" alt="Фото" loading="lazy"
                             onerror="this.parentElement.innerHTML='<div class=\'media-error\'>❌ Не удалось загрузить фото</div>'">
                    </a>
                </div>'''

            # Превью для видео
            elif media_type in ["video", "video_note"]:
                duration = msg.get("duration", 0)
                if duration:
                    # Преобразовать в int, если это float
                    duration = int(duration)
                    duration_str = f"{duration // 60}:{duration % 60:02d}"
                else:
                    duration_str = ""
                media_html = f'''
                <div class="media-preview video-preview">
                    <video controls preload="metadata"
                           onerror="this.outerHTML='<div class=\'media-error\'>❌ Не удалось загрузить видео</div>'">
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
            file_size = self._coerce_file_size(msg.get("file_size"))
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
            # Обработать entities, если есть
            entities = msg.get("entities", [])
            if entities:
                text_html = f'<div class="message-text">{self._format_text_with_entities(text, entities, msg.get("chat_id"))}</div>'
            else:
                # Fallback: простая обработка URL через regex
                text_escaped = html.escape(text)
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
        <div class="message-bubble" id="message-{msg_id}" data-message-id="{msg_id}">
            {reply_html}
            {media_html}
            {text_html}
            <div class="message-footer">
                <span class="message-time">{time_str}</span>
                {meta_html}
            </div>
        </div>
        '''

    def _format_text_with_entities(self, text: str, entities: List[Dict[str, Any]], current_chat_id: Optional[int] = None) -> str:
        """
        Форматировать текст с учётом entities (ссылки, форматирование).
        """
        if not entities:
            # Fallback: простая обработка URL через regex
            text_escaped = html.escape(text)
            url_pattern = r'(https?://[^\s]+|tg://[^\s]+)'
            def replace_url_fallback(match):
                url = match.group(1)
                # Извлечь chat_id для добавления в список загрузок
                extracted_chat_id = self._extract_chat_id_from_link(url)
                if extracted_chat_id and extracted_chat_id != current_chat_id:
                    self.found_chat_ids.add(extracted_chat_id)
                converted_url = self._convert_telegram_link(url, current_chat_id)
                return f'<a href="{html.escape(converted_url)}" target="_blank" class="message-link">{html.escape(url)}</a>'
            text_escaped = re.sub(url_pattern, replace_url_fallback, text_escaped)
            return text_escaped

        # Определить, какие части текста обработаны через entities
        processed_ranges = set()
        for entity in entities:
            offset = entity.get("offset", 0)
            length = entity.get("length", 0)
            entity_type = entity.get("type", "")
            # Если это URL entity, отметить диапазон как обработанный
            if entity_type in ("MessageEntityUrl", "MessageEntityTextUrl"):
                for i in range(offset, offset + length):
                    processed_ranges.add(i)

        # Обработать URL, которые не были обработаны через entities (до обработки entities)
        url_pattern = r'(https?://[^\s<>"]+|tg://[^\s<>"]+)'
        url_matches = list(re.finditer(url_pattern, text))
        text_with_urls = text
        offset_adjustments = []  # Список (позиция, смещение) для корректировки индексов entities

        for match in reversed(url_matches):  # Обрабатываем с конца
            start, end = match.span()
            # Проверить, не обработан ли этот URL через entities
            if any(i in processed_ranges for i in range(start, end)):
                continue

            url = match.group(1)
            # Извлечь chat_id для добавления в список загрузок
            extracted_chat_id = self._extract_chat_id_from_link(url)
            if extracted_chat_id and extracted_chat_id != current_chat_id:
                self.found_chat_ids.add(extracted_chat_id)
            converted_url = self._convert_telegram_link(url, current_chat_id)
            replacement = f'<a href="{html.escape(converted_url)}" target="_blank" class="message-link">{html.escape(url)}</a>'
            # Вставить ссылку в текст
            text_with_urls = text_with_urls[:start] + replacement + text_with_urls[end:]
            # Сохранить информацию о смещении для корректировки индексов entities
            offset_adjustments.append((start, len(replacement) - (end - start)))

        # Скорректировать индексы entities после вставки URL
        adjusted_entities = []
        for entity in entities:
            entity_copy = entity.copy()
            offset = entity.get("offset", 0)
            # Применить все смещения, которые произошли до этой позиции
            for adj_pos, adj_offset in offset_adjustments:
                if adj_pos <= offset:
                    offset += adj_offset
            entity_copy["offset"] = offset
            adjusted_entities.append(entity_copy)

        # Сортировать entities по offset (с конца, чтобы не сбить индексы при замене)
        # Важно: сортируем сначала по offset (с конца), затем по длине (большие первыми)
        # чтобы обрабатывать внешние entities перед внутренними
        sorted_entities = sorted(adjusted_entities, key=lambda e: (e.get("offset", 0), -e.get("length", 0)), reverse=True)

        result = text_with_urls
        for entity in sorted_entities:
            offset = entity.get("offset", 0)
            length = entity.get("length", 0)
            entity_type = entity.get("type", "")

            # Проверить границы с учётом уже обработанного текста
            if offset < 0 or offset >= len(result):
                continue
            if offset + length > len(result):
                # Обрезать length до доступного размера
                length = len(result) - offset
            if length <= 0:
                continue

            # Взять текст из уже обработанного result
            entity_text = result[offset:offset + length]

            # Проверить, не содержит ли entity_text уже HTML-теги
            if "<" in entity_text and ">" in entity_text:
                entity_text_escaped = entity_text
            else:
                entity_text_escaped = html.escape(entity_text)

            # Обработать разные типы entities
            html_tag = None
            href = None
            css_class = "message-link"

            if entity_type == "MessageEntityUrl":
                if "<" in entity_text and ">" in entity_text:
                    href = entity_text
                    # Удалить HTML-теги для href
                    href_clean = re.sub(r'<[^>]+>', '', href)
                    if href_clean:
                        href = href_clean
                else:
                    href = entity_text
                # Извлечь chat_id
                extracted_chat_id = self._extract_chat_id_from_link(href)
                if extracted_chat_id and extracted_chat_id != current_chat_id:
                    self.found_chat_ids.add(extracted_chat_id)
                html_tag = f'<a href="{html.escape(href)}" target="_blank" class="{css_class}">{entity_text_escaped}</a>'
            elif entity_type == "MessageEntityTextUrl":
                url = entity.get("url", "")
                if url:
                    extracted_chat_id = self._extract_chat_id_from_link(url)
                    if extracted_chat_id and extracted_chat_id != current_chat_id:
                        self.found_chat_ids.add(extracted_chat_id)
                    href = self._convert_telegram_link(url, current_chat_id)
                    html_tag = f'<a href="{html.escape(href)}" target="_blank" class="{css_class}">{entity_text_escaped}</a>'
            elif entity_type == "MessageEntityMention":
                href = f"https://t.me/{entity_text.lstrip('@')}"
                html_tag = f'<a href="{html.escape(href)}" target="_blank" class="{css_class}">{entity_text_escaped}</a>'
            elif entity_type == "MessageEntityHashtag":
                html_tag = f'<span class="message-hashtag">{entity_text_escaped}</span>'
            elif entity_type == "MessageEntityBold":
                html_tag = f'<strong>{entity_text_escaped}</strong>'
            elif entity_type == "MessageEntityItalic":
                html_tag = f'<em>{entity_text_escaped}</em>'
            elif entity_type == "MessageEntityCode":
                html_tag = f'<code>{entity_text_escaped}</code>'
            elif entity_type == "MessageEntityPre":
                html_tag = f'<pre>{entity_text_escaped}</pre>'
            elif entity_type == "MessageEntityUnderline":
                html_tag = f'<u>{entity_text_escaped}</u>'
            elif entity_type == "MessageEntityStrike":
                html_tag = f'<s>{entity_text_escaped}</s>'
            elif entity_type == "MessageEntityBlockquote":
                html_tag = f'<blockquote>{entity_text_escaped}</blockquote>'
            elif entity_type == "MessageEntitySpoiler":
                html_tag = f'<span class="message-spoiler" onclick="this.classList.toggle(\'revealed\')">{entity_text_escaped}</span>'

            if html_tag:
                if offset < len(result) and offset + length <= len(result):
                    result = result[:offset] + html_tag + result[offset + length:]
                elif offset < len(result):
                    result = result[:offset] + html_tag + result[offset + length:]

        return result

    def _extract_chat_id_from_link(self, url: str) -> Optional[int]:
        """Извлечь chat_id из Telegram ссылки."""
        if url.startswith("tg://"):
            parsed = urlparse(url)
            if parsed.scheme == "tg":
                if parsed.netloc == "openmessage":
                    params = parse_qs(parsed.query)
                    chat_id = params.get("chat_id", [None])[0]
                    if chat_id:
                        try:
                            return int(chat_id)
                        except (ValueError, TypeError):
                            pass

        if url.startswith("https://t.me/") or url.startswith("http://t.me/"):
            pattern = r'https?://t\.me/c/(-?\d+)/(\d+)'
            match = re.match(pattern, url)
            if match:
                chat_id_str, _ = match.groups()
                try:
                    return int(chat_id_str)
                except (ValueError, TypeError):
                    pass
        return None

    def _convert_telegram_link(self, url: str, current_chat_id: Optional[int] = None) -> str:
        """Преобразовать Telegram deep link в ссылку на архивный HTML файл."""
        from utils.history_saver import _archive_chat_id_for_path

        if url.startswith("tg://"):
            parsed = urlparse(url)
            if parsed.scheme == "tg":
                if parsed.netloc == "resolve":
                    params = parse_qs(parsed.query)
                    domain = params.get("domain", [None])[0]
                    post = params.get("post", [None])[0]
                    if domain and post:
                        url = f"https://t.me/{domain}/{post}"
                elif parsed.netloc == "openmessage":
                    params = parse_qs(parsed.query)
                    chat_id = params.get("chat_id", [None])[0]
                    message_id = params.get("message_id", [None])[0]
                    if chat_id and message_id:
                        try:
                            chat_id_int = int(chat_id)
                            path_id = _archive_chat_id_for_path(chat_id_int)
                            archive_file = f"chat_{path_id}.html"
                            return f"{archive_file}#message-{message_id}"
                        except (ValueError, TypeError):
                            pass

        if url.startswith("https://t.me/") or url.startswith("http://t.me/"):
            pattern = r'https?://t\.me/(?:c/)?(-?\d+)/(\d+)'
            match = re.match(pattern, url)
            if match:
                chat_id_str, message_id = match.groups()
                try:
                    chat_id_int = int(chat_id_str)
                    path_id = _archive_chat_id_for_path(chat_id_int)
                    archive_file = f"chat_{path_id}.html"
                    return f"{archive_file}#message-{message_id}"
                except (ValueError, TypeError):
                    pass

            pattern = r'https?://t\.me/([^/]+)/(\d+)'
            match = re.match(pattern, url)
            if match:
                return url

        return url

    @staticmethod
    def _coerce_file_size(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _format_file_size(self, size_bytes: int) -> str:
        size_bytes = self._coerce_file_size(size_bytes)
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def _get_file_icon(self, media_type: str) -> str:
        icons = {
            "photo": "🖼️",
            "video": "🎬",
            "video_note": "🎥",
            "audio": "🎵",
            "voice": "🎤",
            "document": "📄",
        }
        return icons.get(media_type, "📎")