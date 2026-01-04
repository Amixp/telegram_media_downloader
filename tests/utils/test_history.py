"""Unittest module for history HTML generation."""

import sys
import tempfile
import unittest

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

