"""Тесты для utils.path_rewrite (to_display_path)."""
import os
import unittest

from utils.path_rewrite import to_display_path


class TestToDisplayPath(unittest.TestCase):
    def test_empty_media_links_base_returns_unchanged(self):
        self.assertEqual(
            to_display_path("/base/photo/x.jpg", "/base", ""),
            "/base/photo/x.jpg",
        )
        self.assertEqual(
            to_display_path("/base/photo/x.jpg", "/base", "   "),
            "/base/photo/x.jpg",
        )

    def test_path_under_base_replaced(self):
        base = os.path.normpath("/media/disk/Telegram")
        new_base = os.path.normpath("/mnt/links/Telegram")
        path = os.path.normpath("/media/disk/Telegram/photo/1.jpg")
        out = to_display_path(path, base, new_base)
        self.assertEqual(out, os.path.join(new_base, "photo", "1.jpg"))

    def test_relative_path_resolved_against_base(self):
        base = os.path.normpath("/base")
        new_base = os.path.normpath("/new")
        path = "photo/x.jpg"
        out = to_display_path(path, base, new_base)
        self.assertEqual(out, os.path.join(new_base, "photo", "x.jpg"))

    def test_path_outside_base_unchanged(self):
        base = os.path.normpath("/base")
        new_base = os.path.normpath("/new")
        path = os.path.normpath("/other/photo/x.jpg")
        self.assertEqual(to_display_path(path, base, new_base), path)

    def test_empty_base_uses_media_links_base(self):
        # при пустом base to_display_path не подменяет (commonpath не совпадает с base)
        base = ""
        new_base = os.path.normpath("/new")
        path = os.path.normpath("/any/path/file.jpg")
        # Пустой base — commonpath не совпадёт с base_abs, вернётся real_path
        result = to_display_path(path, base, new_base)
        self.assertEqual(result, path)

    def test_empty_real_path_returned_unchanged(self):
        self.assertEqual(to_display_path("", "/base", "/new"), "")
        self.assertEqual(to_display_path("   ", "/base", "/new"), "   ")
