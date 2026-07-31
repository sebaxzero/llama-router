"""Phase 2 tests — models registry and profiles."""
from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from llama_router.core import storage
from llama_router.core.events import EventBus
from llama_router.core.paths import PathManager
from llama_router.schemas import ModelEntry
from llama_router.services.config_manager import ConfigManager
from llama_router.services.models_manager import ModelsManager, _gguf_kind
from llama_router.services.profile_manager import ProfileManager


def _gguf_bytes(architecture: str = "llama", gtype: str | None = None) -> bytes:
    """Craft a minimal valid GGUF v3 header with the given metadata."""
    def s(text: str) -> bytes:
        b = text.encode()
        return struct.pack("<Q", len(b)) + b

    kv = []
    kv.append(s("general.architecture") + struct.pack("<I", 8) + s(architecture))
    if gtype is not None:
        kv.append(s("general.type") + struct.pack("<I", 8) + s(gtype))
    return (b"GGUF" + struct.pack("<I", 3)
            + struct.pack("<QQ", 0, len(kv)) + b"".join(kv))


class _Env:
    def __init__(self, td: str) -> None:
        self.paths = PathManager(Path(td))
        self.paths.ensure_dirs()
        storage.init_db(self.paths.db_path)
        self.events = EventBus()
        self.config = ConfigManager(self.paths, self.events)
        self.config.load()
        self.models = ModelsManager(self.paths, self.config, self.events)
        self.models.load()
        self.profiles = ProfileManager(self.paths, self.models, self.events)
        self.profiles.load()


class TestGgufKind(unittest.TestCase):
    def test_kinds(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "main.gguf").write_bytes(_gguf_bytes("llama"))
            (base / "proj-mmproj.gguf").write_bytes(_gguf_bytes("clip"))
            (base / "typed.gguf").write_bytes(_gguf_bytes("clip", gtype="mmproj"))
            (base / "spec.gguf").write_bytes(_gguf_bytes("llama-assistant"))
            (base / "x-draft-y.gguf").write_bytes(_gguf_bytes("llama"))
            self.assertEqual(_gguf_kind(base / "main.gguf"), "model")
            self.assertEqual(_gguf_kind(base / "proj-mmproj.gguf"), "mmproj")
            self.assertEqual(_gguf_kind(base / "typed.gguf"), "mmproj")
            self.assertEqual(_gguf_kind(base / "spec.gguf"), "draft")
            self.assertEqual(_gguf_kind(base / "x-draft-y.gguf"), "draft")


class TestModelsManager(unittest.TestCase):
    def test_scan_registers_only_main_models(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            mdir = env.paths.models_dir
            (mdir / "qwen.gguf").write_bytes(_gguf_bytes())
            (mdir / "qwen-mmproj.gguf").write_bytes(_gguf_bytes("clip"))
            result = env.models.scan()
            self.assertEqual(result["new"], 1)
            self.assertEqual(env.models.list()[0].name, "qwen")

    def test_scan_reuses_unchanged_gguf_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            (env.paths.models_dir / "m.gguf").write_bytes(_gguf_bytes())
            env.models.scan()
            with mock.patch(
                    "llama_router.services.models_manager.read_gguf_info") as read:
                env.models.scan()
            read.assert_not_called()

    def test_missing_and_reappear(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            f = env.paths.models_dir / "m.gguf"
            f.write_bytes(_gguf_bytes())
            env.models.scan()
            f.unlink()
            env.models.scan()
            self.assertEqual(env.models.list()[0].state, "missing")
            f.write_bytes(_gguf_bytes())
            env.models.scan()
            self.assertEqual(env.models.list()[0].state, "valid")
            self.assertEqual(len(env.models.list()), 1)   # no duplicate

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            (env.paths.models_dir / "m.gguf").write_bytes(_gguf_bytes())
            env.models.scan()
            mid = env.models.list()[0].id
            env.models.set_enabled(mid, False)

            fresh = ModelsManager(env.paths, env.config, env.events)
            fresh.load()
            self.assertFalse(fresh.list()[0].enabled)

    def test_detect_mmproj(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            mdir = env.paths.models_dir
            (mdir / "m.gguf").write_bytes(_gguf_bytes())
            (mdir / "m-mmproj.gguf").write_bytes(_gguf_bytes("clip"))
            env.models.scan()
            mid = env.models.list()[0].id
            res = env.models.detect_mmproj(mid)
            self.assertIsNotNone(res["path"])
            self.assertIn("mmproj", res["path"])

    def test_add_remove_folder(self):
        with tempfile.TemporaryDirectory() as td, \
             tempfile.TemporaryDirectory() as td2:
            env = _Env(td)
            env.models.add_folder(td2)
            self.assertEqual(len(env.config.get().model_folders), 1)
            (Path(td2) / "ext.gguf").write_bytes(_gguf_bytes())
            env.models.scan()
            self.assertEqual(len(env.models.list()), 1)
            env.models.remove_folder(td2)
            self.assertEqual(env.config.get().model_folders, [])


class TestProfileManager(unittest.TestCase):
    def test_template_params_are_case_insensitive_copies(self):
        agent = ProfileManager.template_params("agent")
        self.assertEqual(agent, {
            "n-gpu-layers": -1,
            "jinja": "true",
            "reasoning": "on",
            "chat-template-kwargs": '{"preserve_thinking": true}',
        })
        agent["reasoning"] = "off"
        self.assertEqual(
            ProfileManager.template_params("Agent")["reasoning"], "on")
        self.assertEqual(ProfileManager.template_params("DEFAULT"), {
            "n-gpu-layers": -1,
            "reasoning": "off",
        })
        self.assertEqual(ProfileManager.template_params("Custom"), {})

    def test_update_rejects_duplicate_active_alias(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            env.models._models = {
                "m1": ModelEntry(id="m1", name="One", path="one.gguf"),
                "m2": ModelEntry(id="m2", name="Two", path="two.gguf"),
            }
            p1 = env.profiles.create("m1", "Default", {}, "same")
            p2 = env.profiles.create("m2", "Default", {}, "same")
            env.profiles.update(p1.id, {"active": True})
            with self.assertRaisesRegex(ValueError, "Duplicate active route alias"):
                env.profiles.update(p2.id, {"active": True})
            self.assertFalse(env.profiles.get(p2.id).active)

    def test_ensure_defaults_and_preset(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            (env.paths.models_dir / "qwen.gguf").write_bytes(_gguf_bytes())
            env.models.scan()
            mid = env.models.list()[0].id
            env.profiles.ensure_defaults(mid)
            plist = env.profiles.list(mid)
            self.assertEqual([p.name for p in plist], ["Agent", "Default"])
            self.assertTrue(any(p.active for p in plist))
            # idempotent
            env.profiles.ensure_defaults(mid)
            self.assertEqual(len(env.profiles.list(mid)), 2)

            from llama_router import preset
            text = preset.generate(env.models.list(), env.profiles.by_model(), {})
            self.assertIn("[qwen]", text)
            self.assertNotIn("ctx-size", text)  # profiles now have no default ctx

    def test_update_and_delete_for_model(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            p = env.profiles.create("model_x", "Test", {"ctx-size": 1024})
            env.profiles.update(p.id, {"params": {"ctx-size": 2048}})
            self.assertEqual(env.profiles.get(p.id).params["ctx-size"], 2048)
            self.assertEqual(env.profiles.delete_for_model("model_x"), 1)
            self.assertEqual(env.profiles.list(), [])

    def test_apply_preset_params_replaces_stored_values(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            p = env.profiles.create(
                "model_x", "Test", {"mirostat": "2", "temp": 0.8})
            changed = env.profiles.apply_preset_params(
                {p.id: {"temp": "0.8"}})
            self.assertEqual(changed, 1)
            self.assertNotIn("mirostat", env.profiles.get(p.id).params)
            self.assertEqual(env.profiles.get(p.id).params["temp"], "0.8")


class TestExtraParams(unittest.TestCase):
    def test_parse_and_format(self):
        from llama_router.ui.pages.profiles import _format_extra, _parse_extra
        d = _parse_extra("temp = 0.7\n# comment\nflash-attn: on\n\nbad-line\n")
        self.assertEqual(d, {"temp": "0.7", "flash-attn": "on"})
        # Structured keys (dedicated editor fields) stay out of the free box.
        out = _format_extra({"ctx-size": 4096, "temp": "0.7",
                             "custom-flag": "1"})
        self.assertEqual(out, "custom-flag = 1")

    def test_profile_context_slider_uses_gguf_limit(self):
        from llama_router.ui.pages.profiles import _context_limit

        self.assertEqual(_context_limit({"ctx": 131072}), 131072)
        self.assertEqual(_context_limit({}), 32768)
        self.assertEqual(_context_limit({"ctx": 2048}), 4096)

    def test_load_mode_migration_uses_supported_value(self):
        from llama_router.ui.pages.profiles import _migrate_load_mode

        self.assertEqual(
            _migrate_load_mode({"load-mode": "mmap+mlock"})["load-mode"],
            "mlock")
        self.assertEqual(
            _migrate_load_mode({"mlock": True})["load-mode"], "mlock")


if __name__ == "__main__":
    unittest.main()
