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
    def test_format_message_html_shows_date_and_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history = MessageHistory(base_directory=tmpdir, history_format="html")
            html_fragment = history._format_message_html(
                {
                    "id": 1,
                    "date": "2020-01-02T03:04:05+00:00",
                    "text": "x",
                    "has_media": False,
                }
            )

        self.assertIn("02.01.2020 03:04", html_fragment)

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
            # Пути теперь без минуса (abs(chat_id))
            old_chat_id = -100
            old_path_id = abs(old_chat_id)
            with open(os.path.join(history_dir, f"chat_{old_path_id}.jsonl"), "w", encoding="utf-8") as f:
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
            with open(os.path.join(history_dir, f"chat_{old_path_id}.html"), "w", encoding="utf-8") as f:
                f.write("<html>old</html>")

            # Новый запуск: пишем другой чат (в памяти) и генерируем индекс
            new_chat_id = -200
            new_path_id = abs(new_chat_id)
            with open(os.path.join(history_dir, f"chat_{new_path_id}.jsonl"), "w", encoding="utf-8") as f:
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
        # Пути теперь без минуса
        self.assertIn(f"chat_{old_path_id}.html", html_text)
        self.assertIn("Old Chat", html_text)
        self.assertIn(f"chat_{new_path_id}.html", html_text)
        self.assertIn("New Chat", html_text)

    def test_generate_chat_html_skips_bad_jsonl_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = os.path.join(tmpdir, "history")
            os.makedirs(history_dir, exist_ok=True)

            chat_id = -777
            path_id = abs(chat_id)  # Пути теперь без минуса
            jsonl_path = os.path.join(history_dir, f"chat_{path_id}.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                f.write('{"id": 1, "date": "2020-01-01T00:00:00+00:00", "text": "ok", "chat_id": -777, "chat_title": "T"}\n')
                f.write("{broken json\n")
                f.write('{"id": 2, "date": "2020-01-01T00:00:01+00:00", "text": "ok2", "chat_id": -777, "chat_title": "T"}\n')

            history = MessageHistory(base_directory=tmpdir, history_format="html")
            history._generate_chat_html(chat_id)

            html_path = os.path.join(history_dir, f"chat_{path_id}.html")
            self.assertTrue(os.path.exists(html_path))

    def test_save_batch_skips_duplicates(self):
        """Проверка дублей: если все сообщения уже есть в архиве, сохранение пропускается."""
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = os.path.join(tmpdir, "history")
            os.makedirs(history_dir, exist_ok=True)

            chat_id = -888
            path_id = abs(chat_id)
            jsonl_path = os.path.join(history_dir, f"chat_{path_id}.jsonl")

            # Создать архив с сообщениями
            with open(jsonl_path, "w", encoding="utf-8") as f:
                f.write('{"id": 1, "date": "2020-01-01T00:00:00+00:00", "text": "msg1", "chat_id": -888, "chat_title": "T"}\n')
                f.write('{"id": 2, "date": "2020-01-01T00:00:01+00:00", "text": "msg2", "chat_id": -888, "chat_title": "T"}\n')

            history = MessageHistory(base_directory=tmpdir, history_format="jsonl")

            # Попытка сохранить те же сообщения (дубли)
            from datetime import datetime, timezone
            from unittest.mock import Mock

            msg1 = Mock()
            msg1.id = 1
            msg1.date = datetime(2020, 1, 1, tzinfo=timezone.utc)
            msg1.message = "msg1"
            msg1.media = None
            msg1.sender_id = 0
            msg1.reply_to = None
            msg1.edit_date = None
            msg1.views = None
            msg1.forwards = None

            msg2 = Mock()
            msg2.id = 2
            msg2.date = datetime(2020, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
            msg2.message = "msg2"
            msg2.media = None
            msg2.sender_id = 0
            msg2.reply_to = None
            msg2.edit_date = None
            msg2.views = None
            msg2.forwards = None

            # Сохранить (должно пропустить, т.к. дубли)
            history.save_batch([msg1, msg2], chat_id, "T")

            # Проверить, что архив не изменился (всего 2 строки)
            with open(jsonl_path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            self.assertEqual(len(lines), 2, "Архив не должен был измениться (дубли пропущены)")

            # Теперь добавить новое сообщение (не дубль)
            msg3 = Mock()
            msg3.id = 3
            msg3.date = datetime(2020, 1, 1, 0, 0, 2, tzinfo=timezone.utc)
            msg3.message = "msg3"
            msg3.media = None
            msg3.sender_id = 0
            msg3.reply_to = None
            msg3.edit_date = None
            msg3.views = None
            msg3.forwards = None

            history.save_batch([msg3], chat_id, "T")

            # Проверить, что новое сообщение добавилось
            with open(jsonl_path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            self.assertEqual(len(lines), 3, "Новое сообщение должно было добавиться")

