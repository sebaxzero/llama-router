"""Lossless document tools for the user-owned models-preset.ini."""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import ntpath
import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from llama_router.schemas import ModelEntry


# ---------------------------------------------------------------------------
# Lossless document model
# ---------------------------------------------------------------------------

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_HEADER_RE = re.compile(r"^[ \t]*\[(?P<name>[^\]]*)\][ \t]*(?:[;#].*)?$")
_SOURCE_KEYS = {
    "model": "local",
    "m": "local",
    "llama_arg_model": "local",
    "model-url": "url",
    "mu": "url",
    "llama_arg_model_url": "url",
    "hf": "hf",
    "hfr": "hf",
    "hf-repo": "hf",
    "hf-file": "hf",
    "hff": "hf",
    "llama_arg_hf_repo": "hf",
    "docker-repo": "docker",
    "dr": "docker",
    "llama_arg_docker_repo": "docker",
}
_SOURCE_PATH_KEYS = {"model", "mmproj", "model-draft", "mmproj-file"}


@dataclass(frozen=True)
class TextEdit:
    """A replacement against the normalized document view."""

    start: int
    end: int
    replacement: str


@dataclass(frozen=True)
class PresetDiagnostic:
    code: str
    severity: str
    message: str
    start: int = 0
    end: int = 0
    blocks_save: bool = False
    blocks_start: bool = False
    section: str = ""
    key: str = ""


@dataclass(frozen=True)
class PresetEntry:
    key: str
    value: str
    section: str
    line_start: int
    line_end: int
    value_start: int
    value_end: int
    raw_line: str


@dataclass(frozen=True)
class PresetSection:
    name: str
    effective_name: str
    start: int
    end: int
    header_start: int
    header_end: int
    entries: tuple[PresetEntry, ...] = ()


@dataclass(frozen=True)
class PresetRoute:
    section: str
    effective_section: str
    source_key: str
    source_value: str
    source_kind: str
    normalized_source: str
    usable: bool
    start: int
    end: int


def normalize_editor_text(text: str) -> str:
    """Use LF in Tk while retaining the original text in the document."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith(("\n", "\r")):
        return line[-1]
    return ""


def _without_eol(line: str) -> str:
    return line[:-len(_line_ending(line))] if _line_ending(line) else line


def _strip_inline_comment(value: str) -> str:
    """Mirror llama.cpp's whitespace-before-comment rule."""
    for i, ch in enumerate(value):
        if ch in ";#" and i > 0 and value[i - 1].isspace():
            return value[:i].rstrip()
    return value.rstrip()


def _effective_section(name: str) -> str:
    """Apply llama.cpp's tag canonicalization for collision diagnostics."""
    if name in ("", "*") or ":" not in name:
        return name
    prefix, tag = name.rsplit(":", 1)
    if re.search(r"[-.]([A-Za-z0-9_]+)$", tag):
        tag = re.sub(r"[-.]([A-Za-z0-9_]+)$", lambda m: m.group(1).upper(), tag)
    else:
        tag = tag.upper()
    return f"{prefix}:{tag}"


def _path_flavor(value: str) -> str:
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("\\\\", "//")):
        return "windows"
    return "posix"


def normalize_model_path(value: str, *, base: Path | None = None,
                         flavor: str | None = None) -> str:
    """Normalize local paths without asking Path.resolve to parse foreign paths."""
    raw = value.strip()
    flavor = flavor or ("windows" if _path_flavor(raw) == "windows" or
                        (base is not None and
                         _path_flavor(str(base)) == "windows") else "posix")
    if flavor == "windows":
        if base is not None and not ntpath.isabs(raw):
            raw = ntpath.join(str(base), raw)
        return ntpath.normcase(ntpath.normpath(raw.replace("/", "\\")))
    p = Path(raw).expanduser()
    if not p.is_absolute() and base is not None:
        p = base / p
    try:
        p = p.resolve(strict=False)
    except OSError:
        p = Path(os.path.abspath(str(p)))
    return posixpath.normpath(str(p)).replace("\\", "/")


class PresetDocument:
    """Parsed, editable view of a preset without a destructive serializer."""

    def __init__(self, raw_text: str, *, runtime_cwd: Path | None = None,
                 catalog: Any = None, registry: Iterable[ModelEntry] = ()):
        self.raw_text = raw_text
        self.text = normalize_editor_text(raw_text)
        self.runtime_cwd = runtime_cwd
        self.catalog = catalog
        self.registry = tuple(registry)
        self.sections: tuple[PresetSection, ...] = ()
        self.entries: tuple[PresetEntry, ...] = ()
        self.routes: tuple[PresetRoute, ...] = ()
        self.diagnostics: tuple[PresetDiagnostic, ...] = ()
        self._parse()

    @classmethod
    def parse(cls, text: str, *, runtime_cwd: Path | None = None,
              catalog: Any = None,
              registry: Iterable[ModelEntry] = ()) -> "PresetDocument":
        return cls(text, runtime_cwd=runtime_cwd, catalog=catalog,
                   registry=registry)

    @property
    def errors(self) -> tuple[PresetDiagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == "error")

    @property
    def warnings(self) -> tuple[PresetDiagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == "warning")

    @property
    def usable_routes(self) -> tuple[PresetRoute, ...]:
        return tuple(r for r in self.routes if r.usable)

    @property
    def unique_sources(self) -> int:
        # This is a configured-reference count, not a loadability count:
        # stale local paths should still be visible in the status bar.  The
        # separate ``usable_routes`` property remains the start gate.
        return len({r.normalized_source for r in self.routes
                    if r.source_value.strip()})

    @property
    def can_save(self) -> bool:
        return not any(d.blocks_save for d in self.diagnostics)

    @property
    def can_start(self) -> bool:
        return not any(d.blocks_start for d in self.diagnostics)

    def _parse(self) -> None:
        text = self.text
        diagnostics: list[PresetDiagnostic] = []
        entries: list[PresetEntry] = []
        sections: list[PresetSection] = []
        current_name = ""
        current_header_start = 0
        current_header_end = 0
        current_entries: list[PresetEntry] = []
        seen_sections: dict[str, PresetSection] = {}
        seen_keys: dict[str, dict[str, PresetEntry]] = {}

        def finish_section(end: int) -> None:
            nonlocal current_entries
            if not current_name and not current_entries:
                current_entries = []
                return
            section = PresetSection(
                current_name,
                _effective_section(current_name),
                current_header_start,
                end,
                current_header_start,
                current_header_end,
                tuple(current_entries),
            )
            sections.append(section)
            current_entries = []

        offset = 0
        lines = text.splitlines(keepends=True)
        if not lines and text:
            lines = [text]
        for line in lines:
            body = _without_eol(line)
            stripped = body.strip()
            line_start = offset
            line_end = offset + len(line)
            if not stripped or stripped.startswith(("#", ";")):
                offset = line_end
                continue

            header = _HEADER_RE.match(body)
            if header:
                finish_section(line_start)
                current_name = header.group("name").strip(" \t")
                current_header_start = line_start
                current_header_end = line_end
                effective = _effective_section(current_name)
                if current_name in seen_sections:
                    diagnostics.append(PresetDiagnostic(
                        "duplicate_section", "error",
                        f"Duplicate section [{current_name}]; llama-server keeps the last one.",
                        line_start, line_end, True, True, current_name))
                elif effective in {s.effective_name for s in sections}:
                    diagnostics.append(PresetDiagnostic(
                        "duplicate_effective_section", "error",
                        f"Section [{current_name}] collides with canonical section [{effective}].",
                        line_start, line_end, True, True, current_name))
                seen_sections.setdefault(current_name, PresetSection(
                    current_name, effective, line_start, line_end,
                    line_start, line_end, ()))
                offset = line_end
                continue

            # The runtime grammar requires an identifier and '='.
            eq = body.find("=")
            if eq > 0:
                key = body[:eq].strip(" \t")
                if _KEY_RE.match(key):
                    value_start_rel = eq + 1
                    while value_start_rel < len(body) and body[value_start_rel] in " \t":
                        value_start_rel += 1
                    raw_value = body[value_start_rel:]
                    value = _strip_inline_comment(raw_value)
                    value_start = line_start + value_start_rel
                    value_end = value_start + len(value)
                    entry = PresetEntry(key, value, current_name, line_start,
                                        line_end, value_start, value_end, line)
                    entries.append(entry)
                    current_entries.append(entry)
                    section_keys = seen_keys.setdefault(current_name, {})
                    if key in section_keys:
                        diagnostics.append(PresetDiagnostic(
                            "duplicate_key", "warning",
                            f"Duplicate key '{key}'; llama-server keeps the last value.",
                            line_start, line_end, False, False,
                            current_name, key))
                    section_keys[key] = entry
                    offset = line_end
                    continue

            diagnostics.append(PresetDiagnostic(
                "syntax", "error", "Invalid INI line; expected a section, comment or key = value.",
                line_start, line_end, True, True, current_name))
            offset = line_end

        finish_section(len(text))
        self.sections = tuple(sections)
        self.entries = tuple(entries)

        # Validate values only when a runtime catalogue knows the option.  A
        # missing/unknown option is intentionally left editable: a newer
        # llama.cpp build may introduce it before the bundled catalogue knows
        # about it.
        if self.catalog is not None and hasattr(self.catalog, "get"):
            valid_bool = {"true", "false", "on", "off", "yes", "no",
                          "enabled", "disabled", "1", "0"}
            for entry in entries:
                spec = self.catalog.get(entry.key)
                if spec is None:
                    continue
                scope = "global" if entry.section in ("", "*") else "model"
                if scope not in spec.scopes:
                    diagnostics.append(PresetDiagnostic(
                        "parameter_scope", "warning",
                        f"Parameter '{entry.key}' is not valid in {scope} scope.",
                        entry.line_start, entry.line_end, False, False,
                        entry.section, entry.key))
                if spec.router_controlled:
                    diagnostics.append(PresetDiagnostic(
                        "router_controlled", "warning",
                        f"Parameter '{entry.key}' is managed by router Settings and may be ignored.",
                        entry.line_start, entry.line_end, False, False,
                        entry.section, entry.key))
                value = entry.value.strip()
                if not value:
                    if spec.value_type in ("boolean", "integer", "float",
                                           "choice", "path"):
                        diagnostics.append(PresetDiagnostic(
                            "invalid_value", "warning",
                            f"Value for '{entry.key}' is empty.",
                            entry.value_start, entry.value_end, False, False,
                            entry.section, entry.key))
                    continue
                invalid = False
                if spec.allowed_values:
                    allowed = {str(x).casefold() for x in spec.allowed_values}
                    invalid = value.casefold() not in allowed
                elif spec.value_type == "boolean":
                    invalid = value.casefold() not in valid_bool
                elif spec.value_type == "integer":
                    invalid = re.fullmatch(r"[+-]?\d+", value) is None
                elif spec.value_type == "float":
                    try:
                        float(value)
                    except ValueError:
                        invalid = True
                if invalid:
                    diagnostics.append(PresetDiagnostic(
                        "invalid_value", "warning",
                        f"Value for '{entry.key}' is not a valid {spec.value_type}.",
                        entry.value_start, entry.value_end, False, False,
                        entry.section, entry.key))

        # A canonical duplicate through aliases is less common than an exact
        # duplicate.  Let a runtime catalog identify it when available.
        if self.catalog is not None and hasattr(self.catalog, "canonical_name"):
            by_section: dict[str, dict[str, PresetEntry]] = {}
            for entry in entries:
                canonical = self.catalog.canonical_name(entry.key) or entry.key
                bucket = by_section.setdefault(entry.section, {})
                if canonical in bucket and bucket[canonical].key != entry.key:
                    diagnostics.append(PresetDiagnostic(
                        "duplicate_parameter_alias", "warning",
                        f"Keys '{bucket[canonical].key}' and '{entry.key}' refer to the same option.",
                        entry.line_start, entry.line_end, False, False,
                        entry.section, entry.key))
                bucket[canonical] = entry

        routes: list[PresetRoute] = []
        for section in sections:
            if section.name in ("", "*"):
                continue
            source_entry = next(
                (e for e in reversed(section.entries)
                 if e.key.casefold() in _SOURCE_KEYS), None)
            if source_entry is None:
                diagnostics.append(PresetDiagnostic(
                    "missing_source", "error",
                    f"Section [{section.name}] has no model, URL, HF or Docker source.",
                    section.header_start, section.header_end, True, True,
                    section.name))
                continue
            key = source_entry.key.casefold()
            kind = _SOURCE_KEYS.get(key, "unknown")
            value = source_entry.value.strip()
            normalized = value
            usable = bool(value)
            if kind == "local":
                normalized = normalize_model_path(value, base=self.runtime_cwd)
                # A missing local path is a route warning; it becomes a start
                # error only when no other usable route remains.
                if (_path_flavor(value) != "windows" or os.name == "nt"):
                    candidate = Path(value)
                    if not candidate.is_absolute() and self.runtime_cwd is not None:
                        candidate = self.runtime_cwd / candidate
                    if not candidate.exists():
                        usable = False
                        diagnostics.append(PresetDiagnostic(
                            "missing_path", "warning",
                            f"Local model path does not exist: {value}",
                            source_entry.value_start, source_entry.value_end,
                            False, False, section.name, source_entry.key))
            elif kind in ("hf", "docker", "url"):
                normalized = value.casefold()
            if not value:
                usable = False
            routes.append(PresetRoute(
                section.name, section.effective_name, source_entry.key, value,
                kind, normalized, usable, section.start, section.end))

        # Relate local routes to the GGUF registry when one is available.  The
        # registry remains advisory (remote routes and unknown files are still
        # valid text), but the warning makes stale entries visible in the UI.
        registry_paths = {
            normalize_model_path(model.path, base=self.runtime_cwd)
            for model in self.registry if getattr(model, "path", "")
        }
        if registry_paths:
            for route in routes:
                if route.source_kind == "local" and route.usable:
                    if route.normalized_source not in registry_paths:
                        diagnostics.append(PresetDiagnostic(
                            "unregistered_model", "warning",
                            f"Model path is not present in the GGUF registry: {route.source_value}",
                            route.start, route.end, False, False, route.section,
                            route.source_key))

        by_source: dict[str, list[PresetRoute]] = {}
        for route in routes:
            # Report repeated references even when a local file is currently
            # missing; the duplication is still actionable in the editor.
            if route.source_value.strip():
                by_source.setdefault(route.normalized_source, []).append(route)
        for same_source in by_source.values():
            if len(same_source) > 1:
                names = ", ".join(f"[{route.section}]" for route in same_source)
                for route in same_source:
                    diagnostics.append(PresetDiagnostic(
                        "duplicate_model_route", "warning",
                        f"Multiple routes reference the same model: {names}.",
                        route.start, route.end, False, False, route.section,
                        route.source_key))

        if not routes:
            diagnostics.append(PresetDiagnostic(
                "no_routes", "error", "The preset has no model sections.",
                0, len(text), False, True))
        elif not any(route.usable for route in routes):
            diagnostics.append(PresetDiagnostic(
                "no_usable_routes", "error", "The preset has no usable model routes.",
                0, len(text), False, True))

        self.routes = tuple(routes)
        self.diagnostics = tuple(diagnostics)

    def section_at_offset(self, offset: int) -> PresetSection | None:
        for section in self.sections:
            if section.start <= offset <= section.end:
                return section
        return None

    def entry_at_offset(self, offset: int) -> PresetEntry | None:
        for entry in self.entries:
            if entry.line_start <= offset <= entry.line_end:
                return entry
        return None

    def add_model(self, section_name: str, model_path: str,
                  companions: dict[str, str] | None = None) -> TextEdit:
        block = f"[{section_name}]\nmodel = {model_path}\n"
        for key, value in (companions or {}).items():
            block += f"{key} = {value}\n"
        if self.text and not self.text.endswith("\n"):
            block = "\n" + block
        elif self.text and not self.text.endswith("\n\n"):
            block = "\n" + block
        elif not self.text:
            block = block
        return TextEdit(len(self.text), len(self.text), block)

    def add_parameter(self, section: str, key: str, value: str) -> TextEdit | None:
        target = next((s for s in self.sections if s.name == section), None)
        if target is None:
            return None
        wanted = (self.catalog.canonical_name(key)
                  if self.catalog is not None and
                  hasattr(self.catalog, "canonical_name") else None) or key
        existing = [e for e in target.entries
                    if ((self.catalog.canonical_name(e.key)
                         if self.catalog is not None and
                         hasattr(self.catalog, "canonical_name") else None)
                        or e.key).casefold() == wanted.casefold()]
        if existing:
            return None
        insert_at = target.end
        line = f"{key} = {value}\n"
        if target.entries:
            last = target.entries[-1]
            insert_at = last.line_end
            if not _line_ending(last.raw_line):
                line = "\n" + line
        elif not _line_ending(self.text[target.header_start:target.header_end]):
            line = "\n" + line
        return TextEdit(insert_at, insert_at, line)

    def remove_sections(self, names: Iterable[str]) -> list[TextEdit]:
        wanted = set(names)
        edits: list[TextEdit] = []
        for section in self.sections:
            if section.name in wanted:
                # Keep comments/blank lines between this section and the next
                # section intact; remove only the header and its actual keys.
                end = section.entries[-1].line_end if section.entries \
                    else section.header_end
                edits.append(TextEdit(section.start, end, ""))
        return edits


def apply_edits(text: str, edits: Iterable[TextEdit]) -> str:
    result = text
    for edit in sorted(edits, key=lambda e: (e.start, e.end), reverse=True):
        result = result[:edit.start] + edit.replacement + result[edit.end:]
    return result


def merge_editor_text(base_raw: str, base_view: str, draft_view: str) -> str:
    """Keep equal source lines byte-for-byte while saving manual edits."""
    if normalize_editor_text(draft_view) == normalize_editor_text(base_view):
        return base_raw
    raw_lines = base_raw.splitlines(keepends=True)
    base_lines = normalize_editor_text(base_view).splitlines()
    draft_lines = normalize_editor_text(draft_view).splitlines()
    matcher = difflib.SequenceMatcher(a=base_lines, b=draft_lines, autojunk=False)
    if not raw_lines and not draft_lines:
        return ""
    dominant = "\r\n" if base_raw.count("\r\n") > base_raw.count("\n") / 2 else "\n"
    out: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.extend(raw_lines[i1:i2])
            continue
        if tag in ("replace", "insert"):
            newline = dominant
            if i1 < len(raw_lines):
                newline = _line_ending(raw_lines[i1]) or newline
            out.extend((line + newline) for line in draft_lines[j1:j2])
        # delete emits no lines.
    # splitlines() drops a final terminator; restore it when the draft has one.
    draft_has_final = normalize_editor_text(draft_view).endswith("\n")
    result = "".join(out)
    if draft_has_final and result and not result.endswith(("\n", "\r")):
        result += dominant
    if not draft_has_final:
        result = result.rstrip("\r\n")
    return result


def fingerprint_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_preset_bytes(data: bytes) -> tuple[str, bool]:
    bom = data.startswith(b"\xef\xbb\xbf")
    if bom:
        data = data[3:]
    return data.decode("utf-8"), bom
