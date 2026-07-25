"""Generate the models-preset.ini consumed by llama-server --models-preset.

Direct port of pi-test's models/preset.py — the generated INI must stay
byte-compatible for the same app state.
"""
from __future__ import annotations

import configparser
import io
from pathlib import Path
from typing import Any

from llama_router.core.storage import write_text
from llama_router.core.utils import sanitise
from llama_router.schemas import ModelEntry, ModelState, Profile


# Boolean CLI toggle flags — no "off" form exists on the llama.cpp side, so they
# must only ever be written as "true" (via _write_conditional_params) and never
# as "false" through the generic loop below.
BOOLEAN_TOGGLE_KEYS = {
    "mlock", "no-mmap", "swa-full", "no-kv-offload", "no-cache-prompt",
    "no-mmproj-offload", "load-on-startup", "cpu-moe", "embedding",
}

# Parameters that must never appear in the INI (they're CLI-only or handled
# separately by the server launcher).
_BLOCKED = {"host", "port", "api-key", "models-preset", "metrics",
            "cont-batching", "no-cont-batching", "models-max", "parallel",
            "threads", "cors", "log-disable",
            "model", "load-on-startup",
            # Written conditionally by _write_conditional_params
            "no-mmproj-offload", "embedding",
            "spec-type", "spec-draft-model", "spec-draft-n-max",
            "cache-type-k-draft", "cache-type-v-draft",
            # Performance: only when true
            "mlock", "no-mmap", "cpu-moe",
            # Performance: only when fit is active
            "fit-target",
            # Cache: only when true
            "swa-full", "no-kv-offload", "no-cache-prompt",
            # Chat template: only when set and jinja isn't off
            "chat-template-file"}

# Keys that should appear only in per-model profile sections, never in the
# global [*] section. sleep-idle-seconds is set per-model in the UI.
_PROFILE_ONLY_KEYS = {"sleep-idle-seconds"}


def _section_name(model: ModelEntry, profile: Profile, multi: bool) -> str:
    """Return the INI section name for a model/profile pair.

    If a model has only one active profile the section is just the route alias
    (or sanitised model name); with multiple active profiles each gets its own
    section: ``alias_ProfileName``.
    """
    alias = sanitise(profile.route_alias.strip()) or sanitise(model.name)
    if multi:
        return f"{alias}_{sanitise(profile.name)}"
    return alias


def section_conflicts(
    models: list[ModelEntry],
    profiles_by_model: dict[str, list[Profile]],
) -> list[str]:
    """Return duplicate active INI section names (case-insensitive)."""
    seen: dict[str, str] = {}
    conflicts: set[str] = set()
    for model in models:
        if not model.enabled or model.state != ModelState.VALID:
            continue
        active = [p for p in profiles_by_model.get(model.id, []) if p.active]
        multi = len(active) > 1
        for profile in active:
            section = _section_name(model, profile, multi)
            folded = section.casefold()
            if folded in seen:
                conflicts.add(section)
            else:
                seen[folded] = section
    return sorted(conflicts, key=str.casefold)


def _bool_str(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _is_true(v: Any) -> bool:
    return v is True or str(v).lower() == "true"


def _write_conditional_params(section_dict: dict, params: dict) -> None:
    """Write params that have conditional INI-write rules."""
    # ── Multimodal ──────────────────────────────────────────────────────────
    if _is_true(params.get("no-mmproj-offload")):
        section_dict["no-mmproj-offload"] = "true"

    # ── Speculative decoding ─────────────────────────────────────────────────
    spec_type = str(params.get("spec-type") or "none")
    if spec_type and spec_type != "none":
        section_dict["spec-type"] = spec_type
        draft_model = str(params.get("spec-draft-model") or "")
        if draft_model:
            section_dict["spec-draft-model"] = draft_model
        n_max = params.get("spec-draft-n-max")
        section_dict["spec-draft-n-max"] = str(int(n_max)) if n_max is not None else "2"

    for key in ("cache-type-k-draft", "cache-type-v-draft"):
        val = str(params.get(key) or "")
        if val and val != "f16":
            section_dict[key] = val

    # ── Performance ──────────────────────────────────────────────────────────
    if _is_true(params.get("mlock")):
        section_dict["mlock"] = "true"
    if _is_true(params.get("no-mmap")):
        section_dict["no-mmap"] = "true"
    if _is_true(params.get("cpu-moe")):
        section_dict["cpu-moe"] = "true"

    # fit-target: only when fit is not explicitly off (default = on)
    fit_val = str(params.get("fit") or "on")
    if fit_val != "off":
        ft = params.get("fit-target")
        if ft is not None and str(ft).strip():
            section_dict["fit-target"] = str(int(float(str(ft))))

    # ── Cache ────────────────────────────────────────────────────────────────
    for key in ("swa-full", "no-kv-offload", "no-cache-prompt"):
        if _is_true(params.get(key)):
            section_dict[key] = "true"

    # ── Chat template file ───────────────────────────────────────────────────
    # llama-server requires --jinja for --chat-template-file; jinja defaults to
    # on (empty value), so only skip when it's explicitly turned off.
    tmpl_file = str(params.get("chat-template-file") or "").strip()
    if tmpl_file and str(params.get("jinja") or "true").lower() != "false":
        section_dict["chat-template-file"] = tmpl_file

    # ── Router ───────────────────────────────────────────────────────────────
    # load-on-startup: default false; models load on first request instead.
    if _is_true(params.get("load-on-startup")):
        section_dict["load-on-startup"] = "true"

    # ── Embedding ────────────────────────────────────────────────────────────
    if _is_true(params.get("embedding")):
        section_dict["embedding"] = "true"


def generate(
    models: list[ModelEntry],
    profiles_by_model: dict[str, list[Profile]],
    global_params: dict[str, Any],
) -> str:
    """Return the full INI text for models-preset.ini."""
    conflicts = section_conflicts(models, profiles_by_model)
    if conflicts:
        raise ValueError("Duplicate active route alias: " + ", ".join(conflicts))

    cfg = configparser.RawConfigParser()
    cfg.optionxform = str  # preserve key case

    # [*] global defaults
    cfg["*"] = {}
    for k, v in global_params.items():
        if k not in _BLOCKED and k not in _PROFILE_ONLY_KEYS:
            cfg["*"][k] = _bool_str(v)

    enabled = [m for m in models if m.enabled and m.state == ModelState.VALID]

    for model in sorted(enabled, key=lambda m: m.name.lower()):
        active_profiles = [p for p in profiles_by_model.get(model.id, []) if p.active]
        if not active_profiles:
            continue

        multi = len(active_profiles) > 1

        for profile in active_profiles:
            section = _section_name(model, profile, multi)
            cfg[section] = {"model": model.path}

            for k, v in profile.params.items():
                if k not in _BLOCKED:
                    cfg[section][k] = _bool_str(v)

            _write_conditional_params(cfg[section], profile.params)

    buf = io.StringIO()
    cfg.write(buf)
    return buf.getvalue()


def parse_profile_params(
    text: str,
    models: list[ModelEntry],
    profiles_by_model: dict[str, list[Profile]],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Read editable INI values back into global and active profile params.

    Sections are matched with the same naming rule used by :func:`generate`.
    Inactive profiles have no INI section and are intentionally left alone.
    Deleting an option from a matched section therefore removes it from that
    profile instead of allowing the database to restore it on the next regen.
    """
    cfg = configparser.RawConfigParser(strict=False)
    cfg.optionxform = str
    cfg.read_string(text)

    global_params = dict(cfg.items("*")) if cfg.has_section("*") else {}
    updates: dict[str, dict[str, str]] = {}
    enabled = [m for m in models if m.enabled and m.state == ModelState.VALID]
    for model in enabled:
        active = [p for p in profiles_by_model.get(model.id, []) if p.active]
        multi = len(active) > 1
        for profile in active:
            section = _section_name(model, profile, multi)
            if not cfg.has_section(section):
                continue
            updates[profile.id] = {
                key: value for key, value in cfg.items(section)
                if key != "model"
            }
    return global_params, updates


def _model_key_value(stripped: str) -> str | None:
    """Value of a ``model = path`` / ``model: path`` line (input pre-stripped).

    Plain string parsing on purpose — any regex over this user-editable text
    kept tripping CodeQL's polynomial-ReDoS check.
    """
    if not stripped.startswith("model"):
        return None
    rest = stripped[len("model"):].lstrip()
    if not rest.startswith(("=", ":")):
        return None
    value = rest[1:].strip()
    return value or None


def _norm_path(p: str) -> str:
    # Always normalise both separators so Windows/Linux paths compare equal
    # regardless of the host OS (the preset file may contain either style).
    return p.replace("\\", "/").lower()


def strip_disabled_sections(text: str, models: list[ModelEntry]) -> str:
    """Drop INI sections whose ``model`` path belongs to a disabled model.

    Applied when saving a hand-edited preset: the editor may hold content
    fetched before a model was disabled, and a disabled model must never
    survive in models-preset.ini. Works on the raw text so the user's
    formatting and comments in the remaining sections are preserved.
    """
    disabled = {_norm_path(m.path) for m in models if not m.enabled}
    if not disabled:
        return text

    out: list[str] = []
    block: list[str] = []
    block_model: str | None = None

    def flush() -> None:
        nonlocal block, block_model
        if block_model is None or block_model not in disabled:
            out.extend(block)
        block, block_model = [], None

    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            flush()
        block.append(line)
        stripped = line.strip()
        if not stripped.startswith(("#", ";")):
            value = _model_key_value(stripped)
            if value:
                block_model = _norm_path(value)
    flush()
    return "".join(out)


def write_preset(
    path: Path,
    models: list[ModelEntry],
    profiles_by_model: dict[str, list[Profile]],
    global_params: dict[str, Any],
) -> None:
    text = generate(models, profiles_by_model, global_params)
    write_text(path, text)
