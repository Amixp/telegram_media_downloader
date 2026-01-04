"""Unittest module for DownloadManager (media_downloader.py)."""

import asyncio
import os
import platform
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import yaml
from telethon.errors import FileReferenceExpiredError
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from media_downloader import DownloadManager
from utils.config import ConfigManager


MOCK_DIR: str = "/root/project"
if platform.system() == "Windows":
    MOCK_DIR = "\\root\\project"


def platform_generic_path(_path: str) -> str:
    platform_specific_path: str = _path
    if platform.system() == "Windows":
        platform_specific_path = platform_specific_path.replace("/", "\\")
    return platform_specific_path


class Chat:
    def __init__(self, chat_id):
        self.id = chat_id


class MockMessage:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id")
        self.media = kwargs.get("media")
        self.audio = kwargs.get("audio", None)
        self.document = kwargs.get("document", None)
        self.photo = kwargs.get("photo", None)
        self.video = kwargs.get("video", None)
        self.voice = kwargs.get("voice", None)
        self.video_note = kwargs.get("video_note", None)
        self.chat = Chat(kwargs.get("chat_id", 123456))
        self.date = kwargs.get("date", datetime.now(timezone.utc))
        self.text = kwargs.get("text", "")

        if self.photo is not None:
            self.media = mock.Mock(spec=MessageMediaPhoto, photo=self.photo)
        elif self.document or self.audio or self.video or self.voice or self.video_note:
            media_obj = self.document or self.audio or self.video or self.voice or self.video_note
            self.media = mock.Mock(spec=MessageMediaDocument, document=media_obj)
            if self.video:
                self.document = self.video
            elif self.audio:
                self.document = self.audio
            elif self.voice:
                self.document = self.voice
            elif self.video_note:
                self.document = self.video_note
        else:
            self.media = None


class MockAudio:
    def __init__(self, **kwargs):
        self.file_name = kwargs.get("file_name", "test.mp3")
        self.mime_type = kwargs["mime_type"]
        self.id = 123
        self.attributes = kwargs.get("attributes", [mock.Mock(file_name=self.file_name)])


class MockDocument:
    def __init__(self, **kwargs):
        self.file_name = kwargs.get("file_name", "test.pdf")
        self.mime_type = kwargs["mime_type"]
        self.id = 123
        self.attributes = kwargs.get("attributes", [mock.Mock(file_name=self.file_name)])


class MockPhoto:
    def __init__(self, **kwargs):
        self.date = kwargs["date"]
        self.id = 123


class MockVoice:
    def __init__(self, **kwargs):
        self.mime_type = kwargs["mime_type"]
        self.date = kwargs["date"]
        self.id = 123
        self.attributes = []


class MockVideo:
    def __init__(self, **kwargs):
        self.file_name = kwargs.get("file_name", "test.mp4")
        self.mime_type = kwargs["mime_type"]
        self.id = 123
        self.size = kwargs.get("size", 1024)

        class VideoAttr:
            def __init__(self):
                self.voice = None
                self.round_message = False

        self.attributes = [VideoAttr()]


class MockVideoNote:
    def __init__(self, **kwargs):
        self.mime_type = kwargs["mime_type"]
        self.date = kwargs["date"]
        self.id = 123
        self.attributes = []


class MockClient:
    async def get_messages(self, *args, **kwargs):
        ids = kwargs.get("ids", kwargs.get("message_ids"))
        return [
            MockMessage(
                id=ids,
                media=True,
                chat_id=123456,
                video=MockVideo(file_name="sample_video.mov", mime_type="video/mov"),
            )
        ]

    async def download_media(self, message_or_media, file=None, **kwargs):
        msg = message_or_media
        if msg.id in [7, 8]:
            raise FileReferenceExpiredError(request=None)
        if msg.id == 9:
            raise Exception("Unauthorized")
        if msg.id == 11:
            raise TimeoutError
        if msg.id == 13:
            return None
        return file or "downloaded"

    def iter_messages(self, chat_id, min_id=0, reverse=False):  # noqa: ANN001
        # Минимальная заглушка для тестов: возвращает async-генератор
        self.last_iter_params = {"chat_id": chat_id, "min_id": min_id, "reverse": reverse}

        async def _gen():
            if hasattr(self, "_iter_messages"):
                for m in self._iter_messages:  # type: ignore[attr-defined]
                    yield m
            return

        return _gen()


class MediaDownloaderTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)

    def _make_manager(self) -> DownloadManager:
        tmpdir = tempfile.mkdtemp(prefix="tmd-test-config-")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        cfg_path = os.path.join(tmpdir, "config.yaml")
        cfg = {
            "api_id": 1,
            "api_hash": "x",
            "language": "ru",
            "media_types": ["all"],
            "file_formats": {"audio": ["all"], "document": ["all"], "video": ["all"]},
            "download_settings": {"skip_duplicates": False},
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)
        mgr = ConfigManager(config_path=cfg_path)
        mgr.load()
        return DownloadManager(mgr)

    @mock.patch("media_downloader.THIS_DIR", new=MOCK_DIR)
    def test_get_media_meta(self):
        dm = self._make_manager()

        msg = MockMessage(
            id=1,
            media=True,
            voice=MockVoice(mime_type="audio/ogg", date=datetime(2019, 7, 25, 14, 53, 50)),
        )
        file_name, ext = self.loop.run_until_complete(dm._get_media_meta(msg.voice, "voice"))  # type: ignore
        self.assertEqual(
            (platform_generic_path("/root/project/voice/voice_2019-07-25_14-53-50.ogg"), "ogg"),
            (file_name, ext),
        )

        msg = MockMessage(id=2, media=True, photo=MockPhoto(date=datetime(2019, 8, 5, 14, 35, 12)))
        file_name, ext = self.loop.run_until_complete(dm._get_media_meta(msg.photo, "photo"))  # type: ignore
        self.assertEqual(
            (platform_generic_path("/root/project/photo/photo_123"), "jpg"),
            (file_name, ext),
        )

        msg = MockMessage(
            id=3,
            media=True,
            document=MockDocument(file_name="sample_document.pdf", mime_type="application/pdf"),
        )
        file_name, ext = self.loop.run_until_complete(dm._get_media_meta(msg.document, "document"))  # type: ignore
        self.assertEqual(
            (platform_generic_path("/root/project/document/sample_document.pdf"), "pdf"),
            (file_name, ext),
        )

        msg = MockMessage(
            id=4,
            media=True,
            audio=MockAudio(file_name="sample_audio.mp3", mime_type="audio/mp3"),
        )
        file_name, ext = self.loop.run_until_complete(dm._get_media_meta(msg.audio, "audio"))  # type: ignore
        self.assertEqual(
            (platform_generic_path("/root/project/audio/sample_audio.mp3"), "mp3"),
            (file_name, ext),
        )

        msg = MockMessage(id=5, media=True, video=MockVideo(mime_type="video/mp4"))
        file_name, ext = self.loop.run_until_complete(dm._get_media_meta(msg.video, "video"))  # type: ignore
        self.assertEqual(
            (platform_generic_path("/root/project/video/video_123"), "mp4"),
            (file_name, ext),
        )

        msg = MockMessage(
            id=6,
            media=True,
            video_note=MockVideoNote(mime_type="video/mp4", date=datetime(2019, 7, 25, 14, 53, 50)),
        )
        file_name, ext = self.loop.run_until_complete(dm._get_media_meta(msg.video_note, "video_note"))  # type: ignore
        self.assertEqual(
            (
                platform_generic_path("/root/project/video_note/video_note_2019-07-25_14-53-50.mp4"),
                "mp4",
            ),
            (file_name, ext),
        )

    def test_can_download(self):
        dm = self._make_manager()
        file_formats = {"audio": ["mp3"], "video": ["mp4"], "document": ["all"]}
        self.assertTrue(dm._can_download("audio", file_formats, "mp3"))  # type: ignore
        self.assertFalse(dm._can_download("audio", file_formats, "ogg"))  # type: ignore
        self.assertTrue(dm._can_download("document", file_formats, "pdf"))  # type: ignore

    def test_get_media_type(self):
        dm = self._make_manager()

        msg = MockMessage(id=1, media=True, photo=MockPhoto(date=datetime(2019, 8, 5, 14, 35, 12)))
        self.assertEqual(dm.get_media_type(msg), "photo")

        doc_attr = mock.Mock()
        doc_attr.voice = None
        doc_attr.round_message = None
        msg = MockMessage(
            id=2,
            media=True,
            document=MockDocument(mime_type="application/pdf", attributes=[doc_attr]),
        )
        self.assertEqual(dm.get_media_type(msg), "document")

        audio_attr = mock.Mock()
        audio_attr.voice = False
        audio_attr.round_message = None
        msg = MockMessage(id=3, media=True, document=MockDocument(mime_type="audio/mp3", attributes=[audio_attr]))
        self.assertEqual(dm.get_media_type(msg), "audio")

        voice_attr = mock.Mock()
        voice_attr.voice = True
        voice_attr.round_message = None
        msg = MockMessage(id=4, media=True, document=MockDocument(mime_type="audio/ogg", attributes=[voice_attr]))
        self.assertEqual(dm.get_media_type(msg), "voice")

        video_attr = mock.Mock()
        video_attr.voice = None
        video_attr.round_message = False
        msg = MockMessage(id=5, media=True, document=MockDocument(mime_type="video/mp4", attributes=[video_attr]))
        self.assertEqual(dm.get_media_type(msg), "video")

        vn_attr = mock.Mock()
        vn_attr.voice = None
        vn_attr.round_message = True
        msg = MockMessage(id=6, media=True, document=MockDocument(mime_type="video/mp4", attributes=[vn_attr]))
        self.assertEqual(dm.get_media_type(msg), "video_note")

        msg = MockMessage(id=7, media=None)
        self.assertIsNone(dm.get_media_type(msg))

    def test_is_exist(self):
        dm = self._make_manager()
        this_dir = os.path.dirname(os.path.abspath(__file__))
        self.assertTrue(dm._is_exist(os.path.join(this_dir, "__init__.py")))  # type: ignore
        self.assertFalse(dm._is_exist(os.path.join(this_dir, "init.py")))  # type: ignore
        self.assertFalse(dm._is_exist(this_dir))  # type: ignore

    def test_progress_callback(self):
        dm = self._make_manager()
        from tqdm import tqdm

        with tqdm(total=100, unit="B", unit_scale=True, desc="Test") as pbar:
            dm._progress_callback(0, 100, pbar)  # type: ignore
            self.assertEqual(pbar.total, 100)
            self.assertEqual(pbar.n, 0)
            dm._progress_callback(50, 100, pbar)  # type: ignore
            self.assertEqual(pbar.n, 50)

    @mock.patch("media_downloader.tqdm")
    @mock.patch("media_downloader.THIS_DIR", new=MOCK_DIR)
    def test_download_media(self, mock_tqdm):
        dm = self._make_manager()
        client = MockClient()

        mock_pbar = mock.Mock()
        mock_tqdm.return_value.__enter__ = mock.Mock(return_value=mock_pbar)
        mock_tqdm.return_value.__exit__ = mock.Mock(return_value=None)

        msg = MockMessage(
            id=5,
            media=True,
            chat_id=123456,
            video=MockVideo(file_name="sample_video.mp4", mime_type="video/mp4", size=1024),
        )
        result = self.loop.run_until_complete(dm.download_media(client, msg, ["video"], {"video": ["all"]}))
        self.assertEqual(result, 5)

        msg_refetch = MockMessage(
            id=7,
            media=True,
            chat_id=123456,
            video=MockVideo(file_name="sample_video.mov", mime_type="video/mov", size=1024),
        )
        result = self.loop.run_until_complete(dm.download_media(client, msg_refetch, ["video"], {"video": ["all"]}))
        self.assertEqual(result, 7)

    @classmethod
    def tearDownClass(cls):
        asyncio.set_event_loop(None)
        cls.loop.close()

    def test_history_rebuild_if_missing_resets_min_id(self):
        # Конфиг: история включена, но архив отсутствует, last_read_message_id уже "в конце"
        tmpdir = tempfile.mkdtemp(prefix="tmd-test-history-missing-")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        cfg_path = os.path.join(tmpdir, "config.yaml")
        cfg = {
            "api_id": 1,
            "api_hash": "x",
            "language": "ru",
            "media_types": ["all"],
            "file_formats": {"audio": ["all"], "document": ["all"], "video": ["all"]},
            "download_settings": {
                "skip_duplicates": False,
                "download_message_history": True,
                "history_format": "html",
                "history_directory": "history",
                "base_directory": tmpdir,
                "history_rebuild_if_missing": True,
            },
            "chats": [
                {"chat_id": 123456, "title": "T", "last_read_message_id": 500, "ids_to_retry": [], "enabled": True}
            ],
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

        mgr = ConfigManager(config_path=cfg_path)
        mgr.load()
        dm = DownloadManager(mgr)
        client = MockClient()
        client._iter_messages = []  # type: ignore[attr-defined]

        # Файла chat_123456.jsonl нет -> ожидаем min_id=1 (сброс)
        self.loop.run_until_complete(dm.begin_import_chat(client, 123456, "T", pagination_limit=100))
        self.assertEqual(client.last_iter_params["min_id"], 1)

    def test_history_rebuild_if_missing_keeps_min_id_when_file_exists(self):
        tmpdir = tempfile.mkdtemp(prefix="tmd-test-history-exists-")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        history_dir = os.path.join(tmpdir, "history")
        os.makedirs(history_dir, exist_ok=True)
        # Создать пустой JSONL как "архив существует"
        with open(os.path.join(history_dir, "chat_123456.jsonl"), "w", encoding="utf-8") as f:
            f.write("")

        cfg_path = os.path.join(tmpdir, "config.yaml")
        cfg = {
            "api_id": 1,
            "api_hash": "x",
            "language": "ru",
            "media_types": ["all"],
            "file_formats": {"audio": ["all"], "document": ["all"], "video": ["all"]},
            "download_settings": {
                "skip_duplicates": False,
                "download_message_history": True,
                "history_format": "html",
                "history_directory": "history",
                "base_directory": tmpdir,
                "history_rebuild_if_missing": True,
            },
            "chats": [
                {"chat_id": 123456, "title": "T", "last_read_message_id": 500, "ids_to_retry": [], "enabled": True}
            ],
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

        mgr = ConfigManager(config_path=cfg_path)
        mgr.load()
        dm = DownloadManager(mgr)
        client = MockClient()
        client._iter_messages = []  # type: ignore[attr-defined]

        # Файл есть -> ожидаем min_id=501 (как обычно)
        self.loop.run_until_complete(dm.begin_import_chat(client, 123456, "T", pagination_limit=100))
        self.assertEqual(client.last_iter_params["min_id"], 501)
