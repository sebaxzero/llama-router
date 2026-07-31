"""Phase 3 tests — download manager (local HTTP server) and runtime manager."""
from __future__ import annotations

import http.server
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import tarfile
import io
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from llama_router.core import storage
from llama_router.core.utils import extract_archive
from llama_router.core.events import EventBus
from llama_router.core.paths import PathManager
from llama_router.schemas import DownloadItem, DownloadState
from llama_router.services.config_manager import ConfigManager
from llama_router.services.download_manager import DownloadManager
from llama_router.services.runtime_manager import RuntimeManager, asset_matches_os

PAYLOAD = bytes(range(256)) * 4096   # 1 MiB deterministic blob


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        start = 0
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range",
                             f"bytes {start}-{len(PAYLOAD)-1}/{len(PAYLOAD)}")
        else:
            self.send_response(200)
        body = PAYLOAD[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _Env:
    def __init__(self, td: str) -> None:
        self.paths = PathManager(Path(td))
        self.paths.ensure_dirs()
        storage.init_db(self.paths.db_path)
        self.events = EventBus()
        self.config = ConfigManager(self.paths, self.events)
        self.config.load()
        self.downloads = DownloadManager(self.paths, self.config, self.events)
        self.runtimes = RuntimeManager(self.paths, self.config,
                                       self.downloads, self.events)
        self.runtimes.load()


def _wait(item: DownloadItem, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if item.state in (DownloadState.COMPLETED, DownloadState.FAILED,
                          DownloadState.CANCELLED):
            return
        time.sleep(0.05)
    raise TimeoutError(f"download stuck in {item.state}")


class TestDownloadManager(unittest.TestCase):
    def test_concurrency_limit_updates_from_config_event(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            self.assertEqual(env.downloads._limit, 3)
            env.events.publish("config_saved", {"max_concurrent_downloads": 7})
            env.events.drain()
            self.assertEqual(env.downloads._limit, 7)

    @mock.patch("llama_router.services.download_manager._http_text")
    def test_github_releases_use_public_feed(self, fetch):
        fetch.return_value = """\
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <updated>2026-07-30T20:34:50Z</updated>
    <title>b10199</title>
    <content type="html">&lt;a href="https://github.com/ggml-org/llama.cpp/releases/download/b10199/llama-b10199-bin-win-cpu-x64.zip"&gt;Windows&lt;/a&gt;&lt;a href="https://example.com/source.txt"&gt;notes&lt;/a&gt;</content>
  </entry>
</feed>"""
        with tempfile.TemporaryDirectory() as td:
            releases = _Env(td).downloads.gh_releases()

        self.assertEqual(releases, [{
            "tag": "b10199",
            "published": "2026-07-30T20:34:50Z",
            "assets": [{
                "name": "llama-b10199-bin-win-cpu-x64.zip",
                "url": "https://github.com/ggml-org/llama.cpp/releases/download/b10199/llama-b10199-bin-win-cpu-x64.zip",
                "size": 0,
            }],
        }])
        self.assertEqual(fetch.call_args.args[0],
                         "https://github.com/ggml-org/llama.cpp/releases.atom")

    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                                    _RangeHandler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _url(self) -> str:
        return f"http://127.0.0.1:{self.port}/blob.bin"

    def test_download_completes(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            item = env.downloads.start_runtime(self._url(), "blob.bin", td)
            _wait(item)
            self.assertEqual(item.state, DownloadState.COMPLETED)
            self.assertEqual(Path(item.destination).read_bytes(), PAYLOAD)
            self.assertEqual(item.total_bytes, len(PAYLOAD))

    def test_resume_from_part(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            dest = Path(td) / "blob.bin"
            # Simulate an interrupted download: half the payload in .part
            Path(str(dest) + ".part").write_bytes(PAYLOAD[:len(PAYLOAD) // 2])
            item = env.downloads.start_runtime(self._url(), "blob.bin", td)
            _wait(item)
            self.assertEqual(item.state, DownloadState.COMPLETED)
            self.assertEqual(dest.read_bytes(), PAYLOAD)

    def test_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            item = env.downloads.start_runtime(
                "http://127.0.0.1:1/nope.bin", "nope.bin", td)
            _wait(item, timeout=40)
            self.assertEqual(item.state, DownloadState.FAILED)
            self.assertTrue(item.error)

    def test_on_complete_runs_and_failure_captured(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            ran = []
            item = env.downloads.start_runtime(
                self._url(), "blob.bin", td,
                on_complete=lambda it: ran.append(it.destination))
            _wait(item)
            time.sleep(0.2)
            self.assertEqual(len(ran), 1)

    def test_resumed_download_uses_registered_completion_handler(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            dest = Path(td) / "blob.bin"
            saved = DownloadItem(
                id="dl_resume", kind="runtime", name="blob.bin",
                url=self._url(), destination=str(dest),
                meta={"marker": "durable"})
            storage.db_write(env.paths.db_path, "downloads", [saved.to_dict()])

            fresh = DownloadManager(env.paths, env.config, env.events)
            completed = []
            fresh.set_completion_handler(
                "runtime", lambda item: completed.append(item.meta["marker"]))
            fresh.load()
            item = fresh.get("dl_resume")
            self.assertIsNotNone(item)
            _wait(item)
            deadline = time.monotonic() + 2
            while not completed and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(completed, ["durable"])


class TestArchiveSafety(unittest.TestCase):
    def test_tar_cannot_escape_target(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            archive = base / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as tf:
                data = b"owned"
                member = tarfile.TarInfo("../outside.txt")
                member.size = len(data)
                tf.addfile(member, io.BytesIO(data))
            with self.assertRaisesRegex(ValueError, "Unsafe archive member"):
                extract_archive(archive, base / "target")
            self.assertFalse((base / "outside.txt").exists())


class TestRuntimeManager(unittest.TestCase):
    def _fake_build(self, base: Path, name: str = "b1000-cpu",
                    cuda: bool = False) -> Path:
        folder = base / name
        folder.mkdir(parents=True)
        (folder / "llama-server.exe").write_bytes(b"MZ fake")
        if cuda:
            (folder / "ggml-cuda.dll").write_bytes(b"fake")
        return folder

    def test_register_detect_activate_delete(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            folder = self._fake_build(env.paths.runtime_dir, "b100-cuda-12",
                                      cuda=True)
            rt = env.runtimes.register_extracted_folder(folder, "b100", "cuda")
            self.assertEqual(rt.state, "installed")
            self.assertIn("cuda 12", rt.name)
            # idempotent
            again = env.runtimes.register_extracted_folder(folder, "b100", "cuda")
            self.assertEqual(again.id, rt.id)

            env.runtimes.set_active(rt.id)
            self.assertEqual(env.runtimes.get_active().id, rt.id)
            exe = env.runtimes.get_executable()
            self.assertTrue(str(exe).endswith("llama-server.exe"))

            env.runtimes.delete(rt.id)
            self.assertEqual(env.runtimes.list(), [])
            self.assertFalse(folder.exists())          # github source → files gone
            self.assertIsNone(env.config.get().active_runtime_id)

    def test_import_local_never_deletes_files(self):
        with tempfile.TemporaryDirectory() as td, \
             tempfile.TemporaryDirectory() as build_td:
            env = _Env(td)
            folder = self._fake_build(Path(build_td), "mybuild")
            rt = env.runtimes.import_local(str(folder), "my build")
            self.assertEqual(rt.state, "custom")
            self.assertEqual(rt.backend, "cpu")
            env.runtimes.delete(rt.id)
            self.assertTrue(folder.exists())           # custom → files kept

    def test_import_local_requires_exe(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            with self.assertRaises(FileNotFoundError):
                env.runtimes.import_local(td, "empty")

    def test_invalid_on_load_when_exe_missing(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            folder = self._fake_build(env.paths.runtime_dir)
            rt = env.runtimes.register_extracted_folder(folder, "b1000", "cpu")
            (folder / "llama-server.exe").unlink()
            fresh = RuntimeManager(env.paths, env.config, env.downloads,
                                   env.events)
            fresh.load()
            self.assertEqual(fresh.get(rt.id).state, "invalid")
            self.assertIsNone(fresh.get_active())

    def test_install_asset_extracts_and_registers(self):
        with tempfile.TemporaryDirectory() as td, \
             tempfile.TemporaryDirectory() as srv_td:
            # Serve a zip containing a fake llama-server
            zpath = Path(srv_td) / "llama-b9-bin-win-cpu-x64.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr("build/bin/llama-server.exe", "MZ fake")

            class H(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *a, **kw):
                    super().__init__(*a, directory=srv_td, **kw)

                def log_message(self, *a):
                    pass

            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                env = _Env(td)
                url = f"http://127.0.0.1:{httpd.server_address[1]}/{zpath.name}"
                item = env.runtimes.install_asset(
                    "b9", {"name": zpath.name, "url": url, "size": 1})
                _wait(item)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not env.runtimes.list():
                    time.sleep(0.05)
                self.assertEqual(len(env.runtimes.list()), 1)
                rt = env.runtimes.list()[0]
                self.assertEqual(rt.version, "b9")
                self.assertEqual(rt.state, "installed")
                self.assertTrue((Path(rt.path)).is_dir())
            finally:
                httpd.shutdown()


class TestOsFilter(unittest.TestCase):
    def test_asset_matches_cpu_architecture(self):
        with mock.patch("llama_router.services.runtime_manager.sys.platform",
                        "linux"):
            self.assertTrue(asset_matches_os(
                "llama-bin-ubuntu-arm64.tar.gz", machine="aarch64"))
            self.assertFalse(asset_matches_os(
                "llama-bin-ubuntu-x64.tar.gz", machine="aarch64"))
            self.assertTrue(asset_matches_os(
                "llama-bin-ubuntu-x64.tar.gz", machine="AMD64"))

    def test_asset_matches_os(self):
        if sys.platform == "win32":
            self.assertTrue(asset_matches_os("llama-b1-bin-win-cuda-12.4-x64.zip"))
            self.assertFalse(asset_matches_os("llama-b1-bin-ubuntu-x64.zip"))


if __name__ == "__main__":
    unittest.main()
