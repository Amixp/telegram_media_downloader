import os
import tempfile
from unittest import mock


def test_export_chat_copies_or_links_files():
    from export_chat import export_chat

    with tempfile.TemporaryDirectory() as tmpdir:
        base = os.path.join(tmpdir, "base")
        history = os.path.join(base, "history")
        os.makedirs(history, exist_ok=True)

        media_dir = os.path.join(base, "photo")
        os.makedirs(media_dir, exist_ok=True)
        media_path = os.path.join(media_dir, "img.jpg")
        with open(media_path, "wb") as f:
            f.write(b"123")

        chat_id = -123
        path_id = abs(chat_id)
        jsonl_path = os.path.join(history, f"chat_{path_id}.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write(
                '{"id": 1, "date": "2020-01-01T00:00:00+00:00", "text": "x", "chat_id": -123, "chat_title": "T", "downloaded_file": "'
                + media_path.replace("\\", "\\\\")
                + '"}\n'
            )

        out = os.path.join(tmpdir, "out")
        result, export_path = export_chat(base_directory=base, chat_id=chat_id, out_directory=out, link_mode="copy")

        assert result.exported == 1
        assert os.path.exists(os.path.join(export_path, f"chat_{path_id}.jsonl"))
        exported_files_dir = os.path.join(export_path, "media")
        assert os.path.isdir(exported_files_dir)
        exported_files = os.listdir(exported_files_dir)
        assert len(exported_files) == 1


def test_export_chat_from_clickhouse_when_primary_source():
    """При clickhouse_config enabled + primary_source сообщения берутся из CH."""
    from export_chat import export_chat

    with tempfile.TemporaryDirectory() as tmpdir:
        base = os.path.join(tmpdir, "base")
        history = os.path.join(base, "history")
        os.makedirs(history, exist_ok=True)
        media_path = os.path.join(tmpdir, "photo.jpg")
        with open(media_path, "wb") as f:
            f.write(b"x")

        chat_id = -456
        out = os.path.join(tmpdir, "out")
        mock_messages = [
            {
                "id": 1,
                "chat_id": chat_id,
                "date": "2025-01-01T12:00:00",
                "text": "Hi",
                "downloaded_file": media_path,
                "has_media": False,
                "media_type": "text",
                "file_size": 0,
                "sender_id": 0,
                "chat_title": "Chat",
            },
        ]

        with mock.patch("utils.clickhouse_db.ClickHouseMetadataDB") as MockDB:
            mock_db = mock.Mock()
            mock_db.enabled = True
            mock_db.get_messages_for_chat.return_value = mock_messages
            MockDB.return_value = mock_db

            result, export_path = export_chat(
                base_directory=base,
                chat_id=chat_id,
                out_directory=out,
                link_mode="copy",
                clickhouse_config={"enabled": True, "primary_source": True},
            )

        assert result.exported == 1
        assert result.missing == 0
        assert os.path.isdir(os.path.join(export_path, "media"))
        assert len(os.listdir(os.path.join(export_path, "media"))) == 1
        MockDB.assert_called_once()
        mock_db.get_messages_for_chat.assert_called_once_with(chat_id)


def test_iter_messages_fallback_to_jsonl_when_no_clickhouse():
    """При пустом clickhouse_db сообщения читаются из JSONL."""
    from export_chat import _iter_messages

    with tempfile.TemporaryDirectory() as tmpdir:
        jsonl_path = os.path.join(tmpdir, "chat_1.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write('{"id": 1, "text": "a", "chat_id": 1}\n')
            f.write('{"id": 2, "text": "b", "chat_id": 1}\n')

        # clickhouse_db=None — источник JSONL
        out = list(_iter_messages(jsonl_path=jsonl_path, clickhouse_db=None, chat_id=1))
        assert len(out) == 2
        assert out[0]["id"] == 1 and out[0]["text"] == "a"
        assert out[1]["id"] == 2 and out[1]["text"] == "b"


def test_iter_messages_fallback_to_jsonl_when_clickhouse_disabled():
    """При clickhouse_db.enabled=False используется JSONL."""
    from export_chat import _iter_messages

    with tempfile.TemporaryDirectory() as tmpdir:
        jsonl_path = os.path.join(tmpdir, "chat_2.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write('{"id": 10, "text": "from file", "chat_id": 2}\n')

        mock_db = mock.Mock()
        mock_db.enabled = False

        out = list(_iter_messages(jsonl_path=jsonl_path, clickhouse_db=mock_db, chat_id=2))
        assert len(out) == 1
        assert out[0]["id"] == 10 and out[0]["text"] == "from file"
        mock_db.get_messages_for_chat.assert_not_called()

