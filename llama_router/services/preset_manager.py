"""Lifecycle and persistence for the user-owned models-preset.ini."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from llama_router.core.events import EventBus
from llama_router.core.paths import PathManager
from llama_router.core.storage import atomic_write_bytes, backup_bytes
from llama_router.preset import (PresetDiagnostic, PresetDocument,
                                 decode_preset_bytes, fingerprint_bytes,
                                 merge_editor_text,
                                 normalize_editor_text)

@dataclass(frozen=True)
class PresetSnapshot:
    path: Path
    raw_bytes: bytes
    raw_text: str
    text: str
    fingerprint: str
    bom: bool
    readable: bool
    writable: bool
    document: PresetDocument
    loaded_at: float
    origin: str = "load"

    @property
    def usable_routes(self):
        return self.document.usable_routes

    @property
    def route_count(self) -> int:
        return len(self.document.routes)

    @property
    def unique_model_count(self) -> int:
        return self.document.unique_sources

    @property
    def errors(self):
        return self.document.errors

    @property
    def warnings(self):
        return self.document.warnings


class PresetConflictError(RuntimeError):
    pass


class PresetValidationError(ValueError):
    def __init__(self, document: PresetDocument):
        self.document = document
        messages = "; ".join(d.message for d in document.errors[:3])
        super().__init__(messages or "Preset validation failed")


class PresetManager:
    """Own the saved preset snapshot; never regenerate it from app state."""

    def __init__(self, paths: PathManager, models, events: EventBus,
                 runtimes=None, catalog=None) -> None:
        self._paths = paths
        self._models = models
        self._events = events
        self._runtimes = runtimes
        self._catalog = catalog
        self._snapshot: PresetSnapshot | None = None
        self._signature: tuple[int, int] | None = None
        self._last_poll = 0.0

    @property
    def path(self) -> Path:
        return self._paths.preset_ini

    @property
    def snapshot(self) -> PresetSnapshot:
        return self._snapshot or self.load()

    def set_catalog(self, catalog) -> None:
        self._catalog = catalog
        if self._snapshot is not None:
            self.load(force=True, origin="catalog")

    def _runtime_cwd(self) -> Path | None:
        if self._runtimes is None:
            return None
        try:
            exe = self._runtimes.get_executable()
            return exe.parent if exe else None
        except Exception:
            return None

    def _publish(self, snap: PresetSnapshot) -> None:
        self._events.publish("preset_changed", {
            "origin": snap.origin,
            "fingerprint": snap.fingerprint,
            "route_count": snap.route_count,
            "unique_model_count": snap.unique_model_count,
            "error_count": len(snap.errors),
            "warning_count": len(snap.warnings),
            "readable": snap.readable,
            "writable": snap.writable,
        })

    def _read_snapshot(self, *, origin: str) -> PresetSnapshot:
        path = self.path
        raw_bytes = b""
        try:
            raw_bytes = path.read_bytes() if path.exists() else b""
            writable = not path.exists() or os.access(path, os.W_OK)
            raw_text, bom = decode_preset_bytes(raw_bytes)
            document = PresetDocument.parse(
                raw_text, runtime_cwd=self._runtime_cwd(),
                catalog=self._catalog, registry=self._models.list())
            if not writable:
                document.diagnostics = tuple(document.diagnostics) + (
                    PresetDiagnostic(
                        "not_writable", "error",
                        "models-preset.ini is not writable.", 0, 0,
                        True, False),)
            readable = True
        except (OSError, UnicodeDecodeError) as exc:
            # Keep the exact bytes in the snapshot even when decoding fails;
            # this makes the failure non-destructive and allows an explicit
            # restore/overwrite decision without pretending the file was empty.
            if not raw_bytes:
                try:
                    raw_bytes = path.read_bytes()
                except OSError:
                    raw_bytes = b""
            raw_text = ""
            bom = False
            writable = False
            readable = False
            document = PresetDocument.parse("")
            document.diagnostics = (PresetDiagnostic(
                "read_error", "error", f"Could not read preset: {exc}",
                0, 0, True, True),)
        snap = PresetSnapshot(
            path=path,
            raw_bytes=raw_bytes,
            raw_text=raw_text,
            text=normalize_editor_text(raw_text),
            fingerprint=fingerprint_bytes(raw_bytes),
            bom=bom,
            readable=readable,
            writable=writable,
            document=document,
            loaded_at=time.time(),
            origin=origin,
        )
        try:
            st = path.stat()
            self._signature = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._signature = None
        self._snapshot = snap
        return snap

    def load(self, *, force: bool = False, origin: str = "load",
             publish: bool = True) -> PresetSnapshot:
        if self._snapshot is None or force:
            snap = self._read_snapshot(origin=origin)
            if publish:
                self._publish(snap)
            return snap
        return self._snapshot

    def poll_external(self, *, force: bool = False) -> PresetSnapshot | None:
        now = time.monotonic()
        if not force and now - self._last_poll < 0.75:
            return None
        self._last_poll = now
        path = self.path
        try:
            st = path.stat()
            signature = (st.st_mtime_ns, st.st_size)
        except OSError:
            signature = None
        if not force and signature == self._signature:
            return None
        previous = self._snapshot.fingerprint if self._snapshot else None
        snap = self._read_snapshot(origin="external")
        if previous == snap.fingerprint and not force:
            return None
        self._publish(snap)
        return snap

    def save(self, draft_text: str, expected_fingerprint: str,
             *, base_text: str | None = None,
             overwrite_external: bool = False) -> PresetSnapshot:
        current = self._read_snapshot(origin="save-check")
        if (not overwrite_external and expected_fingerprint != current.fingerprint):
            raise PresetConflictError(
                "models-preset.ini changed outside the editor")
        if not current.writable:
            raise PermissionError("models-preset.ini is not writable")
        if not current.readable and current.raw_bytes:
            raise OSError("Current preset cannot be read safely")
        base_view = normalize_editor_text(base_text if base_text is not None
                                          else current.raw_text)
        merged = merge_editor_text(current.raw_text, base_view,
                                   normalize_editor_text(draft_text))
        document = PresetDocument.parse(
            merged, runtime_cwd=self._runtime_cwd(), catalog=self._catalog,
            registry=self._models.list())
        if not document.can_save:
            raise PresetValidationError(document)
        old_bytes = current.raw_bytes
        if old_bytes:
            backup_bytes(self.path, self.path.with_name(
                self.path.name + ".bak"))
        payload = (b"\xef\xbb\xbf" if current.bom else b"") + merged.encode("utf-8")
        atomic_write_bytes(self.path, payload, mode_from=self.path if self.path.exists() else None)
        snap = self._read_snapshot(origin="save")
        self._publish(snap)
        return snap

    def restore_backup(self) -> PresetSnapshot:
        backup = self.path.with_name(self.path.name + ".bak")
        if not backup.exists():
            raise FileNotFoundError(str(backup))
        current = self._read_snapshot(origin="restore-check")
        if current.raw_bytes:
            backup_bytes(self.path, self.path.with_name(
                self.path.name + ".before-restore.bak"))
        atomic_write_bytes(self.path, backup.read_bytes(), mode_from=backup)
        snap = self._read_snapshot(origin="restore")
        self._publish(snap)
        return snap
