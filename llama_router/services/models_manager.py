"""GGUF model registry — the ★ models-manager. Sync port of pi-test's
ModelManager: folder scans classify files via the GGUF header so mmproj
projectors and draft models never register as standalone models."""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from llama_router.core.events import EventBus
from llama_router.core.gguf import quant_name, read_gguf_info
from llama_router.core.paths import PathManager
from llama_router.core.storage import db_read, db_write
from llama_router.core.utils import uid
from llama_router.schemas import ModelEntry, ModelState
from llama_router.services.config_manager import ConfigManager

log = logging.getLogger(__name__)


# Filename fallback for draft/speculative companion models when the GGUF
# header can't be read (e.g. older files): needs a separator around the
# marker so names like "minidraft" don't match.
_DRAFT_NAME_RE = re.compile(r"(^|[-_.])(mtp|draft|eagle)([-_.]|$)", re.IGNORECASE)


def _classify(p: Path, info: dict | None) -> str:
    """Classify a GGUF file: 'model', 'mmproj', or 'draft'.

    mmproj projectors and MTP/EAGLE draft models (speculative decoding
    "assistant" architectures) are companions of a main model — they must
    never be registered as standalone models.
    """
    if "mmproj" in p.name.lower():
        return "mmproj"
    info = info or {}
    if info.get("type") == "mmproj":
        return "mmproj"
    if str(info.get("architecture", "")).endswith("-assistant"):
        return "draft"
    if _DRAFT_NAME_RE.search(p.stem):
        return "draft"
    return "model"


def _gguf_kind(p: Path) -> str:
    return _classify(p, read_gguf_info(p))


# Quant label in a filename, e.g. Q5_K_XL, IQ2_M, TQ1_0, BF16. Preferred over
# the header's file_type: dynamic quants (unsloth UD) only approximate it there.
_QUANT_NAME_RE = re.compile(
    r"(?:^|[-_.])((?:I?Q|TQ)\d[A-Z0-9_]*|BF16|F16|F32|MXFP4)(?=$|[-_.])",
    re.IGNORECASE)


def _meta_from_info(info: dict | None, stem: str = "") -> dict:
    """Distil header KVs into the display metadata stored on a ModelEntry."""
    info = info or {}
    meta: dict = {}
    if info.get("architecture"):
        meta["arch"] = info["architecture"]
    match = _QUANT_NAME_RE.search(stem) if stem else None
    if match:
        meta["quant"] = match.group(1).upper()
    else:
        file_type = info.get("file_type")
        if isinstance(file_type, int):
            q = quant_name(file_type)
            if q:
                meta["quant"] = q
    if info.get("size_label"):
        meta["params"] = info["size_label"]
    if info.get("context_length"):
        meta["ctx"] = info["context_length"]
    return meta


class ModelsManager:
    def __init__(self, paths: PathManager, config: ConfigManager,
                 events: EventBus) -> None:
        self._paths = paths
        self._config = config
        self._events = events
        self._models: dict[str, ModelEntry] = {}  # id → entry

    # ── Public API ───────────────────────────────────────────────────────────

    def load(self) -> None:
        rows = db_read(self._paths.db_path, "models", default=[])
        self._models = {}
        for row in rows:
            try:
                m = ModelEntry.from_dict(row)
                self._models[m.id] = m
            except Exception:
                log.warning("Skipping invalid model entry: %s", row)
        log.debug("Loaded %d models from registry", len(self._models))

    def scan(self) -> dict:
        """Scan all configured folders, merge with registry, persist.

        Blocking (reads GGUF headers) — call from a worker thread; results
        arrive via the 'models_scanned' event.
        """
        cfg = self._config.get()
        folders = list({self._paths.models_dir,
                        *[Path(f) for f in cfg.model_folders]})

        found_paths: dict[str, tuple[int, dict]] = {}  # path → (size, meta)
        for folder in folders:
            if not folder.is_dir():
                continue
            for p in folder.rglob("*.gguf"):
                info = read_gguf_info(p)
                if _classify(p, info) == "model":
                    found_paths[str(p.resolve())] = (
                        p.stat().st_size, _meta_from_info(info, p.stem))

        # Merge with existing registry
        existing_by_path = {m.path: m for m in self._models.values()}
        new_count = 0

        for path_str, (size, meta) in found_paths.items():
            if path_str in existing_by_path:
                existing_by_path[path_str].state = ModelState.VALID
                existing_by_path[path_str].size = size
                existing_by_path[path_str].meta = meta
            else:
                m = ModelEntry(
                    id=uid("model"),
                    name=Path(path_str).stem,
                    path=path_str,
                    size=size,
                    state=ModelState.VALID,
                    added_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                    meta=meta,
                )
                self._models[m.id] = m
                new_count += 1

        # Prune companion files (mmproj/draft) auto-added by older scans —
        # their file still exists, so MISSING would be misleading.
        stale = [
            mid for mid, m in self._models.items()
            if m.path not in found_paths
            and Path(m.path).is_file()
            and _gguf_kind(Path(m.path)) != "model"
        ]
        for mid in stale:
            log.info("Dropping companion file from registry: %s",
                     self._models[mid].path)
            del self._models[mid]

        # Mark missing
        for m in self._models.values():
            if m.path not in found_paths:
                m.state = ModelState.MISSING

        self._persist()
        self._events.publish("models_scanned", {
            "total": len(self._models),
            "new": new_count,
        })
        log.info("Model scan: %d total, %d new", len(self._models), new_count)
        return {"total": len(self._models), "new": new_count}

    def list(self) -> list[ModelEntry]:
        return sorted(self._models.values(), key=lambda m: m.name.lower())

    def get(self, model_id: str) -> ModelEntry | None:
        return self._models.get(model_id)

    def set_enabled(self, model_id: str, enabled: bool) -> ModelEntry:
        m = self._require(model_id)
        m.enabled = enabled
        self._persist()
        self._events.publish("model_updated", m.to_dict())
        return m

    def set_enabled_all(self, enabled: bool) -> int:
        """Enable or disable every registered model in one persist."""
        changed = 0
        for m in self._models.values():
            if m.enabled != enabled:
                m.enabled = enabled
                changed += 1
        if changed:
            self._persist()
            self._events.publish("models_scanned", {
                "total": len(self._models), "new": 0,
            })
        return changed

    def remove(self, model_id: str) -> None:
        """Drop the registry entry. Never touches the .gguf file on disk."""
        self._require(model_id)
        del self._models[model_id]
        self._persist()
        self._events.publish("model_removed", {"id": model_id})

    def detect_mmproj(self, model_id: str) -> dict:
        """Auto-detect an MMProj file for *model_id*.

        Conditions: exactly one main-model GGUF and exactly one mmproj GGUF
        in the model's folder. Returns {"path": str|None, "ambiguous": bool}.
        """
        m = self._models.get(model_id)
        if not m:
            return {"path": None, "ambiguous": False}
        folder = Path(m.path).parent
        if not folder.is_dir():
            return {"path": None, "ambiguous": False}
        kinds = {f: _gguf_kind(f) for f in folder.glob("*.gguf")}
        model_ggufs = [f for f, k in kinds.items() if k == "model"]
        mmproj_ggufs = [f for f, k in kinds.items() if k == "mmproj"]
        if len(mmproj_ggufs) == 1 and len(model_ggufs) == 1:
            return {"path": str(mmproj_ggufs[0].resolve()), "ambiguous": False}
        ambiguous = len(mmproj_ggufs) > 1 or len(model_ggufs) > 1
        return {"path": None, "ambiguous": ambiguous}

    def detect_draft(self, model_id: str) -> dict:
        """Auto-detect a speculative draft model for *model_id*.

        Only companions whose GGUF architecture ends in "-assistant" qualify —
        filename-only draft matches are not trusted enough to auto-load.
        """
        m = self._models.get(model_id)
        if not m:
            return {"path": None, "ambiguous": False}
        folder = Path(m.path).parent
        if not folder.is_dir():
            return {"path": None, "ambiguous": False}
        model_ggufs, drafts = [], []
        for f in folder.glob("*.gguf"):
            kind = _gguf_kind(f)
            if kind == "model":
                model_ggufs.append(f)
            elif kind == "draft" and (read_gguf_info(f) or {}).get(
                    "architecture", "").endswith("-assistant"):
                drafts.append(f)
        if len(drafts) == 1 and len(model_ggufs) == 1:
            return {"path": str(drafts[0].resolve()), "ambiguous": False}
        return {"path": None, "ambiguous": len(drafts) > 1 or len(model_ggufs) > 1}

    # ── Folder management ────────────────────────────────────────────────────

    def add_folder(self, folder: str) -> list[str]:
        p = str(Path(folder).resolve())
        cfg = self._config.get()
        if p not in cfg.model_folders:
            self._config.update({"model_folders": cfg.model_folders + [p]})
        return self._config.get().model_folders

    def remove_folder(self, folder: str) -> list[str]:
        p = str(Path(folder).resolve())
        cfg = self._config.get()
        self._config.update(
            {"model_folders": [f for f in cfg.model_folders if f != p]})
        return self._config.get().model_folders

    # ── Internal ─────────────────────────────────────────────────────────────

    def _require(self, model_id: str) -> ModelEntry:
        m = self._models.get(model_id)
        if not m:
            raise KeyError(f"Model not found: {model_id}")
        return m

    def _persist(self) -> None:
        rows = [m.to_dict() for m in self._models.values()]
        db_write(self._paths.db_path, "models", rows)
