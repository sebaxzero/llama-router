"""SQLite key-value store for structured data, atomic text writes for files."""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


class InstanceGuard:
    """Cross-platform exclusive lock held for the lifetime of one app."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0, os.SEEK_END)
                if self._file.tell() == 0:
                    self._file.write(b"0")
                    self._file.flush()
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, IOError):
            self._file.close()
            self._file = None
            return False

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("another instance is already using this data folder")
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


@contextlib.contextmanager
def _connect(path: Path):
    # sqlite3's own context manager commits but never closes — on Windows that
    # keeps the file locked until GC, so close explicitly.
    conn = sqlite3.connect(str(path))
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(path: Path) -> None:
    """Create DB and schema. Safe to call repeatedly (idempotent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kv "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )


def db_read(db_path: Path, key: str, default: Any = None) -> Any:
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM kv WHERE key = ?", (key,)
            ).fetchone()
            return json.loads(row[0]) if row else default
    except Exception:
        return default


def db_write(db_path: Path, key: str, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
            (key, payload),
        )


def write_text(path: Path, text: str) -> None:
    """Atomically write plain text to *path* (used for .ini files)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
