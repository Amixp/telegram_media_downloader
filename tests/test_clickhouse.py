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

    @mock.patch("utils.clickhouse_db.Client")
    def test_get_existing_message_ids(self, mock_client_class):
        """get_existing_message_ids возвращает множество message_id из БД."""
        mock_client = mock_client_class.return_value
        mock_client.execute.return_value = [(10,), (11,)]
        db = ClickHouseMetadataDB(self.config)
        result = db.get_existing_message_ids(chat_id=1, message_ids=[10, 11, 12])
        self.assertEqual(result, {10, 11})
        select_calls = [c for c in mock_client.execute.call_args_list if "SELECT message_id" in c[0][0]]
        self.assertEqual(len(select_calls), 1)
        self.assertIn("chat_id", select_calls[0][0][0])

    @mock.patch("utils.clickhouse_db.Client")
    def test_get_existing_message_ids_disabled(self, mock_client_class):
        """При enabled=False get_existing_message_ids возвращает пустое множество."""
        db = ClickHouseMetadataDB({"enabled": False})
        result = db.get_existing_message_ids(chat_id=1, message_ids=[10])
        self.assertEqual(result, set())
        mock_client_class.assert_not_called()

    @mock.patch("utils.clickhouse_db.Client")
    def test_get_messages_for_chat(self, mock_client_class):
        """get_messages_for_chat возвращает список dict в формате JSONL."""
        from datetime import datetime
        dt = datetime(2025, 1, 15, 12, 0, 0)
        mock_client = mock_client_class.return_value
        mock_client.execute.return_value = [
            (1, 10, dt, "Hi", "text", "", 0, 0, "Chat"),
            (1, 11, dt, "Bye", "photo", "/path/photo.jpg", 1024, 999, "Chat"),
        ]
        db = ClickHouseMetadataDB(self.config)
        result = db.get_messages_for_chat(chat_id=1)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], 10)
        self.assertEqual(result[0]["text"], "Hi")
        self.assertEqual(result[0]["downloaded_file"], None)
        self.assertEqual(result[1]["id"], 11)
        self.assertEqual(result[1]["downloaded_file"], "/path/photo.jpg")
        self.assertEqual(result[1]["has_media"], True)

    @mock.patch("utils.clickhouse_db.Client")
    def test_get_chats_manifest(self, mock_client_class):
        """get_chats_manifest возвращает список (chat_id, title, count, last_date)."""
        from datetime import datetime
        dt = datetime(2025, 1, 15)
        mock_client = mock_client_class.return_value
        mock_client.execute.return_value = [(1, "Chat One", 100, dt), (-2, "Chat Two", 50, dt)]
        db = ClickHouseMetadataDB(self.config)
        result = db.get_chats_manifest()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], (1, "Chat One", 100, dt))
        self.assertEqual(result[1][0], -2)
        self.assertEqual(result[1][2], 50)

    @mock.patch("utils.clickhouse_db.Client")
    def test_get_chat_stats(self, mock_client_class):
        """get_chat_stats возвращает (title, message_count, last_message_date) или None."""
        from datetime import datetime
        dt = datetime(2025, 1, 15)
        mock_client = mock_client_class.return_value
        mock_client.execute.return_value = [("My Chat", 42, dt)]
        db = ClickHouseMetadataDB(self.config)
        result = db.get_chat_stats(chat_id=1)
        self.assertIsNotNone(result)
        title, count, last_date = result
        self.assertEqual(title, "My Chat")
        self.assertEqual(count, 42)
        self.assertEqual(last_date, dt)

    @mock.patch("utils.clickhouse_db.Client")
    def test_get_chat_stats_empty(self, mock_client_class):
        """get_chat_stats возвращает None при отсутствии сообщений."""
        mock_client = mock_client_class.return_value
        mock_client.execute.return_value = []
        db = ClickHouseMetadataDB(self.config)
        result = db.get_chat_stats(chat_id=999)
        self.assertIsNone(result)

    @mock.patch("utils.clickhouse_db.Client")
    def test_get_messages_page(self, mock_client_class):
        """get_messages_page возвращает (messages, total) с пагинацией."""
        from datetime import datetime
        dt = datetime(2025, 1, 15, 12, 0, 0)
        mock_client = mock_client_class.return_value

        def execute_side_effect(query, params=None):
            if "count()" in query:
                return [(250,)]
            return [
                (1, 10, dt, "Hi", "text", "", 0, 0, "Chat"),
                (1, 11, dt, "Bye", "photo", "/path/photo.jpg", 1024, 999, "Chat"),
            ]

        mock_client.execute.side_effect = execute_side_effect
        db = ClickHouseMetadataDB(self.config)
        messages, total = db.get_messages_page(chat_id=1, offset=0, limit=100)
        self.assertEqual(total, 250)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["id"], 10)
        self.assertEqual(messages[1]["has_media"], True)


if __name__ == "__main__":
    unittest.main()
