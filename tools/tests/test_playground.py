"""Playground tests — SSE streaming against a fake server, session CRUD."""
from __future__ import annotations

import http.server
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from llama_router.core import storage
from llama_router.core.events import EventBus
from llama_router.core.paths import PathManager
from llama_router.services.playground import (PlaygroundService, _describe,
                                              _summarise)


class _FakeConfig:
    def __init__(self, api_key: str = "") -> None:
        self._key = api_key

    def get(self):
        class _S:
            api_key = self._key
        return type("Cfg", (), {"server": _S()})()


class _FakeServer:
    def __init__(self, port: int, models=()) -> None:
        self._port = port
        self._loaded_models = list(models)

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"


class _FakeProfiles:
    def __init__(self, profiles=()) -> None:
        self._p = list(profiles)

    def by_model(self):
        return {"m1": self._p}


class _Profile:
    def __init__(self, alias, active=True):
        self.route_alias, self.name, self.active = alias, alias, active


# ── Fake llama-server: streams a canned SSE completion ───────────────────────

_CHUNKS = [
    '{"choices":[{"delta":{"content":"Hel"}}]}',
    '{"choices":[{"delta":{"content":"lo"}}]}',
    '{"choices":[{"delta":{}}],"timings":{"predicted_n":2,'
    '"predicted_per_second":40.0}}',
]


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for c in _CHUNKS:
                self.wfile.write(f"data: {c}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)   # gives the cancel test a window to abort
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except OSError:
            pass   # the cancel test drops the connection mid-stream, by design

    def log_message(self, *_a):
        pass


class _Collector:
    """Drains the bus into a list, like App._pump does on the UI thread."""

    def __init__(self, bus: EventBus) -> None:
        self.seen: list[tuple[str, dict]] = []
        for evt in ("pg_token", "pg_done", "pg_error"):
            bus.subscribe(evt, lambda d, e=evt: self.seen.append((e, d)))
        self._bus = bus

    def wait_for(self, event: str, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._bus.drain()
            for name, data in self.seen:
                if name == event:
                    return data
            time.sleep(0.02)
        raise AssertionError(f"no {event} within {timeout}s: {self.seen}")


class TestStreaming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _service(self, td, models=()):
        paths = PathManager(Path(td))
        paths.ensure_dirs()
        storage.init_db(paths.db_path)
        bus = EventBus()
        svc = PlaygroundService(_FakeConfig(), _FakeServer(self.port, models),
                                _FakeProfiles(), bus, paths)
        return svc, bus

    def test_stream_publishes_tokens_then_done(self):
        with tempfile.TemporaryDirectory() as td:
            svc, bus = self._service(td)
            col = _Collector(bus)
            svc.send([{"role": "user", "content": "hi"}], "m")
            done = col.wait_for("pg_done")
            self.assertEqual(done["text"], "Hello")
            self.assertEqual([d["text"] for e, d in col.seen if e == "pg_token"],
                             ["Hel", "lo"])
            self.assertEqual(done["stats"]["tokens"], 2)
            self.assertAlmostEqual(done["stats"]["tps"], 40.0)

    def test_connection_failure_publishes_error(self):
        with tempfile.TemporaryDirectory() as td:
            paths = PathManager(Path(td))
            paths.ensure_dirs()
            bus = EventBus()
            # Port 1 is never listening — urlopen fails fast.
            svc = PlaygroundService(_FakeConfig(), _FakeServer(1),
                                    _FakeProfiles(), bus, paths)
            col = _Collector(bus)
            svc.send([{"role": "user", "content": "hi"}], "m")
            self.assertTrue(col.wait_for("pg_error")["error"])

    def test_cancel_suppresses_done(self):
        with tempfile.TemporaryDirectory() as td:
            svc, bus = self._service(td)
            col = _Collector(bus)
            svc.send([{"role": "user", "content": "hi"}], "m")
            svc.cancel()
            time.sleep(0.5)
            bus.drain()
            self.assertFalse([e for e, _ in col.seen if e == "pg_done"])

    def test_old_worker_cannot_clear_new_response(self):
        with tempfile.TemporaryDirectory() as td:
            svc, _bus = self._service(td)
            current = mock.Mock()
            svc._session = 2
            svc._resp = (2, current)

            # This is the guarded cleanup performed by worker/session 1.
            with svc._resp_lock:
                if svc._resp is not None and svc._resp[0] == 1:
                    svc._resp = None

            self.assertTrue(svc.streaming)
            svc.cancel()
            current.close.assert_called_once_with()


class TestModels(unittest.TestCase):
    def test_prefers_loaded_models(self):
        with tempfile.TemporaryDirectory() as td:
            paths = PathManager(Path(td))
            svc = PlaygroundService(_FakeConfig(), _FakeServer(1, ["a", "b"]),
                                    _FakeProfiles([_Profile("z")]), EventBus(),
                                    paths)
            self.assertEqual(svc.models(), ["a", "b"])

    def test_falls_back_to_active_route_aliases(self):
        with tempfile.TemporaryDirectory() as td:
            paths = PathManager(Path(td))
            svc = PlaygroundService(
                _FakeConfig(), _FakeServer(1),
                _FakeProfiles([_Profile("z"), _Profile("off", active=False)]),
                EventBus(), paths)
            self.assertEqual(svc.models(), ["z"])


class TestSessions(unittest.TestCase):
    def test_crud_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            paths = PathManager(Path(td))
            paths.ensure_dirs()
            storage.init_db(paths.db_path)
            svc = PlaygroundService(_FakeConfig(), _FakeServer(1),
                                    _FakeProfiles(), EventBus(), paths)
            self.assertEqual(svc.sessions(), [])

            s = svc.new_session(name="chat one")
            s["messages"].append({"role": "user", "content": "hi"})
            svc.save_session(s)
            rows = svc.sessions()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], "chat one")
            self.assertEqual(rows[0]["messages"][0]["content"], "hi")

            # saving again upserts rather than duplicating
            s["name"] = "renamed"
            svc.save_session(s)
            rows = svc.sessions()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["name"], "renamed")

            svc.delete_session(s["id"])
            self.assertEqual(svc.sessions(), [])

    def test_corrupt_rows_are_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            paths = PathManager(Path(td))
            paths.ensure_dirs()
            storage.init_db(paths.db_path)
            storage.db_write(paths.db_path, "pg_sessions",
                             [{"no_id": 1}, {"id": "ok"}])
            svc = PlaygroundService(_FakeConfig(), _FakeServer(1),
                                    _FakeProfiles(), EventBus(), paths)
            self.assertEqual([s["id"] for s in svc.sessions()], ["ok"])


class TestSummarise(unittest.TestCase):
    def test_falls_back_to_delta_count(self):
        stats = _summarise({}, 10, 2.0)
        self.assertEqual(stats["tokens"], 10)
        self.assertAlmostEqual(stats["tps"], 5.0)

    def test_prefers_usage_over_count(self):
        stats = _summarise({"usage": {"completion_tokens": 99}}, 3, 1.0)
        self.assertEqual(stats["tokens"], 99)

    def test_describe_plain_exception(self):
        self.assertEqual(_describe(ValueError("boom")), "boom")


if __name__ == "__main__":
    unittest.main()
