"""Threaded download manager — urllib + Range resume, retry with backoff,
stall detection. Port of pi-test's asyncio DownloadManager to worker threads.

Workers publish progress through the EventBus (thread-safe queue), so the UI
never needs locks. An optional on_complete callback runs on the worker thread
after a successful download — used by RuntimeManager to extract archives
without blocking the UI.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
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

# The public feed is not tied to GitHub REST's shared anonymous API quota.
GH_RELEASES = "https://github.com/ggml-org/llama.cpp/releases.atom"

_UA = {"User-Agent": "llama-router"}


class _StallError(Exception):
    """Connection alive but throughput stayed below _STALL_MIN_BPS for a full
    _STALL_WINDOW — typical of a degraded CDN connection that never drops."""


class _Shutdown(Exception):
    """Internal signal used to pause a worker during application shutdown."""


def _http_text(url: str, headers: dict | None = None, timeout: float = 15) -> str:
    req = urllib.request.Request(url, headers={**_UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


class DownloadManager:
    def __init__(self, paths: PathManager, config: ConfigManager,
                 events: EventBus) -> None:
        self._paths = paths
        self._events = events
        self._items: dict[str, DownloadItem] = {}
        self._completion_handler: Callable[[DownloadItem], None] | None = None
        self._limit = max(1, config.get().max_concurrent_downloads)
        self._active = 0
        self._limit_cv = threading.Condition()
        self._threads: set[threading.Thread] = set()
        self._close_lock = threading.Lock()
        self._persist_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._responses: set = set()
        self._response_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._closed = False
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
            handler = self._completion_handler
            destination = Path(item.destination)
            if destination.is_file():
                item.total_bytes = max(item.total_bytes, destination.stat().st_size)
                item.downloaded_bytes = item.total_bytes
                item.speed_bps = 0.0
                item.error = ""
                if handler is None:
                    # There is no durable post-processing step for this kind;
                    # the archive itself is the complete result.
                    self._items[item.id] = item
                    self._complete(item)
                else:
                    item.state = DownloadState.QUEUED
                    self._enqueue(item, handler, post_process=True)
                    resumed += 1
                continue
            item.state = DownloadState.QUEUED
            item.downloaded_bytes = 0
            item.speed_bps = 0.0
            item.error = ""
            self._enqueue(item, handler)
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

    def set_completion_handler(self, handler: Callable[[DownloadItem], None]) -> None:
        """Register the durable post-download handler used after a restart."""
        self._completion_handler = handler

    def close(self, timeout: float = 5.0) -> None:
        """Stop workers promptly and leave unfinished items queued for resume."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._shutdown.set()
        self._events.unsubscribe("config_saved", self._on_config_saved)
        with self._limit_cv:
            self._limit_cv.notify_all()
            threads = list(self._threads)
        with self._response_lock:
            responses = list(self._responses)
        for response in responses:
            try:
                response.close()
            except Exception:
                pass

        deadline = time.monotonic() + max(0.0, timeout)
        current = threading.current_thread()
        for thread in threads:
            if thread is current:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)

        with self._limit_cv:
            unfinished = list(self._threads)
        for item in list(self._items.values()):
            if item.state in (DownloadState.QUEUED, DownloadState.DOWNLOADING):
                self._pause(item)
        if unfinished:
            log.warning("%d download worker(s) did not stop before shutdown",
                        len(unfinished))

    # ── GitHub helpers (blocking — call from a worker thread) ────────────────

    def gh_releases(self, limit: int = 10) -> list[dict]:
        try:
            root = ET.fromstring(_http_text(GH_RELEASES, timeout=15))
            atom = "{http://www.w3.org/2005/Atom}"
            result = []
            for entry in root.findall(f"{atom}entry")[:limit]:
                content = entry.findtext(f"{atom}content") or ""
                urls = dict.fromkeys(re.findall(
                    r'href="(https://github\.com/ggml-org/llama\.cpp/'
                    r'releases/download/[^"]+)',
                    content))
                assets = [
                    # ponytail: Atom omits sizes; use HEAD requests if the UI
                    # ever needs exact sizes before a download starts.
                    {"name": url.rsplit("/", 1)[-1], "url": url, "size": 0}
                    for url in urls
                    if url.endswith((".zip", ".tar.gz", ".tgz"))
                ]
                if assets:
                    result.append({
                        "tag": entry.findtext(f"{atom}title") or "",
                        "published": entry.findtext(f"{atom}updated") or "",
                        "assets": assets,
                    })
            return result
        except Exception as e:
            log.warning("GitHub releases fetch failed: %s", e)
            return []

    # ── Internals ────────────────────────────────────────────────────────────

    def _persist(self, *, exclude_id: str | None = None) -> None:
        """Save pending items so an app restart can resume them. Progress bytes
        aren't persisted — resume position comes from the .part file."""
        with self._persist_lock:
            self._persist_locked(exclude_id=exclude_id)

    def _persist_locked(self, *, exclude_id: str | None = None) -> None:
        pending = [
            i.to_dict() for i in self._items.values()
            if i.id != exclude_id
            and i.state in (DownloadState.QUEUED, DownloadState.DOWNLOADING)
        ]
        try:
            db_write(self._paths.db_path, "downloads", pending)
        except Exception:
            log.debug("Could not persist download queue", exc_info=True)

    def _enqueue(
            self, item: DownloadItem,
            on_complete: Callable[[DownloadItem], None] | None = None,
            *, post_process: bool = False) -> DownloadItem:
        self._items[item.id] = item
        if self._shutdown.is_set():
            self._emit(item)
            return item
        th = threading.Thread(target=self._run,
                              args=(item.id, on_complete, post_process),
                              daemon=True, name=f"dl-{item.id}")
        with self._limit_cv:
            self._threads.add(th)
        self._persist()
        self._emit(item)
        try:
            th.start()
        except Exception:
            with self._limit_cv:
                self._threads.discard(th)
            raise
        return item

    def _run(self, dl_id: str,
             on_complete: Callable[[DownloadItem], None] | None,
             post_process: bool = False) -> None:
        item = None
        acquired = False
        try:
            if not self._acquire_slot():
                return
            acquired = True
            item = self._items.get(dl_id)
            if not item or self._shutdown.is_set():
                if item:
                    self._pause(item)
                return
            item.state = DownloadState.DOWNLOADING
            item.error = ""
            log.info("Download started: %s (%s)", item.name, item.kind)
            self._emit(item)
            try:
                if not post_process:
                    self._download(item)
            except _Shutdown:
                self._pause(item)
                return
            except Exception as e:
                if self._shutdown.is_set():
                    self._pause(item)
                    return
                log.error("Download %s failed: %s", dl_id, e)
                item.state = DownloadState.FAILED
                item.error = str(e)
                self._persist()
                self._emit(item)
                return

            if self._shutdown.is_set():
                self._pause(item)
                return
            handler = on_complete or self._completion_handler
            if handler is not None:
                try:
                    handler(item)
                except Exception as e:
                    if self._shutdown.is_set():
                        self._pause(item)
                        return
                    log.error("Post-download step failed for %s: %s",
                              item.name, e)
                    # The archive is already safe on disk. Keep it queued so
                    # the durable handler can retry after the next launch.
                    item.state = DownloadState.QUEUED
                    item.error = str(e)
                    self._persist()
                    self._emit(item)
                    return
            self._complete(item)
        finally:
            if acquired:
                self._release_slot()
            with self._limit_cv:
                self._threads.discard(threading.current_thread())
                self._limit_cv.notify_all()

    def _on_config_saved(self, data: dict) -> None:
        limit = max(1, int((data or {}).get("max_concurrent_downloads", 1)))
        with self._limit_cv:
            self._limit = limit
            self._limit_cv.notify_all()

    def _acquire_slot(self) -> bool:
        with self._limit_cv:
            while self._active >= self._limit and not self._shutdown.is_set():
                self._limit_cv.wait()
            if self._shutdown.is_set():
                return False
            self._active += 1
            return True

    def _release_slot(self) -> None:
        with self._limit_cv:
            self._active -= 1
            self._limit_cv.notify_all()

    def _pause(self, item: DownloadItem) -> None:
        item.state = DownloadState.QUEUED
        item.speed_bps = 0.0
        item.error = ""
        self._persist()
        self._emit(item)

    def _download(self, item: DownloadItem) -> None:
        _retryable = (TimeoutError, ConnectionError, _StallError,
                      urllib.error.URLError, OSError)
        attempt = 0
        while True:
            if self._shutdown.is_set():
                raise _Shutdown
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
                if self._shutdown.wait(wait):
                    raise _Shutdown

    def _attempt_download(self, item: DownloadItem) -> None:
        self._check_shutdown()
        dest = Path(item.destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = Path(str(dest) + ".part")

        resume_from = part.stat().st_size if part.exists() else 0
        headers: dict[str, str] = dict(_UA)
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"

        req = urllib.request.Request(item.url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            with self._response_lock:
                self._responses.add(resp)
            try:
                self._check_shutdown()
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
                self._check_shutdown()
                with open(part, mode) as f:
                    while True:
                        self._check_shutdown()
                        chunk = resp.read(_CHUNK)
                        if not chunk:
                            break
                        with self._io_lock:
                            self._check_shutdown()
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
            finally:
                with self._response_lock:
                    self._responses.discard(resp)

        self._finalise(item)

    def _check_shutdown(self) -> None:
        if self._shutdown.is_set():
            raise _Shutdown

    def _finalise(self, item: DownloadItem) -> None:
        dest = Path(item.destination)
        part = Path(str(dest) + ".part")
        with self._io_lock:
            self._check_shutdown()
            if part.exists():
                part.replace(dest)
            item.downloaded_bytes = max(item.total_bytes, item.downloaded_bytes)
            item.speed_bps = 0.0
            # Keep the queue record until the durable post-processing handler
            # has succeeded; shutdown can happen in this exact window.
            log.info("Download archive ready: %s", item.name)
            self._persist()
            self._emit(item)

    def _complete(self, item: DownloadItem) -> None:
        with self._persist_lock:
            item.downloaded_bytes = max(item.total_bytes, item.downloaded_bytes)
            item.speed_bps = 0.0
            item.error = ""
            # Keep the item out of the durable snapshot, then expose
            # COMPLETED while holding the same lock so another worker cannot
            # rewrite it as pending after cleanup.
            self._persist_locked(exclude_id=item.id)
            item.state = DownloadState.COMPLETED
        log.info("Download completed: %s", item.name)
        self._emit(item)
        self._events.publish("download_complete", item.to_dict())

    def _emit(self, item: DownloadItem) -> None:
        self._events.publish("download_progress", item.to_dict())
