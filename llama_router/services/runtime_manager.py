"""llama.cpp runtime binaries — the ★ runtime-manager. Downloads prebuilt
releases from GitHub (pi-test's approach — no compiling, unlike LlamaForge),
imports custom local builds, tracks the active runtime."""
from __future__ import annotations

import logging
import re
import shutil
import sys
import platform
import threading
import time
from pathlib import Path

from llama_router.core.events import EventBus
from llama_router.core.paths import PathManager
from llama_router.core.storage import db_read, db_write
from llama_router.core.utils import (cuda_major_ver, detect_backend,
                                     extract_archive, uid)
from llama_router.schemas import DownloadItem, RuntimeEntry, RuntimeState
from llama_router.services.config_manager import ConfigManager
from llama_router.services.download_manager import DownloadManager

log = logging.getLogger(__name__)


def _find_exe(folder: Path, base_name: str = "llama-server") -> Path | None:
    if not folder.is_dir():
        return None
    for p in sorted(folder.rglob(f"{base_name}*")):
        if p.is_file() and p.name in (base_name, f"{base_name}.exe"):
            return p
    return None


def _detect_backend_from_folder(folder: Path) -> str:
    """Detect GPU backend by scanning for specific ggml libraries."""
    if not folder.is_dir():
        return "cpu"
    has_cuda = has_vulkan = False
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        nl = p.name.lower()
        if nl.startswith(("ggml-cuda.", "ggml-cuda-")):
            has_cuda = True
        elif nl.startswith(("ggml-vulkan.", "ggml-vulkan-")):
            has_vulkan = True
    if has_cuda and has_vulkan:
        return "custom"
    if has_cuda:
        return "cuda"
    if has_vulkan:
        return "vulkan"
    return "cpu"


def asset_matches_os(name: str, machine: str | None = None) -> bool:
    """True when a release asset targets this OS and CPU architecture."""
    nl = name.lower()
    if sys.platform == "win32":
        os_match = "win" in nl
    elif sys.platform == "darwin":
        os_match = "macos" in nl
    else:
        os_match = "linux" in nl or "ubuntu" in nl
    if not os_match:
        return False
    arch = (machine or platform.machine()).lower()
    want_arm = arch in ("arm64", "aarch64")
    has_arm = any(x in nl for x in ("arm64", "aarch64"))
    has_x64 = any(x in nl for x in ("x64", "x86_64", "amd64"))
    if has_arm or has_x64:
        return has_arm if want_arm else has_x64
    return True


class RuntimeManager:
    def __init__(self, paths: PathManager, config: ConfigManager,
                 downloads: DownloadManager, events: EventBus) -> None:
        self._paths = paths
        self._config = config
        self._downloads = downloads
        self._events = events
        self._runtimes: dict[str, RuntimeEntry] = {}
        self._install_locks: dict[str, threading.Lock] = {}
        downloads.set_completion_handler(self._complete_download)

    # ── Public API ───────────────────────────────────────────────────────────

    def load(self) -> None:
        rows = db_read(self._paths.db_path, "runtimes", default=[])
        self._runtimes = {}
        for row in rows:
            try:
                r = RuntimeEntry.from_dict(row)
                self._runtimes[r.id] = r
            except Exception:
                log.warning("Skipping invalid runtime entry")
        self._validate_all()
        log.debug("Loaded %d runtimes", len(self._runtimes))

    def list(self) -> list[RuntimeEntry]:
        return sorted(self._runtimes.values(), key=lambda r: r.version,
                      reverse=True)

    def get(self, runtime_id: str) -> RuntimeEntry | None:
        return self._runtimes.get(runtime_id)

    def get_active(self) -> RuntimeEntry | None:
        active_id = self._config.get().active_runtime_id
        if active_id and active_id in self._runtimes:
            rt = self._runtimes[active_id]
            if rt.state in (RuntimeState.INSTALLED, RuntimeState.CUSTOM):
                return rt
        # Fall back to first valid
        for rt in self.list():
            if rt.state in (RuntimeState.INSTALLED, RuntimeState.CUSTOM):
                return rt
        return None

    def set_active(self, runtime_id: str) -> RuntimeEntry:
        rt = self._require(runtime_id)
        self._config.update({"active_runtime_id": runtime_id})
        self._events.publish("runtime_activated", rt.to_dict())
        return rt

    def import_local(self, folder: str, name: str) -> RuntimeEntry:
        """Register an already-installed runtime from *folder*."""
        p = Path(folder).resolve()
        exe = _find_exe(p)
        if not exe:
            raise FileNotFoundError(f"llama-server executable not found in {p}")

        rt = RuntimeEntry(
            id=uid("runtime"),
            name=name,
            version=name,
            backend=_detect_backend_from_folder(p),
            path=str(p),
            source="custom",
            installed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            state=RuntimeState.CUSTOM,
        )
        self._runtimes[rt.id] = rt
        self._persist()
        self._events.publish("runtime_added", rt.to_dict())
        return rt

    def install_asset(self, tag: str, asset: dict,
                      all_assets: list[dict] | None = None) -> DownloadItem:
        """Download *asset* from release *tag*, extract into runtime/, register.

        On Windows CUDA builds, the matching cudart-* asset from the same
        release is queued into the same folder — the DLLs land next to
        llama-server.exe so users without the CUDA toolkit can still run.
        """
        backend = detect_backend(asset["name"])
        major = cuda_major_ver(asset["name"])
        folder_name = f"{tag}-cuda-{major}" if backend == "cuda" and major \
            else f"{tag}-{backend}"
        target = self._paths.runtime_dir / folder_name

        components = [asset]
        cudart = None
        if backend == "cuda" and sys.platform == "win32" and all_assets:
            cudart = next(
                (a for a in all_assets
                 if a["name"].lower().startswith("cudart")
                 and asset_matches_os(a["name"])
                 and cuda_major_ver(a["name"]) == major), None)
            if cudart:
                components.append(cudart)
        common = {"action": "install_runtime", "target": str(target),
                  "tag": tag, "backend": backend,
                  "expected": len(components)}
        item = self._downloads.start_runtime(
            asset["url"], asset["name"], str(self._paths.runtime_dir),
            meta={**common, "component": "runtime"})

        if cudart:
            self._downloads.start_runtime(
                cudart["url"], cudart["name"], str(self._paths.runtime_dir),
                meta={**common, "component": "cudart"})
        return item

    def _complete_download(self, item: DownloadItem) -> None:
        """Finish a runtime asset, including downloads resumed after restart."""
        meta = item.meta or {}
        if meta.get("action") != "install_runtime":
            return
        target = Path(meta["target"])
        target.resolve().relative_to(self._paths.runtime_dir.resolve())
        # Downloads queued by versions before grouped installs used a
        # register boolean. Finish those safely without assuming markers.
        if "register" in meta:
            lock = self._install_locks.setdefault(str(target.resolve()),
                                                  threading.Lock())
            with lock:
                extract_archive(Path(item.destination), target)
                if meta.get("register"):
                    self.register_extracted_folder(
                        target, str(meta["tag"]), str(meta["backend"]))
            return
        staging = target.with_name(target.name + ".installing")
        lock = self._install_locks.setdefault(str(target.resolve()),
                                              threading.Lock())
        with lock:
            extract_archive(Path(item.destination), staging)
            marker = staging / (".component-" + str(meta.get("component", "runtime")))
            marker.touch()
            expected = int(meta.get("expected", 1))
            if len(list(staging.glob(".component-*"))) < expected:
                return
            for p in staging.glob(".component-*"):
                p.unlink(missing_ok=True)
            final_target = target
            suffix = 2
            while final_target.exists():
                final_target = target.with_name(f"{target.name}-{suffix}")
                suffix += 1
            staging.replace(final_target)
            self.register_extracted_folder(
                final_target, str(meta["tag"]), str(meta["backend"]))

    def register_extracted_folder(self, folder: Path, version: str,
                                  backend: str) -> RuntimeEntry:
        """Register an already-extracted runtime folder. Idempotent."""
        folder_str = str(folder.resolve())
        for rt in self._runtimes.values():
            if str(Path(rt.path).resolve()) == folder_str:
                log.debug("Runtime already registered at %s", folder_str)
                return rt

        exe = _find_exe(folder)
        state = RuntimeState.INSTALLED if exe else RuntimeState.INVALID

        m = re.search(r"-cuda-(\d+)$", folder.name)
        display_name = (f"llama.cpp {version} (cuda {m.group(1)})" if m
                        else f"llama.cpp {version} ({backend})")

        rt = RuntimeEntry(
            id=uid("runtime"),
            name=display_name,
            version=version,
            backend=backend,
            path=folder_str,
            source="github",
            installed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            state=state,
        )
        self._runtimes[rt.id] = rt
        self._persist()
        self._events.publish("runtime_added", rt.to_dict())
        log.info("Runtime registered: %s at %s", rt.name, folder_str)
        return rt

    def delete(self, runtime_id: str) -> None:
        rt = self._require(runtime_id)
        path = Path(rt.path)
        del self._runtimes[runtime_id]
        if self._config.get().active_runtime_id == runtime_id:
            self._config.update({"active_runtime_id": None})
        self._persist()
        self._events.publish("runtime_deleted", {"id": runtime_id})
        # Delete files for downloaded runtimes; skip custom (user's own build)
        if rt.source == "github" and path.is_dir():
            try:
                path.resolve().relative_to(self._paths.runtime_dir.resolve())
                shutil.rmtree(path)
                log.info("Deleted runtime files at %s", path)
            except ValueError:
                log.warning("Skipped deletion: %s is outside runtime_dir", path)

    def get_executable(self) -> Path | None:
        rt = self.get_active()
        if not rt:
            return None
        return _find_exe(Path(rt.path))

    def fetch_releases(self, limit: int = 8) -> list[dict]:
        """Blocking GitHub fetch — call from a worker thread. Assets are
        pre-filtered for the current OS. The full unfiltered asset list is
        kept as ``all_assets`` so the installer can pick the matching
        ``cudart-*`` from the same release."""
        releases = self._downloads.gh_releases(limit)
        for rel in releases:
            rel["all_assets"] = rel["assets"]  # full list, kept for cudart lookup
            rel["assets"] = [a for a in rel["assets"]
                             if asset_matches_os(a["name"])
                             and not a["name"].lower().startswith("cudart")]
        return [r for r in releases if r["assets"]]

    # ── Internal ─────────────────────────────────────────────────────────────

    def _validate_all(self) -> None:
        for rt in self._runtimes.values():
            if rt.state == RuntimeState.INSTALLED:
                if not _find_exe(Path(rt.path)):
                    rt.state = RuntimeState.INVALID

    def _require(self, runtime_id: str) -> RuntimeEntry:
        rt = self._runtimes.get(runtime_id)
        if not rt:
            raise KeyError(f"Runtime not found: {runtime_id}")
        return rt

    def _persist(self) -> None:
        rows = [r.to_dict() for r in self._runtimes.values()]
        db_write(self._paths.db_path, "runtimes", rows)
