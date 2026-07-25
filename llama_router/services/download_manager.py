"""Threaded download manager — urllib + Range resume, retry with backoff,
stall detection. Port of pi-test's asyncio DownloadManager to worker threads.

Workers publish progress through the EventBus (thread-safe queue), so the UI
never needs locks. An optional on_complete callback runs on the worker thread
after a successful download — used by RuntimeManager to extract archives
without blocking the UI.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from llama_router.core.events import EventBus
from llama_router.core.paths import PathManager
from llama_router.core.storage import db_read, db_write
from llama_router.core.utils import uid
from llama_router.schemas import DownloadItem, DownloadState
from llama_router.services.config_manager import ConfigManager

log = logging.getLogger(__name__)

_CHUNK = 256 * 1024        # 256 KB read chunks
_PROGRESS_INTERVAL = 0.5   # seconds between progress events
_MAX_RETRIES = 3
_RETRY_BASE = 2.0          # seconds; wait = RETRY_BASE ** attempt
_STALL_WINDOW = 30.0       # seconds over which throughput is averaged
_STALL_MIN_BPS = 50 * 1024   # below this average the connection is stalled

GH_API = "https://api.github.com/repos/ggerganov/llama.cpp/releases"

_UA = {"User-Agent": "llama-router"}


class _StallError(Exception):
    """Connection alive but throughput stayed below _STALL_MIN_BPS for a full
    _STALL_WINDOW — typical of a degraded CDN connection that never drops."""


def _http_json(url: str, headers: dict | None = None, timeout: float = 15):
    req = urllib.request.Request(url, headers={**_UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class DownloadManager:
    def __init__(self, paths: PathManager, config: ConfigManager,
                 events: EventBus) -> None:
        self._paths = paths
        self._config = config
        self._events = events
        self._items: dict[str, DownloadItem] = {}
        self._completion_handlers: dict[str, Callable[[DownloadItem], None]] = {}
        self._limit = max(1, config.get().max_concurrent_downloads)
        self._active = 0
        self._limit_cv = threading.Condition()
        events.subscribe("config_saved", self._on_config_saved)

    # ── Public API ───────────────────────────────────────────────────────────

    def load(self) -> None:
        """Re-queue downloads interrupted by an app restart (resume via .part)."""
        saved = db_read(self._paths.db_path, "downloads", default=[])
        resumed = 0
        for d in saved:
            try:
                item = DownloadItem.from_dict(d)
            except Exception:
                continue
            if Path(item.destination).exists():
                continue  # finished after the last persist
            item.state = DownloadState.QUEUED
            item.downloaded_bytes = 0
            item.speed_bps = 0.0
            self._enqueue(item, self._completion_handlers.get(item.kind))
            resumed += 1
        if resumed:
            log.info("Resumed %d interrupted download(s)", resumed)
        elif saved:
            self._persist()  # clear stale entries

    def list(self) -> list[DownloadItem]:
        return list(self._items.values())

    def get(self, dl_id: str) -> DownloadItem | None:
        return self._items.get(dl_id)

    def start_runtime(self, url: str, filename: str, destination: str,
                      on_complete: Callable[[DownloadItem], None] | None = None,
                      meta: dict | None = None,
                      ) -> DownloadItem:
        """Queue a GitHub release asset download."""
        dest = str(Path(destination) / filename)
        item = DownloadItem(id=uid("dl"), kind="runtime", name=filename,
                            url=url, destination=dest, meta=meta or {})
        return self._enqueue(item, on_complete)

    def set_completion_handler(
            self, kind: str, handler: Callable[[DownloadItem], None]) -> None:
        """Register the durable post-download handler used after a restart."""
        self._completion_handlers[kind] = handler

    # ── GitHub helpers (blocking — call from a worker thread) ────────────────

    def gh_releases(self, limit: int = 10) -> list[dict]:
        try:
            releases = _http_json(f"{GH_API}?per_page={limit}", timeout=15)
        except Exception as e:
            log.warning("GitHub releases fetch failed: %s", e)
            return []
        result = []
        for rel in releases:
            assets = [
                {"name": a["name"], "url": a["browser_download_url"],
                 "size": a["size"]}
                for a in rel.get("assets", [])
                if a["name"].endswith((".zip", ".tar.gz", ".tgz"))
            ]
            if assets:
                result.append({
                    "tag": rel["tag_name"],
                    "published": rel["published_at"],
                    "assets": assets,
                })
        return result

    # ── Internals ────────────────────────────────────────────────────────────

    def _persist(self) -> None:
        """Save pending items so an app restart can resume them. Progress bytes
        aren't persisted — resume position comes from the .part file."""
        pending = [
            i.to_dict() for i in self._items.values()
            if i.state in (DownloadState.QUEUED, DownloadState.DOWNLOADING)
        ]
        try:
            db_write(self._paths.db_path, "downloads", pending)
        except Exception:
            log.debug("Could not persist download queue", exc_info=True)

    def _enqueue(self, item: DownloadItem,
                 on_complete: Callable[[DownloadItem], None] | None = None
                 ) -> DownloadItem:
        self._items[item.id] = item
        th = threading.Thread(target=self._run, args=(item.id, on_complete),
                              daemon=True, name=f"dl-{item.id}")
        self._persist()
        self._emit(item)
        th.start()
        return item

    def _run(self, dl_id: str,
             on_complete: Callable[[DownloadItem], None] | None) -> None:
        self._acquire_slot()
        try:
            item = self._items.get(dl_id)
            if not item:
                return
            item.state = DownloadState.DOWNLOADING
            log.info("Download started: %s (%s)", item.name, item.kind)
            self._emit(item)
            try:
                self._download(item)
            except Exception as e:
                log.error("Download %s failed: %s", dl_id, e)
                item.state = DownloadState.FAILED
                item.error = str(e)
                self._persist()
                self._emit(item)
                return
        finally:
            self._release_slot()

        if on_complete is not None:
            try:
                on_complete(item)
            except Exception as e:
                log.error("Post-download step failed for %s: %s", item.name, e)
                item.state = DownloadState.FAILED
                item.error = str(e)
                self._emit(item)
                return
        self._events.publish("download_complete", item.to_dict())

    def _on_config_saved(self, data: dict) -> None:
        limit = max(1, int((data or {}).get("max_concurrent_downloads", 1)))
        with self._limit_cv:
            self._limit = limit
            self._limit_cv.notify_all()

    def _acquire_slot(self) -> None:
        with self._limit_cv:
            while self._active >= self._limit:
                self._limit_cv.wait()
            self._active += 1

    def _release_slot(self) -> None:
        with self._limit_cv:
            self._active -= 1
            self._limit_cv.notify_all()

    def _download(self, item: DownloadItem) -> None:
        _retryable = (TimeoutError, ConnectionError, _StallError,
                      urllib.error.URLError, OSError)
        attempt = 0
        while True:
            before = item.downloaded_bytes
            try:
                self._attempt_download(item)
                return
            except urllib.error.HTTPError as e:
                if e.code == 416:
                    # .part already holds the full file — finalise
                    self._finalise(item)
                    return
                raise
            except _retryable as e:
                if item.downloaded_bytes > before:
                    attempt = 0  # progress made — reconnects don't burn budget
                if attempt >= _MAX_RETRIES:
                    raise
                attempt += 1
                wait = _RETRY_BASE ** attempt
                log.warning("Download %s transient error (%s), retry %d/%d in %.0fs",
                            item.id, e, attempt, _MAX_RETRIES, wait)
                item.speed_bps = 0.0
                self._emit(item)
                time.sleep(wait)

    def _attempt_download(self, item: DownloadItem) -> None:
        dest = Path(item.destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = Path(str(dest) + ".part")

        resume_from = part.stat().st_size if part.exists() else 0
        headers: dict[str, str] = dict(_UA)
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"

        req = urllib.request.Request(item.url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_length = int(resp.headers.get("Content-Length") or 0)
            content_range = resp.headers.get("Content-Range")
            if content_range:
                total = int(content_range.split("/")[-1])
            else:
                total = resume_from + content_length
                if resume_from and resp.status == 200:
                    # Server ignored Range — starting over
                    resume_from = 0
                    total = content_length
            item.total_bytes = total

            downloaded = resume_from
            last_ts = time.monotonic()
            last_bytes = downloaded
            window_ts = last_ts
            window_bytes = downloaded

            mode = "ab" if resume_from else "wb"
            with open(part, mode) as f:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    item.downloaded_bytes = downloaded

                    now = time.monotonic()
                    if now - window_ts >= _STALL_WINDOW:
                        if (downloaded - window_bytes) / (now - window_ts) < _STALL_MIN_BPS:
                            raise _StallError(
                                f"below {_STALL_MIN_BPS // 1024} KB/s "
                                f"for {int(_STALL_WINDOW)}s")
                        window_ts = now
                        window_bytes = downloaded
                    if now - last_ts >= _PROGRESS_INTERVAL:
                        item.speed_bps = (downloaded - last_bytes) / (now - last_ts)
                        last_ts = now
                        last_bytes = downloaded
                        self._emit(item)

        self._finalise(item)

    def _finalise(self, item: DownloadItem) -> None:
        dest = Path(item.destination)
        part = Path(str(dest) + ".part")
        if part.exists():
            part.replace(dest)
        item.state = DownloadState.COMPLETED
        item.downloaded_bytes = max(item.total_bytes, item.downloaded_bytes)
        item.speed_bps = 0.0
        log.info("Download completed: %s", item.name)
        self._persist()
        self._emit(item)

    def _emit(self, item: DownloadItem) -> None:
        self._events.publish("download_progress", item.to_dict())
