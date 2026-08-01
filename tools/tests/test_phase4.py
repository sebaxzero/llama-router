"""Phase 4 tests — server manager lifecycle and guards."""
from __future__ import annotations

import http.server
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from llama_router.core import storage
from llama_router.core.events import EventBus
from llama_router.core.logs import LogService
from llama_router.core.paths import PathManager
from llama_router.schemas import ServerStatus
from llama_router.services.config_manager import ConfigManager
from llama_router.services.download_manager import DownloadManager
from llama_router.services.models_manager import ModelsManager
from llama_router.services.profile_manager import ProfileManager
from llama_router.services.runtime_manager import RuntimeManager
from llama_router.services.server_manager import ServerManager


def _gguf_bytes() -> bytes:
    def s(t):
        b = t.encode()
        return struct.pack("<Q", len(b)) + b
    kv = [s("general.architecture") + struct.pack("<I", 8) + s("llama")]
    return (b"GGUF" + struct.pack("<I", 3)
            + struct.pack("<QQ", 0, len(kv)) + b"".join(kv))


class _Env:
    def __init__(self, td: str) -> None:
        self.paths = PathManager(Path(td))
        self.paths.ensure_dirs()
        storage.init_db(self.paths.db_path)
        self.events = EventBus()
        self.logs = LogService(self.events)
        self.config = ConfigManager(self.paths, self.events)
        self.config.load()
        self.models = ModelsManager(self.paths, self.config, self.events)
        self.models.load()
        self.profiles = ProfileManager(self.paths, self.models, self.events)
        self.profiles.load()
        self.downloads = DownloadManager(self.paths, self.config, self.events)
        self.runtimes = RuntimeManager(self.paths, self.config,
                                       self.downloads, self.events)
        self.runtimes.load()
        self.server = ServerManager(self.config, self.runtimes, self.models,
                                    self.profiles, self.events, self.paths,
                                    self.logs)

    def with_model(self):
        (self.paths.models_dir / "m.gguf").write_bytes(_gguf_bytes())
        self.models.scan()
        mid = self.models.list()[0].id
        self.profiles.ensure_defaults(mid)
        return self

    def with_runtime(self, real_exe: bool = False):
        folder = self.paths.runtime_dir / "b1-cpu"
        folder.mkdir(parents=True)
        exe = folder / ("llama-server.exe" if sys.platform == "win32"
                        else "llama-server")
        if real_exe:
            shutil.copy(sys.executable, exe)
        else:
            exe.write_bytes(b"MZ fake")
        rt = self.runtimes.register_extracted_folder(folder, "b1", "cpu")
        self.runtimes.set_active(rt.id)
        return self


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    """Stub llama-server: 200 on /health, empty model list on /v1/models."""

    def do_GET(self):
        body = b'{"data": []}' if self.path.startswith("/v1/models") else b"OK"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class TestHealthTransition(unittest.TestCase):
    """STARTING → RUNNING must reach the UI.

    Regression: the health loop flipped _status to RUNNING and published only
    `server_health`, so every status label (which subscribes to
    `server_status`) stayed stuck on "starting" while the server was up.
    """

    def test_running_transition_publishes_server_status(self):
        httpd = http.server.HTTPServer(("127.0.0.1", 0), _HealthHandler)
        http_thread = threading.Thread(
            target=httpd.serve_forever, daemon=True)
        http_thread.start()
        port = httpd.server_address[1]
        # A live child so the loop's "is our process still alive?" guard passes.
        child = subprocess.Popen([sys.executable, "-c",
                                  "import time; time.sleep(30)"])
        try:
            with tempfile.TemporaryDirectory() as td:
                env = _Env(td)
                env.config.update({"server": {"port": port}})

                seen: list[dict] = []
                env.events.subscribe("server_status", seen.append)

                env.server._process = child
                env.server._set_status(ServerStatus.STARTING)
                seen.clear()   # drop the STARTING publish

                t = threading.Thread(
                    target=env.server._health_loop,
                    args=(env.server._session,), daemon=True)
                t.start()

                deadline = time.monotonic() + 20
                while (env.server.status != ServerStatus.RUNNING
                       and time.monotonic() < deadline):
                    time.sleep(0.05)
                env.server._session += 1   # unblock the loop
                t.join(timeout=10)

                self.assertEqual(env.server.status, ServerStatus.RUNNING)
                env.events.drain()
                statuses = [e.get("status") for e in seen]
                self.assertIn("running", statuses,
                              f"no server_status event for RUNNING: {statuses}")
                self.assertGreater(env.server.uptime, 0)
        finally:
            child.terminate()
            child.wait(timeout=10)
            httpd.shutdown()
            httpd.server_close()
            http_thread.join(timeout=5)


class TestStartValidation(unittest.TestCase):
    def test_no_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td).with_model()
            self.assertEqual(env.server.start()["reason"], "no_runtime")

    def test_no_models(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td).with_runtime()
            self.assertEqual(env.server.start()["reason"], "no_models")

    def test_no_models_when_all_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td).with_runtime().with_model()
            env.models.set_enabled(env.models.list()[0].id, False)
            self.assertEqual(env.server.start()["reason"], "no_models")

    def test_port_in_use(self):
        import http.server
        import threading
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td).with_runtime().with_model()
            httpd = http.server.ThreadingHTTPServer(
                ("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
            http_thread = threading.Thread(
                target=httpd.serve_forever, daemon=True)
            http_thread.start()
            try:
                env.config.update(
                    {"server": {"port": httpd.server_address[1]}})
                self.assertEqual(env.server.start()["reason"], "port_in_use")
            finally:
                httpd.shutdown()
                httpd.server_close()
                http_thread.join(timeout=5)


class TestBuildCmd(unittest.TestCase):
    def test_preview_is_empty_without_runtime(self):
        """A fresh data directory must still be able to build the Dashboard."""
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            self.assertEqual(env.server.build_cmd_preview(), [])

    def test_preview_flags(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td).with_runtime()
            env.config.update({"server": {
                "api_key": "sk-x", "metrics": True, "cont_batching": False,
                "extra_args": "--flash-attn on"}})
            cmd = env.server.build_cmd_preview()
            joined = " ".join(cmd)
            self.assertIn("--models-preset", joined)
            self.assertIn("--api-key sk-x", joined)
            self.assertIn("--metrics", joined)
            self.assertIn("--no-cont-batching", joined)
            self.assertIn("--flash-attn on", joined)
            self.assertIn("--port 8080", joined)

    def test_log_command_redacts_api_key(self):
        cmd = ["llama-server", "--api-key", "sk-secret", "--metrics"]
        safe = ServerManager._redact_cmd(cmd)
        self.assertEqual(safe[2], "<redacted>")
        self.assertEqual(cmd[2], "sk-secret")


class TestUnexpectedExit(unittest.TestCase):
    def test_bad_binary_lands_in_error(self):
        """A binary that exits immediately must end in ERROR, not hang."""
        with tempfile.TemporaryDirectory() as td:
            # real_exe: python.exe rejects llama flags and exits ≠ 0 fast
            env = _Env(td).with_runtime(real_exe=True).with_model()
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            env.config.update({"server": {"port": port}})
            result = env.server.start()
            self.assertTrue(result["ok"])
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if env.server.status == ServerStatus.ERROR:
                    break
                time.sleep(0.1)
            self.assertEqual(env.server.status, ServerStatus.ERROR)
            # preset was generated on the way
            self.assertTrue(env.paths.preset_ini.exists())

    def test_stop_from_error_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td).with_runtime(real_exe=True).with_model()
            env.server.start()
            deadline = time.monotonic() + 15
            while (time.monotonic() < deadline
                   and env.server.status != ServerStatus.ERROR):
                time.sleep(0.1)
            env.server.stop()
            self.assertEqual(env.server.status, ServerStatus.STOPPED)


class TestOrphan(unittest.TestCase):
    def test_reap_noop_without_record(self):
        with tempfile.TemporaryDirectory() as td:
            _Env(td).server.reap_orphan()   # must not raise

    def test_pid_matches_rejects_wrong_name(self):
        import os
        # Current process runs python, not llama-server
        self.assertFalse(
            ServerManager._pid_matches(os.getpid(), "llama-server.exe"))

    def test_instance_id_is_python_pid(self):
        """Each instance is identified by its Python process PID."""
        import os
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            self.assertEqual(env.server._instance_id, str(os.getpid()))

    def test_lockfile_created_on_reap(self):
        """reap_orphan() creates our own lockfile."""
        import os, json
        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            env.server.reap_orphan()
            expected = env.paths.config_dir / \
                f"instance_{os.getpid()}.lock"
            self.assertTrue(expected.exists())
            data = json.loads(expected.read_text())
            self.assertEqual(data["pid_python"], os.getpid())

    def test_peer_alive_not_reaped(self):
        """A peer instance with an active Python pid is never touched."""
        import os, json
        with tempfile.TemporaryDirectory() as td:
            # Write a peer lockfile pointing to THIS process (alive).
            peer_lock = (
                Path(td) / "config" / "instance_999999.lock")
            peer_lock.parent.mkdir(parents=True, exist_ok=True)
            peer_lock.write_text(json.dumps({
                "pid_python": os.getpid(),   # alive!
                "pid_llama": 888888,
            }))
            env = _Env(td)
            env.server.reap_orphan()
            # Peer lockfile still exists (we skipped it).
            self.assertTrue(peer_lock.exists())

    def test_peer_with_unknown_liveness_is_never_reaped(self):
        """Access errors must fail safe instead of killing a live peer."""
        import json
        with tempfile.TemporaryDirectory() as td:
            peer_lock = Path(td) / "config" / "instance_999999.lock"
            peer_lock.parent.mkdir(parents=True, exist_ok=True)
            peer_lock.write_text(json.dumps({
                "pid_python": 999999,
                "pid_llama": 888888,
            }))
            env = _Env(td)
            with mock.patch.object(env.server, "_process_alive",
                                   return_value=None), \
                    mock.patch.object(env.server, "_pid_matches",
                                      return_value=True), \
                    mock.patch.object(env.server, "_kill_tree") as kill:
                env.server.reap_orphan()
            self.assertTrue(peer_lock.exists())
            kill.assert_not_called()

    def test_peer_dead_reaped(self):
        """A peer instance with a dead Python pid is reaped."""
        import json
        with tempfile.TemporaryDirectory() as td:
            # Write a peer lockfile pointing to a dead pid.
            peer_lock = (
                Path(td) / "config" / "instance_999999.lock")
            peer_lock.parent.mkdir(parents=True, exist_ok=True)
            peer_lock.write_text(json.dumps({
                "pid_python": 999999,   # dead
                "pid_llama": 888888,
            }))
            env = _Env(td)
            env.server.reap_orphan()
            # Peer lockfile was cleaned up.
            self.assertFalse(peer_lock.exists())
            # _kill_tree was called with the orphan pid — we can't easily
            # verify the taskkill call, but the lockfile cleanup proves reap
            # ran.

    def test_stale_lockfile_cleaned(self):
        """Lockfiles with both pids dead are cleaned up without error."""
        import json
        with tempfile.TemporaryDirectory() as td:
            stale = (Path(td) / "config" / "instance_111111.lock")
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_text(json.dumps({
                "pid_python": 111111,
                "pid_llama": 222222,
            }))
            env = _Env(td)
            env.server.reap_orphan()
            self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
