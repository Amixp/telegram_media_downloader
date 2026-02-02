import unittest
from unittest import mock
import os
import sys
import asyncio
import tempfile
import yaml
from datetime import datetime, timezone
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from fastapi.testclient import TestClient

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.downloader import DownloadManager
from utils.config import ConfigManager
from web.app import app
from utils.media_utils import get_media_type

class MockPhotoSize:
    def __init__(self, size):
        self.size = size

class MockPhoto:
    def __init__(self, sizes):
        self.sizes = sizes
        self.id = 123
        self.date = datetime.now(timezone.utc)

class MockMessage:
    def __init__(self, photo=None, document=None):
        self.photo = photo
        self.document = document
        if self.photo:
            self.media = MessageMediaPhoto(photo=self.photo)
        elif self.document:
            self.media = MessageMediaDocument(document=self.document)
        else:
            self.media = None
        self.id = 456
        self.chat = mock.Mock(id=789)
        self.date = datetime.now(timezone.utc)

class TestAudit(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Mock config for DownloadManager
        self.mock_config = {
            "api_id": 1,
            "api_hash": "hash",
            "media_types": ["photo", "video"],
            "file_formats": {"photo": ["all"], "video": ["all"]},
            "download_settings": {
                "base_directory": "/tmp/tmd_test",
                "skip_duplicates": True,
                "validate_downloads": False,
                "resumable_downloads": False
            },
            "chats": []
        }

    def tearDown(self):
        self.loop.close()

    @mock.patch("core.downloader.tqdm")
    def test_photo_size_calculation(self, mock_tqdm):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = os.path.join(tmpdir, "config.yaml")
            cfg = {
                "api_id": 1,
                "api_hash": "x",
                "media_types": ["photo"],
                "file_formats": {"photo": ["all"]},
                "download_settings": {
                    "base_directory": tmpdir,
                    "skip_duplicates": False,
                    "history_format": "jsonl",
                    "resumable_downloads": True
                },
                "chats": []
            }
            with open(cfg_path, "w") as f:
                yaml.dump(cfg, f)

            cfg_mgr = ConfigManager(config_path=cfg_path)
            dm = DownloadManager(config_manager=cfg_mgr)

            # Mock media_obj (Photo) with sizes: 100, 500, 250
            # file_size should be 500
            photo = MockPhoto([MockPhotoSize(100), MockPhotoSize(500), MockPhotoSize(250)])
            msg = MockMessage(photo=photo)

            # We need to mock _get_media_meta to return a path
            dm._get_media_meta = mock.AsyncMock(return_value=(os.path.join(tmpdir, "test.jpg"), "jpg"))
            dm._can_download = mock.Mock(return_value=True)
            dm._check_existing_file = mock.Mock(return_value=None)
            dm._is_exist = mock.Mock(return_value=False)

            # Mock client and iter_download
            client = mock.AsyncMock()

            async def mock_iter_download(*args, **kwargs):
                yield b"chunk"

            client.iter_download = mock.Mock(side_effect=mock_iter_download)

            # Capture progress callback values
            dm._progress_callback = mock.Mock()

            self.loop.run_until_complete(dm.download_media(client, msg, ["photo"], {"photo": ["all"]}))

            # Verify tqdm was initialized with total=500
            self.assertTrue(mock_tqdm.called)
            # The last call to tqdm should have total=500
            # tqdm is called in a context manager: with tqdm(...) as pbar
            args, kwargs = mock_tqdm.call_args
            self.assertEqual(kwargs.get("total"), 500, f"Expected total=500, got {kwargs.get('total')}")

            # Verify iter_download was called
            self.assertTrue(client.iter_download.called, "client.iter_download was not called")

    def test_web_app_endpoints(self):
        client = TestClient(app)

        # Test status endpoint
        response = client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("overall", data)
        self.assertIn("status", data["overall"])

        # Test stats endpoint
        response = client.get("/api/stats")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # If ClickHouse is disabled, it returns {"enabled": False, ...}
        if data.get("enabled") is False:
            self.assertIn("error", data)
        else:
            self.assertIn("total_downloaded", data)

if __name__ == "__main__":
    unittest.main()
