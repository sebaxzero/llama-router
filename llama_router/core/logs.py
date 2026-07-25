"""Unified application log sink.

One place funnels every log line — Python `logging` from all modules (via the
`AppLogHandler` bridge) and streamed llama-server stdout — into:

  • a bounded ring buffer               → LogView queries
  • a rotating file (logs/app.log)      → 5 MB × 3 on disk
  • a "log_line" event on the EventBus  → live UI stream

Entry shape everywhere:
    {"id": int, "ts": float, "level": str, "source": str, "message": str}
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from llama_router.core.events import EventBus

# Levels the UI understands. "request" is synthetic (HTTP lines from
# llama-server); stdlib CRITICAL collapses to "error".
_VALID_LEVELS = ("debug", "info", "request", "warning", "error")

_MAX_MSG = 4000   # truncate absurd lines (some llama.cpp dumps are huge)

_FILE_FORMAT = "%(asctime)s %(levelname)-7s [%(source)s] %(message)s"

_LEVELNO_TO_LEVEL = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


def parse_binary_level(text: str) -> str:
    """Classify a raw llama.cpp stdout/stderr line into one of our levels."""
    t = text.lower()
    if "error" in t or "err]" in t:
        return "error"
    if "warn" in t:
        return "warning"
    if any(x in t for x in ("post /v1", "get /v1", "[req", "200 ok", "200ok")):
        return "request"
    return "info"


def _logger_name_to_source(name: str) -> str:
    n = (name or "").lower()
    if "download_manager" in n:
        return "downloads"
    return "app"


class LogService:
    def __init__(self, events: EventBus, max_entries: int = 2000) -> None:
        self._events = events
        self._max = max_entries
        self._lock = threading.Lock()
        self._ring: list[dict] = []
        self._next_id = 1
        self._file_handler: RotatingFileHandler | None = None
        self._app_handler: AppLogHandler | None = None

    # ── setup / teardown ─────────────────────────────────────────────────────

    def install(self, logs_dir: Path) -> None:
        """Open the rotating file and attach the stdlib-logging bridge."""
        root = logging.getLogger()
        if any(isinstance(h, AppLogHandler) for h in root.handlers):
            return  # already installed

        logs_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            logs_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3,
            encoding="utf-8", delay=True,
        )
        handler.setFormatter(logging.Formatter(_FILE_FORMAT))
        self._file_handler = handler

        self._app_handler = AppLogHandler(self)
        self._app_handler.setLevel(logging.INFO)
        root.addHandler(self._app_handler)

    def close(self) -> None:
        if self._app_handler is not None:
            logging.getLogger().removeHandler(self._app_handler)
            self._app_handler = None
        if self._file_handler is not None:
            try:
                self._file_handler.close()
            except Exception:
                pass
            self._file_handler = None

    # ── ingest ────────────────────────────────────────────────────────────────

    def log(self, source: str, level: str, message: str) -> dict:
        """Record one line: assign a monotonic id, append to ring, write to
        disk, publish to the bus. Thread-safe."""
        if level not in _VALID_LEVELS:
            level = "info"
        if message and len(message) > _MAX_MSG:
            message = message[:_MAX_MSG] + " …[truncated]"
        with self._lock:
            entry = {
                "id": self._next_id,
                "ts": time.time(),
                "level": level,
                "source": source,
                "message": message,
            }
            self._next_id += 1
            self._ring.append(entry)
            if len(self._ring) > self._max:
                del self._ring[0]
        self._write_file(entry)
        self._events.publish("log_line", entry)
        return entry

    def _write_file(self, entry: dict) -> None:
        handler = self._file_handler
        if handler is None:
            return
        record = logging.LogRecord(
            name="log", level=logging.INFO, pathname="", lineno=0,
            msg=entry["message"], args=None, exc_info=None,
        )
        # Our levels/source are custom, so drive the formatter directly.
        record.levelname = entry["level"].upper()
        record.source = entry["source"]
        record.created = entry["ts"]
        record.msecs = (entry["ts"] - int(entry["ts"])) * 1000
        try:
            handler.handle(record)   # acquires handler.lock, rotates as needed
        except Exception:
            pass

    # ── query ────────────────────────────────────────────────────────────────

    def get(self, limit: int = 1000, sources=None) -> list[dict]:
        with self._lock:
            items = list(self._ring)
        if sources:
            sset = set(sources)
            items = [e for e in items if e["source"] in sset]
        if limit and limit > 0:
            items = items[-limit:]
        return items

    def clear(self) -> None:
        """Empty the ring and truncate the file (under the handler's own lock —
        truncating with a stale offset null-pads the file on Windows)."""
        with self._lock:
            self._ring.clear()
        handler = self._file_handler
        if handler is not None:
            handler.acquire()
            try:
                if handler.stream is not None:
                    handler.stream.seek(0)
                    handler.stream.truncate()
                    handler.stream.flush()
            except Exception:
                pass
            finally:
                handler.release()


class AppLogHandler(logging.Handler):
    """Bridge stdlib `logging` records into LogService. Never raises."""

    def __init__(self, service: LogService) -> None:
        super().__init__()
        self._service = service
        self._local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        # Reentrancy guard: a side-effect that logs during emit must not recurse.
        if getattr(self._local, "busy", False):
            return
        self._local.busy = True
        try:
            source = _logger_name_to_source(record.name)
            level = _LEVELNO_TO_LEVEL.get(record.levelno, "info")
            try:
                message = record.getMessage()
            except Exception:
                message = str(record.msg)
            if record.exc_info:
                message = message + "\n" + "".join(
                    traceback.format_exception(*record.exc_info)
                ).rstrip()
            self._service.log(source, level, message)
        except Exception:
            pass
        finally:
            self._local.busy = False
