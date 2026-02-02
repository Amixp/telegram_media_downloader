import unittest
import os
import shutil
import zipfile
import asyncio
from utils.archive_handler import ArchiveHandler

class TestArchiveHandler(unittest.TestCase):
    def setUp(self):
        self.test_dir = "test_archives"
        os.makedirs(self.test_dir, exist_ok=True)
        self.settings = {
            "extract_archives": True,
            "delete_after_extraction": False,
            "extraction_directory": "",
            "supported_extensions": ["zip"]
        }
        self.handler = ArchiveHandler(self.settings)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        self.loop.close()

    def test_extract_zip(self):
        # Create a test zip file
        zip_path = os.path.join(self.test_dir, "test.zip")
        content_path = os.path.join(self.test_dir, "content.txt")
        with open(content_path, "w") as f:
            f.write("test content")

        with zipfile.ZipFile(zip_path, 'w') as zipf:
            zipf.write(content_path, arcname="content.txt")

        # Extract (используем loop.run_until_complete для async метода)
        result = self.loop.run_until_complete(self.handler.extract_if_archive(zip_path))

        self.assertTrue(result)
        extract_path = os.path.join(self.test_dir, "test")
        self.assertTrue(os.path.exists(extract_path))
        self.assertTrue(os.path.exists(os.path.join(extract_path, "content.txt")))

    def test_delete_after(self):
        self.handler.delete_after = True
        zip_path = os.path.join(self.test_dir, "test_del.zip")
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            zipf.writestr("file.txt", "data")

        self.loop.run_until_complete(self.handler.extract_if_archive(zip_path))
        self.assertFalse(os.path.exists(zip_path))

    def test_unsupported_format(self):
        txt_path = os.path.join(self.test_dir, "test.txt")
        with open(txt_path, "w") as f:
            f.write("not an archive")

        result = self.loop.run_until_complete(self.handler.extract_if_archive(txt_path))
        self.assertFalse(result)

if __name__ == "__main__":
    unittest.main()
