"""Phase 0 smoke tests — run with: py -3 -m unittest discover tools/tests"""
from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import main as app_main
from llama_router.core import storage, utils
from llama_router.core.events import EventBus
from llama_router.schemas import (AppConfig, ModelEntry, ModelState, Profile,
                                  ServerSettings)
from llama_router import preset
from llama_router.ui.pages.settings import SettingsPage


class TestDebugFlag(unittest.TestCase):
    def test_debug_flag_is_explicit(self):
        self.assertTrue(app_main._debug_requested(["--debug"]))
        self.assertTrue(app_main._debug_requested(["--other", "--debug"]))
        self.assertFalse(app_main._debug_requested([]))


class TestStorage(unittest.TestCase):
    def test_kv_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            storage.init_db(db)
            storage.db_write(db, "k", {"a": [1, 2], "b": "ñ"})
            self.assertEqual(storage.db_read(db, "k"), {"a": [1, 2], "b": "ñ"})
            self.assertEqual(storage.db_read(db, "missing", default=7), 7)

    def test_write_text_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "x.ini"
            storage.write_text(p, "hola")
            self.assertEqual(p.read_text(encoding="utf-8"), "hola")


class TestSchemas(unittest.TestCase):
    def test_model_entry_roundtrip(self):
        m = ModelEntry(id="model_1", name="q", path="/x.gguf", state=ModelState.MISSING)
        d = m.to_dict()
        self.assertEqual(d["state"], "missing")
        m2 = ModelEntry.from_dict(d)
        self.assertEqual(m2, m)

    def test_from_dict_tolerates_unknown_keys(self):
        m = ModelEntry.from_dict({"id": "a", "name": "n", "path": "p", "junk": 1})
        self.assertEqual(m.id, "a")

    def test_appconfig_nested_server(self):
        cfg = AppConfig.from_dict({"server": {"port": 9999}, "theme": "light"})
        self.assertEqual(cfg.server.port, 9999)
        self.assertEqual(cfg.theme, "light")
        self.assertEqual(cfg.to_dict()["server"]["port"], 9999)

    def test_lan_migration(self):
        s = ServerSettings.from_dict({"host": "0.0.0.0"})
        self.assertEqual(s.expose, "lan")
        self.assertEqual(s.effective_host(), "0.0.0.0")
        s2 = ServerSettings.from_dict({"host": "0.0.0.0", "expose": "local"})
        self.assertEqual(s2.effective_host(), "127.0.0.1")

    def test_api_key_is_not_persisted_in_database(self):
        with tempfile.TemporaryDirectory() as td:
            from llama_router.core.paths import PathManager
            from llama_router.services.config_manager import ConfigManager
            paths = PathManager(Path(td))
            paths.ensure_dirs()
            storage.init_db(paths.db_path)
            cfg = ConfigManager(paths, EventBus())
            cfg.load()
            cfg.update({"server": {"api_key": "lr_secret"}})
            raw = storage.db_read(paths.db_path, "config")
            self.assertEqual(raw["server"]["api_key"], "")
            loaded = ConfigManager(paths, EventBus())
            loaded.load()
            self.assertEqual(loaded.get().server.api_key, "lr_secret")


class TestSettingsHelpers(unittest.TestCase):
    def test_partial_api_key_uses_one_bullet_per_masked_character(self):
        self.assertEqual(SettingsPage._partial_key("short"), "•••••")
        self.assertEqual(SettingsPage._partial_key("abcdefghijklmnop"),
                         "abcdef••••••••mnop")


class TestPreset(unittest.TestCase):
    def test_duplicate_active_aliases_are_rejected(self):
        models = [ModelEntry(id="m1", name="One", path="one.gguf"),
                  ModelEntry(id="m2", name="Two", path="two.gguf")]
        profiles = {
            "m1": [Profile(id="p1", name="Default", model_id="m1",
                           active=True, route_alias="same")],
            "m2": [Profile(id="p2", name="Default", model_id="m2",
                           active=True, route_alias="same")],
        }
        with self.assertRaisesRegex(ValueError, "Duplicate active route alias"):
            preset.generate(models, profiles, {})

    def _state(self):
        models = [
            ModelEntry(id="m1", name="Qwen3-8B", path=r"E:\models\qwen3.gguf"),
            ModelEntry(id="m2", name="Disabled", path=r"E:\models\off.gguf", enabled=False),
        ]
        profiles = {
            "m1": [
                Profile(id="p1", name="Default", model_id="m1", active=True,
                        route_alias="qwen3",
                        params={"ctx-size": 32768, "n-gpu-layers": -1,
                                "mlock": True, "spec-type": "none",
                                "fit-target": "2048"}),
                Profile(id="p2", name="Agent", model_id="m1", active=True,
                        route_alias="qwen3",
                        params={"ctx-size": 131072, "jinja": "true"}),
            ],
        }
        return models, profiles, {"flash-attn": "on", "port": 1234}

    def test_generate(self):
        models, profiles, gparams = self._state()
        text = preset.generate(models, profiles, gparams)
        self.assertIn("[*]", text)
        self.assertIn("flash-attn = on", text)
        self.assertNotIn("port", text)                    # blocked key
        self.assertIn("[qwen3_Default]", text)            # multi-profile naming
        self.assertIn("[qwen3_Agent]", text)
        self.assertIn("load-mode = mlock", text)          # legacy mlock migration
        self.assertIn("fit-target = 2048", text)
        self.assertNotIn("spec-type", text)               # "none" → omitted
        self.assertNotIn("off.gguf", text)                # disabled model

    def test_load_settings_map_to_supported_load_modes(self):
        model = ModelEntry(id="m", name="Model", path="model.gguf")
        cases = (
            ({"mlock": True}, "mlock"),
            ({"mlock": True, "no-mmap": True}, "mlock"),
            ({"no-mmap": True}, "none"),
            ({"load-mode": "mmap+mlock"}, "mlock"),
        )
        for params, expected in cases:
            with self.subTest(params=params):
                profile = Profile(id="p", name="Default", model_id="m",
                                  active=True, params=params)
                text = preset.generate([model], {"m": [profile]}, {})
                self.assertIn(f"load-mode = {expected}", text)
                self.assertNotIn("mmap+mlock", text)

    def test_strip_disabled_sections(self):
        models, _, _ = self._state()
        text = "[keep]\nmodel = E:/models/qwen3.gguf\n\n[drop]\nmodel = E:\\models\\off.gguf\nctx-size = 1\n"
        out = preset.strip_disabled_sections(text, models)
        self.assertIn("[keep]", out)
        self.assertNotIn("[drop]", out)

    def test_parse_profile_params_reflects_deleted_option(self):
        models, profiles, _ = self._state()
        text = preset.generate(models, profiles, {})
        text = text.replace("load-mode = mlock\n", "")
        globals_out, updates = preset.parse_profile_params(
            text, models, profiles)
        self.assertEqual(globals_out, {})
        self.assertNotIn("load-mode", updates["p1"])
        self.assertNotIn("mlock", updates["p1"])
        self.assertEqual(updates["p1"]["ctx-size"], "32768")


class TestEventBus(unittest.TestCase):
    def test_publish_from_thread_drain_in_main(self):
        import threading
        bus = EventBus()
        got = []
        bus.subscribe("x", got.append)
        t = threading.Thread(target=lambda: [bus.publish("x", i) for i in range(5)])
        t.start(); t.join()
        n = bus.drain()
        self.assertEqual(n, 5)
        self.assertEqual(got, [0, 1, 2, 3, 4])

    def test_handler_error_does_not_break_drain(self):
        bus = EventBus()
        got = []
        bus.subscribe("x", lambda d: 1 / 0)
        bus.subscribe("x", got.append)
        bus.publish("x", "ok")
        bus.drain()
        self.assertEqual(got, ["ok"])


class TestConfigManager(unittest.TestCase):
    def _mk(self, td):
        from llama_router.core.events import EventBus
        from llama_router.core.paths import PathManager
        from llama_router.services.config_manager import ConfigManager
        paths = PathManager(Path(td))
        storage.init_db(paths.db_path)
        return ConfigManager(paths, EventBus())

    def test_first_run_persists_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            cm = self._mk(td)
            cm.load()
            self.assertEqual(cm.get().server.port, 8080)
            raw = storage.db_read(Path(td) / "config" / "llama_router.db", "config")
            self.assertEqual(raw["server"]["port"], 8080)

    def test_update_deep_merges_server(self):
        with tempfile.TemporaryDirectory() as td:
            cm = self._mk(td)
            cm.load()
            cm.update({"server": {"port": 9001}, "language": "es"})
            cfg = cm.get()
            self.assertEqual(cfg.server.port, 9001)
            self.assertEqual(cfg.server.max_models, 1)   # untouched default
            self.assertEqual(cfg.language, "es")

    def test_unchanged_update_does_not_publish_or_write(self):
        with tempfile.TemporaryDirectory() as td:
            cm = self._mk(td)
            cm.load()
            cm._events.drain()
            with mock.patch(
                    "llama_router.services.config_manager.db_write") as write:
                cm.update({"theme": cm.get().theme})
            write.assert_not_called()
            self.assertEqual(cm._events.drain(), 0)

    def test_reset_server_keeps_app_settings(self):
        with tempfile.TemporaryDirectory() as td:
            cm = self._mk(td)
            cm.load()
            cm.update({"language": "es", "server": {"port": 9001}})
            cm.reset_server()
            self.assertEqual(cm.get().server.port, 8080)   # back to default
            self.assertEqual(cm.get().language, "es")      # preserved

    def test_corrupt_config_resets(self):
        with tempfile.TemporaryDirectory() as td:
            cm = self._mk(td)
            db = Path(td) / "config" / "llama_router.db"
            storage.init_db(db)
            storage.db_write(db, "config", {"server": "not a dict"})
            cm.load()
            self.assertEqual(cm.get().server.port, 8080)


class TestI18n(unittest.TestCase):
    def test_translate_and_fallback(self):
        from llama_router import i18n
        i18n.set_language("es")
        try:
            self.assertEqual(i18n.t("Models"), "Modelos")
            self.assertEqual(i18n.t("server"), "servidor")
            self.assertEqual(i18n.t("model"), "modelo")
            self.assertEqual(i18n.t("profile"), "perfil")
            self.assertEqual(i18n.t("not in catalog"), "not in catalog")
            self.assertEqual(i18n.t("{total} models · {new} new", total=2, new=1),
                             "2 modelos · 1 nuevos")
        finally:
            i18n.set_language("en")
        self.assertEqual(i18n.t("Models"), "Models")

    def test_unknown_language_falls_back_to_english(self):
        from llama_router import i18n
        i18n.set_language("de")
        try:
            self.assertEqual(i18n.t("Models"), "Models")
        finally:
            i18n.set_language("en")


class TestUtils(unittest.TestCase):
    def test_strip_ansi(self):
        self.assertEqual(utils.strip_ansi("\x1b[32mok\x1b[0m"), "ok")

    def test_cuda_ver(self):
        self.assertEqual(utils.cuda_major_ver("llama-b1-bin-win-cuda-12.4-x64.zip"), "12")
        self.assertIsNone(utils.cuda_major_ver("llama-b1-bin-win-vulkan-x64.zip"))

    def test_sanitise(self):
        self.assertEqual(utils.sanitise("My Model (v2)!"), "My_Model__v2")


if __name__ == "__main__":
    unittest.main()
