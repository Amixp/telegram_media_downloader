import unittest
from unittest import mock
from datetime import datetime
from utils.clickhouse_db import ClickHouseMetadataDB

class TestClickHouseDB(unittest.TestCase):
    def setUp(self):
        self.config = {
            "enabled": True,
            "host": "localhost",
            "port": 9000,
            "user": "default",
            "password": "",
            "database": "test_db",
            "batch_size": 2
        }

    @mock.patch("utils.clickhouse_db.Client")
    def test_init_and_schema(self, mock_client_class):
        mock_client = mock_client_class.return_value
        db = ClickHouseMetadataDB(self.config)

        # Trigger client creation
        client = db._get_client()

        # Verify schema creation
        mock_client.execute.assert_any_call(mock.ANY) # Should check for CREATE TABLE
        self.assertEqual(mock_client_class.call_count, 2) # Root client + DB client

    @mock.patch("utils.clickhouse_db.Client")
    @mock.patch("utils.clickhouse_db.asyncio.get_event_loop")
    def test_batch_insert(self, mock_get_loop, mock_client_class):
        mock_client = mock_client_class.return_value
        db = ClickHouseMetadataDB(self.config)

        # Mock executor to run synchronously for testing
        mock_loop = mock.Mock()
        mock_get_loop.return_value = mock_loop

        async def run_in_executor(executor, func, *args):
            func(*args)
        mock_loop.run_in_executor = run_in_executor

        import asyncio
        loop = asyncio.new_event_loop()

        messages = [
            {"chat_id": 1, "message_id": 10, "date": datetime.now(), "text": "Hello", "media_type": "text"},
            {"chat_id": 1, "message_id": 11, "date": datetime.now(), "text": "World", "media_type": "text"}
        ]

        for msg in messages:
            loop.run_until_complete(db.save_message(msg))

        # Verify flush was called because batch_size=2
        mock_client.execute.assert_any_call(mock.ANY, mock.ANY)
        # Check if INSERT was called
        call_args = mock_client.execute.call_args_list
        insert_calls = [c for c in call_args if "INSERT INTO messages" in c[0][0]]
        self.assertTrue(len(insert_calls) > 0)

    @mock.patch("utils.clickhouse_db.Client")
    @mock.patch("utils.clickhouse_db.asyncio.get_event_loop")
    def test_insert_with_none_strings(self, mock_get_loop, mock_client_class):
        """Сообщения с text/media_type=None не должны вызывать 'NoneType' has no attribute 'encode'."""
        mock_client = mock_client_class.return_value
        db = ClickHouseMetadataDB(self.config)

        mock_loop = mock.Mock()
        mock_get_loop.return_value = mock_loop

        async def run_in_executor(executor, func, *args):
            func(*args)

        mock_loop.run_in_executor = run_in_executor

        import asyncio
        loop = asyncio.new_event_loop()

        messages = [
            {
                "chat_id": 1,
                "message_id": 1,
                "date": datetime.now(),
                "text": None,
                "media_type": None,
                "file_path": None,
                "chat_title": None,
            }
        ]
        for msg in messages:
            loop.run_until_complete(db.save_message(msg))
        loop.run_until_complete(db.flush())

        insert_calls = [c for c in mock_client.execute.call_args_list if "INSERT INTO messages" in c[0][0]]
        self.assertTrue(len(insert_calls) > 0)
        row = insert_calls[0][0][1][0]
        self.assertEqual(row[3], "")  # text
        self.assertEqual(row[4], "")  # media_type
        self.assertEqual(row[5], "")  # file_path
        self.assertEqual(row[10], "")  # chat_title

    @mock.patch("utils.clickhouse_db.Client")
    @mock.patch("utils.clickhouse_db.asyncio.get_event_loop")
    def test_update_chat_info(self, mock_get_loop, mock_client_class):
        mock_client = mock_client_class.return_value
        db = ClickHouseMetadataDB(self.config)

        mock_loop = mock.Mock()
        mock_get_loop.return_value = mock_loop
        async def run_in_executor(executor, func, *args):
            func(*args)
        mock_loop.run_in_executor = run_in_executor

        import asyncio
        loop = asyncio.new_event_loop()

        loop.run_until_complete(db.update_chat_info(123, "Test Chat", 100, 1024))

        call_args = mock_client.execute.call_args_list
        chat_calls = [c for c in call_args if "INSERT INTO chats" in c[0][0]]
        self.assertTrue(len(chat_calls) > 0)
        self.assertEqual(chat_calls[0][0][1][0][0], 123)
        self.assertEqual(chat_calls[0][0][1][0][1], "Test Chat")

if __name__ == "__main__":
    unittest.main()
