"""Unittest module for DownloadManager (media_downloader.py)."""

import asyncio
import json
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
from utils.validation import validate_archive_file


MOCK_DIR: str = "/root/project"
if platform.system() == "Windows":
    MOCK_DIR = "\\root\\project"


def platform_generic_path(_path: str) -> str:
    platform_specific_path: str = _path
    if platform.system() == "Windows":
        platform_specific_path = platform_specific_path.replace("/", "\\")
    return platform_specific_path


class Chat:
    def __init__(self, chat_id, title=None):
        self.id = chat_id
        self.title = title or ""


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
        self.chat = Chat(kwargs.get("chat_id", 123456), kwargs.get("title"))
        self.date = kwargs.get("date", datetime.now(timezone.utc))
        self.text = kwargs.get("text", "")
        self.message = kwargs.get("message", self.text)
        self.sender_id = kwargs.get("sender_id", 0)
        self.reply_to = kwargs.get("reply_to")
        self.edit_date = kwargs.get("edit_date")

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
        # Создать валидный JSONL (хотя бы одна строка) — иначе жёсткая проверка отклонит
        with open(os.path.join(history_dir, "chat_123456.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"id": 1, "text": "ok"}\n')

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

    def test_archive_search_alternative_path(self):
        """Поиск архива: на диске chat_999.jsonl (путь без минуса), в конфиге chat_id=999 — находим."""
        tmpdir = tempfile.mkdtemp(prefix="tmd-test-history-altpath-")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        history_dir = os.path.join(tmpdir, "history")
        os.makedirs(history_dir, exist_ok=True)
        # Теперь пути без минуса (abs(chat_id))
        with open(os.path.join(history_dir, "chat_999.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"id": 1, "text": "ok", "chat_id": 999}\n')

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
                {"chat_id": 999, "title": "T", "last_read_message_id": 500, "ids_to_retry": [], "enabled": True}
            ],
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

        mgr = ConfigManager(config_path=cfg_path)
        mgr.load()
        dm = DownloadManager(mgr)
        client = MockClient()
        client._iter_messages = []

        self.loop.run_until_complete(dm.begin_import_chat(client, 999, "T", pagination_limit=100))
        self.assertEqual(client.last_iter_params["min_id"], 501)

    def test_archive_save_and_validate(self):
        """Сохранение архива: save_batch создаёт chat_{id}.jsonl, validate_archive_file проходит."""
        tmpdir = tempfile.mkdtemp(prefix="tmd-test-history-save-")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        history_dir = os.path.join(tmpdir, "history")
        os.makedirs(history_dir, exist_ok=True)
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
            },
            "chats": [{"chat_id": -100222, "title": "Test", "last_read_message_id": 0, "ids_to_retry": [], "enabled": True}],
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

        mgr = ConfigManager(config_path=cfg_path)
        mgr.load()
        dm = DownloadManager(mgr)
        client = MockClient()
        # Одно сообщение без медиа — только сохранение в архив
        client._iter_messages = [
            MockMessage(id=1, media=False, chat_id=-100222, text="hello"),
        ]

        self.loop.run_until_complete(dm.begin_import_chat(client, -100222, "Test", pagination_limit=100))

        jsonl_path = os.path.join(history_dir, "chat_100222.jsonl")
        self.assertTrue(os.path.isfile(jsonl_path), f"Ожидается файл архива: {jsonl_path}")
        self.assertTrue(validate_archive_file(jsonl_path, "jsonl"), "Архив должен проходить валидацию")

    def test_archive_download_and_save_integration(self):
        """Загрузка и сохранение: итерация сообщений → process_messages → save_batch → архив на диске."""
        tmpdir = tempfile.mkdtemp(prefix="tmd-test-history-dl-")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        history_dir = os.path.join(tmpdir, "history")
        os.makedirs(history_dir, exist_ok=True)
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
                "history_format": "json",
                "history_directory": "history",
                "base_directory": tmpdir,
            },
            "chats": [{"chat_id": -100333, "title": "DL", "last_read_message_id": 0, "ids_to_retry": [], "enabled": True}],
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

        mgr = ConfigManager(config_path=cfg_path)
        mgr.load()
        dm = DownloadManager(mgr)
        client = MockClient()
        client._iter_messages = [
            MockMessage(id=10, media=False, chat_id=-100333, text="a"),
            MockMessage(id=11, media=False, chat_id=-100333, text="b"),
        ]

        self.loop.run_until_complete(dm.begin_import_chat(client, -100333, "DL", pagination_limit=100))

        jsonl_path = os.path.join(history_dir, "chat_100333.jsonl")
        self.assertTrue(os.path.isfile(jsonl_path), f"Ожидается файл архива: {jsonl_path}")
        with open(jsonl_path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        self.assertGreaterEqual(len(lines), 2, "В архиве должно быть не менее 2 сообщений")
        self.assertTrue(validate_archive_file(jsonl_path, "jsonl"), "Архив должен проходить валидацию")

    def test_find_file_in_archive_by_name_and_size(self):
        """Поиск файла в архиве по имени и размеру: если файл уже скачан и записан в архив, пропустить скачивание."""
        tmpdir = tempfile.mkdtemp(prefix="tmd-test-archive-find-")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        history_dir = os.path.join(tmpdir, "history")
        os.makedirs(history_dir, exist_ok=True)

        # Создать архив с записью о скачанном файле
        chat_id = -100444
        path_id = abs(chat_id)
        jsonl_path = os.path.join(history_dir, f"chat_{path_id}.jsonl")
        existing_file_path = os.path.join(tmpdir, "documents", "test_file.pdf")
        os.makedirs(os.path.dirname(existing_file_path), exist_ok=True)
        with open(existing_file_path, "wb") as f:
            f.write(b"test content" * 100)  # ~1200 байт
        file_size = os.path.getsize(existing_file_path)

        # Записать в архив информацию о файле
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "id": 100,
                        "date": "2020-01-01T00:00:00+00:00",
                        "text": "test",
                        "chat_id": chat_id,
                        "chat_title": "Test",
                        "has_media": True,
                        "media_type": "document",
                        "file_name": "test_file.pdf",
                        "file_size": file_size,
                        "downloaded_file": existing_file_path,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

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
                "history_format": "jsonl",
                "history_directory": "history",
                "base_directory": tmpdir,
                "validate_downloads": True,
            },
            "chats": [
                {"chat_id": chat_id, "title": "Test", "last_read_message_id": 0, "ids_to_retry": [], "enabled": True}
            ],
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

        mgr = ConfigManager(config_path=cfg_path)
        mgr.load()
        dm = DownloadManager(mgr)

        # Проверить поиск файла в архиве
        found_path = dm._find_file_in_archive(chat_id, "test_file.pdf", file_size)
        self.assertEqual(found_path, existing_file_path, "Файл должен быть найден в архиве")

        # Проверить, что файл будет пропущен при скачивании
        # Создать сообщение с таким же именем и размером
        mock_doc = mock.Mock()
        mock_doc.size = file_size
        mock_doc.mime_type = "application/pdf"
        mock_doc.attributes = [mock.Mock(file_name="test_file.pdf")]

        mock_msg = MockMessage(
            id=101,
            media=True,
            chat_id=chat_id,
            document=mock_doc,
        )

        # Проверить существующий файл (должен найти в архиве)
        expected_path = os.path.join(tmpdir, "documents", "test_file.pdf")
        existing = dm._check_existing_file(
            expected_path,
            "document",
            file_size,
            chat_id=chat_id,
            file_name="test_file.pdf",
        )
        self.assertEqual(existing, existing_file_path, "Файл должен быть найден через архив")
