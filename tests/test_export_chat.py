import os
import tempfile


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
        jsonl_path = os.path.join(history, f"chat_{chat_id}.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write(
                '{"id": 1, "date": "2020-01-01T00:00:00+00:00", "text": "x", "chat_id": -123, "chat_title": "T", "downloaded_file": "'
                + media_path.replace("\\", "\\\\")
                + '"}\n'
            )

        out = os.path.join(tmpdir, "out")
        result, export_path = export_chat(base_directory=base, chat_id=chat_id, out_directory=out, link_mode="copy")

        assert result.exported == 1
        assert os.path.exists(os.path.join(export_path, f"chat_{chat_id}.jsonl"))
        exported_files_dir = os.path.join(export_path, "media")
        assert os.path.isdir(exported_files_dir)
        exported_files = os.listdir(exported_files_dir)
        assert len(exported_files) == 1

