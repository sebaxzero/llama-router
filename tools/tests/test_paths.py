"""resolve_base pointer-file behaviour and the first-run gate."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llama_router.core.paths import (needs_first_run_choice, resolve_base,
                                     write_base_pointer, asset_path,
                                     _POINTER_NAME)
from llama_router.core.storage import InstanceGuard


class TestResolveBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.anchor = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_no_pointer_returns_anchor(self):
        self.assertEqual(resolve_base(self.anchor), self.anchor)

    def test_second_instance_guard_is_rejected(self):
        path = self.anchor / "instance.lock"
        first, second = InstanceGuard(path), InstanceGuard(path)
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
        finally:
            first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_pointer_to_existing_dir_wins(self):
        target = self.anchor / "data"
        target.mkdir()
        write_base_pointer(target, self.anchor)
        self.assertEqual(resolve_base(self.anchor), target.resolve())

    def test_pointer_to_missing_dir_falls_back(self):
        (self.anchor / _POINTER_NAME).write_text(
            str(self.anchor / "gone"), encoding="utf-8")
        self.assertEqual(resolve_base(self.anchor), self.anchor)

    def test_empty_pointer_falls_back(self):
        (self.anchor / _POINTER_NAME).write_text("  \n", encoding="utf-8")
        self.assertEqual(resolve_base(self.anchor), self.anchor)

    def test_asset_path_finds_packaged_icon_in_script_mode(self):
        self.assertTrue(asset_path("app_icon.png").is_file())

    def test_asset_path_uses_pyinstaller_bundle(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", str(self.anchor),
                                  create=True):
            self.assertEqual(
                asset_path("app_icon.ico"),
                self.anchor / "llama_router" / "assets" / "app_icon.ico")


class TestFirstRunGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.anchor = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_script_mode_never_asks(self):
        self.assertFalse(needs_first_run_choice(self.anchor))

    def test_frozen_fresh_install_asks(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertTrue(needs_first_run_choice(self.anchor))

    def test_frozen_with_pointer_does_not_ask(self):
        write_base_pointer(self.anchor, self.anchor)
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertFalse(needs_first_run_choice(self.anchor))

    def test_frozen_with_existing_config_does_not_ask(self):
        (self.anchor / "config").mkdir()
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertFalse(needs_first_run_choice(self.anchor))


if __name__ == "__main__":
    unittest.main()
