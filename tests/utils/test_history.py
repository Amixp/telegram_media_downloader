"""Unittest module for history HTML generation."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.append("..")  # Adds higher directory to python modules path.

try:
    from utils.history import MessageHistory

    _HISTORY_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency (telethon) may be missing
    MessageHistory = None  # type: ignore
    _HISTORY_AVAILABLE = False


@unittest.skipUnless(_HISTORY_AVAILABLE, "utils.history/telethon not available")
class HistoryTestCase(unittest.TestCase):
    def test_format_message_html_file_size_none_not_downloaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history = MessageHistory(base_directory=tmpdir, history_format="html")
            html_fragment = history._format_message_html(
                {
                    "id": 1,
                    "date": None,
                    "text": "",
                    "has_media": True,
                    "media_type": "photo",
                    "file_name": "image.jpg",
                    "file_size": None,  # regression: JSONL may contain null
                }
            )

        self.assertIn("0 B", html_fragment)

    def test_format_message_html_file_size_none_downloaded_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history = MessageHistory(base_directory=tmpdir, history_format="html")
            html_fragment = history._format_message_html(
                {
                    "id": 2,
                    "date": None,
                    "text": "doc",
                    "has_media": True,
                    "media_type": "document",
                    "file_name": "file.pdf",
                    "file_size": None,  # regression: JSONL may contain null
                    "downloaded_file": "/tmp/file.pdf",
                }
            )

        self.assertIn("0 B", html_fragment)

    def test_index_html_preserves_existing_chats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = os.path.join(tmpdir, "history")
            os.makedirs(history_dir, exist_ok=True)

            # Старый чат уже есть в архиве (JSONL + HTML)
            old_chat_id = -100
            with open(os.path.join(history_dir, f"chat_{old_chat_id}.jsonl"), "w", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "id": 1,
                            "date": "2020-01-01T00:00:00+00:00",
                            "text": "old",
                            "chat_id": old_chat_id,
                            "chat_title": "Old Chat",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            with open(os.path.join(history_dir, f"chat_{old_chat_id}.html"), "w", encoding="utf-8") as f:
                f.write("<html>old</html>")

            # Новый запуск: пишем другой чат (в памяти) и генерируем индекс
            new_chat_id = -200
            with open(os.path.join(history_dir, f"chat_{new_chat_id}.jsonl"), "w", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "id": 1,
                            "date": "2021-01-01T00:00:00+00:00",
                            "text": "new",
                            "chat_id": new_chat_id,
                            "chat_title": "New Chat",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

            history = MessageHistory(base_directory=tmpdir, history_format="html")
            history.chats_info = {
                new_chat_id: {
                    "title": "New Chat",
                    "message_count": 1,
                    "last_message_date": datetime(2021, 1, 1, tzinfo=timezone.utc),
                }
            }
            history._generate_index_html()

            index_path = os.path.join(history_dir, "index.html")
            with open(index_path, "r", encoding="utf-8") as f:
                html_text = f.read()

        # В индексе должен остаться старый чат, хотя он не был в chats_info текущего запуска
        self.assertIn(f"chat_{old_chat_id}.html", html_text)
        self.assertIn("Old Chat", html_text)
        self.assertIn(f"chat_{new_chat_id}.html", html_text)
        self.assertIn("New Chat", html_text)

