"""Unittest module for config manager."""

import os
import tempfile
import unittest

import yaml


class ConfigManagerTestCase(unittest.TestCase):
    def test_set_selected_chats_creates_and_disables_others(self):
        # Локальный импорт, чтобы тесты работали без установки пакета
        from utils.config import ConfigManager

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = os.path.join(tmpdir, "config.yaml")
            initial = {
                "api_id": 1,
                "api_hash": "x",
                "chats": [
                    {
                        "chat_id": 100,
                        "title": "Old",
                        "last_read_message_id": 123,
                        "ids_to_retry": [1, 2],
                        "enabled": True,
                    },
                    {
                        "chat_id": 200,
                        "title": "Other",
                        "last_read_message_id": 0,
                        "ids_to_retry": [],
                        "enabled": True,
                    },
                ],
            }
            with open(cfg_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(initial, f, allow_unicode=True)

            mgr = ConfigManager(config_path=cfg_path)
            mgr.load()
            mgr.set_selected_chats([(100, "Old updated"), (300, "New chat")])
            mgr.save()

            with open(cfg_path, "r", encoding="utf-8") as f:
                saved = yaml.safe_load(f) or {}

        chats = {c["chat_id"]: c for c in saved["chats"]}
        self.assertTrue(chats[100]["enabled"])
        self.assertEqual(chats[100]["title"], "Old updated")
        # Не трогаем прогресс/ретраи
        self.assertEqual(chats[100]["last_read_message_id"], 123)
        self.assertEqual(chats[100]["ids_to_retry"], [1, 2])

        self.assertFalse(chats[200]["enabled"])
        self.assertTrue(chats[300]["enabled"])
        self.assertEqual(chats[300]["last_read_message_id"], 0)
        self.assertEqual(chats[300]["ids_to_retry"], [])
        # Порядок очереди сохраняется
        self.assertEqual(chats[100]["order"], 0)
        self.assertEqual(chats[300]["order"], 1)

    def test_filter_chat_items_search_and_filter(self):
        from utils.chat_selector import ChatListItem, ChatSelector

        items = [
            ChatListItem(chat_id=1, title="Dev chat", chat_type="group", last_message_preview="hello world"),
            ChatListItem(chat_id=2, title="Breaking news", chat_type="channel", last_message_preview="whatever"),
            ChatListItem(chat_id=3, title="Alice", chat_type="user", last_message_preview="ping"),
        ]

        filtered = ChatSelector.filter_chat_items(items, filter_mode="groups_channels", search_query="")
        self.assertEqual([i.chat_id for i in filtered], [1, 2])

        filtered = ChatSelector.filter_chat_items(items, filter_mode="users", search_query="")
        self.assertEqual([i.chat_id for i in filtered], [3])

        filtered = ChatSelector.filter_chat_items(items, filter_mode="all", search_query="break")
        self.assertEqual([i.chat_id for i in filtered], [2])

        filtered = ChatSelector.filter_chat_items(items, filter_mode="all", search_query="1")
        self.assertEqual([i.chat_id for i in filtered], [1])

