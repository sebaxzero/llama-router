import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from llama_router.core.paths import PathManager
from llama_router.core.storage import InstanceGuard, db_read, db_write, init_db
from llama_router.schemas import DownloadItem, DownloadState
from tools import screenshots


class ScreenshotStateTests(unittest.TestCase):
    def test_camera_crop_keeps_aspect_and_stays_in_bounds(self):
        crop = screenshots._camera_box(
            (1920, 1080), (960, 504), (0, 0), 1.5)
        self.assertEqual(crop[:2], (0, 0))
        self.assertLessEqual(crop[2], 1920)
        self.assertLessEqual(crop[3], 1080)
        self.assertAlmostEqual(
            (crop[2] - crop[0]) / (crop[3] - crop[1]),
            960 / 504, places=2)

    def test_ease_has_stable_endpoints(self):
        self.assertEqual(screenshots._ease(-1), 0)
        self.assertEqual(screenshots._ease(0), 0)
        self.assertEqual(screenshots._ease(1), 1)
        self.assertEqual(screenshots._ease(2), 1)
        self.assertAlmostEqual(screenshots._ease(0.5), 0.5)

    def test_capture_base_isolated_and_guard_released(self):
        with tempfile.TemporaryDirectory() as temp:
            real = Path(temp) / "real"
            paths = PathManager(real)
            paths.ensure_dirs()
            init_db(paths.db_path)
            db_write(paths.db_path, "existing", {"page": "dashboard"})
            paths.preset_ini.write_text("[*]\nmodel = real.gguf\n",
                                        encoding="utf-8")
            before_db = paths.db_path.read_bytes()
            before_preset = paths.preset_ini.read_bytes()

            with screenshots.isolated_capture_base(real) as capture:
                capture_paths = PathManager(capture)
                self.assertEqual(
                    db_read(capture_paths.db_path, "existing"),
                    {"page": "dashboard"})
                db_write(capture_paths.db_path, "existing",
                         {"page": "capture"})
                capture_paths.preset_ini.write_text("capture\n",
                                                    encoding="utf-8")
                self.assertEqual(db_read(capture_paths.db_path, "existing"),
                                 {"page": "capture"})

            self.assertEqual(paths.db_path.read_bytes(), before_db)
            self.assertEqual(paths.preset_ini.read_bytes(), before_preset)
            guard = InstanceGuard(paths.config_dir / "llama-router.instance")
            self.assertTrue(guard.acquire())
            guard.release()

    def test_capture_rejects_an_existing_app_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            real = Path(temp) / "real"
            paths = PathManager(real)
            paths.ensure_dirs()
            held = InstanceGuard(paths.config_dir / "llama-router.instance")
            self.assertTrue(held.acquire())
            try:
                with self.assertRaises(RuntimeError):
                    with screenshots.isolated_capture_base(real):
                        pass
            finally:
                held.release()

    def test_capture_app_closes_fixture_after_build_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            real = Path(temp) / "real"
            paths = PathManager(real)
            paths.ensure_dirs()
            init_db(paths.db_path)
            playground = mock.Mock()
            logs = mock.Mock()
            fake_app = SimpleNamespace(
                ctx=SimpleNamespace(
                    services={"playground": playground}, logs=logs))
            with mock.patch.object(screenshots, "build_app",
                                   return_value=fake_app) as build, \
                    mock.patch.object(screenshots, "_close_app") as close:
                with self.assertRaisesRegex(RuntimeError, "fixture failed"):
                    with screenshots.capture_app(real_base=real):
                        raise RuntimeError("fixture failed")

            build.assert_called_once()
            close.assert_called_once_with(fake_app)
            playground.cancel.assert_called_once_with()
            logs.close.assert_called_once_with()
            guard = InstanceGuard(paths.config_dir / "llama-router.instance")
            self.assertTrue(guard.acquire())
            guard.release()

    def test_empty_registry_fixture_does_not_write_real_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            real = Path(temp) / "real"
            real_paths = PathManager(real)
            real_paths.ensure_dirs()
            init_db(real_paths.db_path)
            with screenshots.isolated_capture_base(real) as capture:
                from llama_router.core.events import EventBus
                from llama_router.services.config_manager import ConfigManager
                from llama_router.services.models_manager import ModelsManager
                from llama_router.services.profile_manager import ProfileManager

                events = EventBus()
                config = ConfigManager(PathManager(capture), events)
                config.load()
                models = ModelsManager(PathManager(capture), config, events)
                models.load()
                profiles = ProfileManager(PathManager(capture), models, events)
                profiles.load()
                screenshots._ensure_sample_model(models, profiles)
                self.assertTrue(profiles.list("model_sample01"))

            self.assertEqual(db_read(real_paths.db_path, "profiles", []), [])

    def test_capture_does_not_resume_downloads_from_copied_queue(self):
        try:
            import tkinter as tk
        except ImportError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")

        with tempfile.TemporaryDirectory() as temp:
            real = Path(temp) / "real"
            real_paths = PathManager(real)
            real_paths.ensure_dirs()
            init_db(real_paths.db_path)
            source = real / "archive.bin"
            source.write_bytes(b"capture must not download this")
            destination = Path(temp) / "outside" / "archive.bin"
            pending = DownloadItem(
                id="download-from-real-queue", kind="runtime",
                name=source.name, url=source.as_uri(),
                destination=str(destination), state=DownloadState.QUEUED)
            db_write(real_paths.db_path, "downloads", [pending.to_dict()])
            real_paths.preset_ini.write_bytes(b"[before]\nmodel = real.gguf\n")
            before_db = real_paths.db_path.read_bytes()
            before_preset = real_paths.preset_ini.read_bytes()

            with screenshots.capture_app(real_base=real) as app:
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    app.root.update()
                    app.ctx.events.drain()
                    time.sleep(0.02)

            self.assertFalse(destination.exists())
            self.assertFalse(Path(str(destination) + ".part").exists())
            self.assertEqual(real_paths.db_path.read_bytes(), before_db)
            self.assertEqual(real_paths.preset_ini.read_bytes(), before_preset)

    def test_readme_capture_names_and_links_match_the_app(self):
        from llama_router.ui.app import PAGES

        self.assertEqual(set(screenshots.README_PAGES), set(PAGES))
        readme = (screenshots.ROOT / "README.md").read_text(encoding="utf-8")
        links = re.findall(r"llama_router/assets/screenshots/[^)\" ]+", readme)
        self.assertTrue(links)
        for link in links:
            self.assertTrue((screenshots.ROOT / link).is_file(), link)

    def test_dev_video_writes_the_default_readme_assets(self):
        runner = (screenshots.ROOT / "tools" / "dev.bat").read_text(
            encoding="utf-8")
        command = next(
            line for line in runner.splitlines()
            if "tools\\screenshots.py --video" in line)
        self.assertNotIn("--out", command)


if __name__ == "__main__":
    unittest.main()
