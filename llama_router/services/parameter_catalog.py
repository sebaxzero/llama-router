"""Runtime-aware llama-server parameter catalogue (stdlib only)."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from llama_router.core.events import EventBus
from llama_router.core.paths import asset_path
from llama_router.core.storage import db_read, db_write

log = logging.getLogger(__name__)

_CACHE_KEY = "parameter_catalog_cache_v1"
_HELP_TIMEOUT = 10

# This map is shared conceptually with ServerManager._build_cmd.  The UI never
# owns a second list; changing a command option must update this one map too.
ROUTER_CONTROLLED_OPTIONS = {
    "models-preset", "host", "port", "models-max", "parallel",
    "threads", "threads-batch", "api-key", "metrics", "cont-batching",
    "no-cont-batching", "models-autoload", "no-models-autoload",
    "ssl-key-file", "ssl-cert-file", "models-dir", "alias", "api-key-file",
}
_MODEL_SOURCE_ROLES = frozenset({"primary_model", "mmproj", "draft"})


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    aliases: tuple[str, ...] = ()
    negated_aliases: tuple[str, ...] = ()
    environment_variables: tuple[str, ...] = ()
    category: str = "General"
    description: str = ""
    value_type: str = "unknown"
    metavar: str = ""
    default: Any = None
    allowed_values: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ("global", "model")
    router_controlled: bool = False
    blocked_reason: str = ""
    source_role: str = "none"
    source: str = "fallback"

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "ParameterSpec":
        def tup(value: Any) -> tuple[str, ...]:
            if isinstance(value, str):
                return (value,)
            return tuple(str(x) for x in (value or ()))
        source_role = str(row.get("source_role", "none"))
        default_scopes = ("model",) if source_role in _MODEL_SOURCE_ROLES \
            else ("global", "model")
        return cls(
            name=str(row.get("name", "")), aliases=tup(row.get("aliases")),
            negated_aliases=tup(row.get("negated_aliases")),
            environment_variables=tup(row.get("environment_variables")),
            category=str(row.get("category", "General")),
            description=str(row.get("description", "")),
            value_type=str(row.get("value_type", "unknown")),
            metavar=str(row.get("metavar", "")), default=row.get("default"),
            allowed_values=tup(row.get("allowed_values")),
            scopes=tup(row.get("scopes")) or default_scopes,
            router_controlled=bool(row.get("router_controlled", False)),
            blocked_reason=str(row.get("blocked_reason", "")),
            source_role=source_role,
            source=str(row.get("source", "fallback")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "aliases": list(self.aliases),
            "negated_aliases": list(self.negated_aliases),
            "environment_variables": list(self.environment_variables),
            "category": self.category, "description": self.description,
            "value_type": self.value_type, "metavar": self.metavar,
            "default": self.default, "allowed_values": list(self.allowed_values),
            "scopes": list(self.scopes),
            "router_controlled": self.router_controlled,
            "blocked_reason": self.blocked_reason,
            "source_role": self.source_role, "source": self.source,
        }


@dataclass(frozen=True)
class CatalogSnapshot:
    parameters: tuple[ParameterSpec, ...]
    source: str
    version: str = ""


def _clean_flag(flag: str) -> str:
    return flag.lstrip("-").strip()


def _infer_type(metavar: str, default: str, allowed: tuple[str, ...],
                description: str) -> str:
    hint = (metavar or "").lower()
    if allowed:
        return "choice"
    if any(x in hint for x in ("path", "file", "fname", "dir")):
        return "path"
    if any(x in hint for x in ("json", "string", "str", "text")):
        return "string"
    if hint in {"n", "index", "port", "m", "miB", "MiB".lower()}:
        return "integer"
    if any(x in hint for x in ("float", "scale", "factor")):
        return "float"
    if default.lower() in {"true", "false", "enabled", "disabled"}:
        return "boolean"
    if "whether to" in description.lower() and not hint:
        return "boolean"
    return "string" if hint else "unknown"


def parse_help(text: str, *, source: str = "runtime") -> list[ParameterSpec]:
    """Parse the line-oriented help emitted by current llama.cpp builds."""
    rows: dict[str, dict[str, Any]] = {}
    category = "General"
    current: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal current
        if not current:
            return
        names = current.get("names", [])
        positives = [n for n in names if not n.startswith("no-")]
        negatives = [n for n in names if n.startswith("no-")]
        if not positives:
            positives = names[:1]
        canonical = max(positives, key=lambda n: ("-" in n, len(n)))
        allowed = tuple(current.get("allowed", ()))
        metavar = current.get("metavar", "")
        if not allowed and any(sep in metavar for sep in ("|", ",")):
            allowed = tuple(x.strip(" <>{}[]`") for x in
                            re.split(r"[|,]", metavar) if x.strip())
        default = current.get("default")
        desc = " ".join(str(x).strip() for x in current.get("desc", ())).strip()
        if default is None:
            default_match = re.search(r"\(default:\s*([^\)]+)\)", desc)
            if default_match:
                default = default_match.group(1).strip()
        if not current.get("env"):
            env_match = re.search(r"\(env:\s*([^\)]+)\)", desc)
            if env_match:
                current["env"] = [x.strip() for x in
                                   env_match.group(1).split(",")]
        if not allowed:
            allowed_match = re.search(r"allowed values:\s*(.*)$", desc,
                                      re.IGNORECASE)
            if allowed_match:
                allowed = tuple(x.strip(" '`.") for x in
                                allowed_match.group(1).split(","))
        desc = re.sub(r"\s*\(default:\s*[^\)]+\)", "", desc).strip()
        desc = re.sub(r"\s*\(env:\s*[^\)]+\)", "", desc).strip()
        row = {
            "name": canonical,
            "aliases": tuple(n for n in positives if n != canonical),
            "negated_aliases": tuple(negatives),
            "environment_variables": tuple(current.get("env", ())),
            "category": current.get("category", category),
            "description": desc,
            "metavar": metavar,
            "default": default,
            "allowed_values": allowed,
            "value_type": _infer_type(metavar, str(default or ""), allowed, desc),
            "source": source,
        }
        existing = rows.get(canonical)
        if existing:
            row["aliases"] = tuple(dict.fromkeys(
                existing.get("aliases", ()) + row["aliases"] + (canonical,)))
            row["aliases"] = tuple(x for x in row["aliases"] if x != canonical)
            row["negated_aliases"] = tuple(dict.fromkeys(
                existing.get("negated_aliases", ()) + row["negated_aliases"]))
            if not row["description"]:
                row["description"] = existing.get("description", "")
        rows[canonical] = row
        current = None

    for raw in text.replace("\r", "").split("\n"):
        line = raw.rstrip()
        header = re.match(r"^\s*-{3,}\s*(.*?)\s*-{3,}\s*$", line)
        if header:
            finish()
            category = header.group(1).strip() or "General"
            continue

        # llama.cpp prints an option table whose left column contains flags,
        # aliases and (sometimes) one metavar, followed by a wide gap and the
        # description.  Splitting at that gap is more stable than trying to
        # consume whitespace in a single flag regex: ``-t, --threads N`` and
        # ``--model FNAME`` otherwise make the metavar look like prose.
        leading = len(line) - len(line.lstrip())
        option_match = re.match(r"^\s*(?P<left>.*)\s{2,}(?P<desc>\S.*)$", line)
        left = option_match.group("left") if option_match else line.strip()
        desc = option_match.group("desc") if option_match else ""
        # A help row may contain only flags on its first line and wrap the
        # description below it.  In that shape the alignment gap between the
        # first two aliases looks exactly like the flag/description gap, so a
        # greedy split would turn ``-hf, -hfr, --hf-repo`` into one option
        # named ``hf`` with the remaining aliases treated as prose.  If the
        # would-be description starts with another flag, the whole line is
        # the option column.
        if (option_match and desc.lstrip().startswith("-")
                and left.rstrip().endswith((",", "/"))):
            left, desc = line.strip(), ""
        flag_tokens = re.findall(r"-{1,2}[A-Za-z0-9][A-Za-z0-9_-]*", left)
        # Wrapped descriptions can contain a flag (for example ``--no-mmproj``)
        # but are indented far beyond the option table.  Treat them as prose.
        if flag_tokens and leading <= 4 and line.lstrip().startswith("-"):
            finish()
            names = [_clean_flag(token) for token in flag_tokens]
            last_flag_end = max(left.find(token) + len(token)
                                for token in flag_tokens)
            between = left[last_flag_end:].strip(" ,\t")
            metavar = between.split()[0].strip("<>{}[]") if between else ""
            current = {"names": names, "metavar": metavar,
                       "desc": [desc], "category": category}
            continue
        if current is None:
            continue
        text_line = line.strip()
        env_match = re.search(r"\(env:\s*([^\)]+)\)", text_line)
        if env_match:
            current.setdefault("env", []).extend(
                x.strip() for x in env_match.group(1).split(","))
        default_match = re.search(r"\(default:\s*([^\)]+)\)", text_line)
        if default_match:
            current["default"] = default_match.group(1).strip()
        allowed_match = re.search(r"allowed values:\s*(.*)$", text_line,
                                  re.IGNORECASE)
        if allowed_match:
            current.setdefault("allowed", []).extend(
                x.strip(" '`.") for x in allowed_match.group(1).split(","))
        if text_line and not env_match and not default_match and not allowed_match:
            current.setdefault("desc", []).append(text_line)
    finish()

    out: list[ParameterSpec] = []
    for row in rows.values():
        if not row["name"]:
            continue
        out.append(ParameterSpec.from_dict(row))
    return sorted(out, key=lambda x: x.name)


class ParameterCatalog:
    def __init__(self, paths, runtimes, events: EventBus) -> None:
        self._paths = paths
        self._runtimes = runtimes
        self._events = events
        self._snapshot = CatalogSnapshot((), "none")
        self._lock = threading.RLock()

    @property
    def snapshot(self) -> CatalogSnapshot:
        with self._lock:
            if not self._snapshot.parameters:
                self._snapshot = self._load_fallback()
            return self._snapshot

    @property
    def parameters(self) -> tuple[ParameterSpec, ...]:
        return self.snapshot.parameters

    def canonical_name(self, name: str) -> str | None:
        wanted = _clean_flag(name).casefold()
        for spec in self.parameters:
            if wanted == spec.name.casefold() or any(
                    wanted == alias.casefold()
                    for alias in (*spec.aliases, *spec.negated_aliases,
                                  *spec.environment_variables)):
                return spec.name
        return None

    def get(self, name: str) -> ParameterSpec | None:
        canonical = self.canonical_name(name)
        return next((p for p in self.parameters if p.name == canonical), None)

    def search(self, query: str, *, include_managed: bool = False,
               scope: str | None = None) -> list[ParameterSpec]:
        q = query.casefold().strip()
        scored: list[tuple[int, ParameterSpec]] = []
        for spec in self.parameters:
            if spec.router_controlled and not include_managed:
                continue
            if scope and scope not in spec.scopes:
                continue
            fields = [spec.name, *spec.aliases, *spec.negated_aliases,
                      *spec.environment_variables, spec.category,
                      spec.description]
            hay = " ".join(fields).casefold()
            if q and q not in hay:
                continue
            name = spec.name.casefold()
            score = 0 if not q else (0 if name.startswith(q) else
                                     1 if any(a.casefold().startswith(q)
                                              for a in spec.aliases) else
                                     2 if q in name else 3)
            scored.append((score, spec))
        return [spec for _, spec in sorted(scored, key=lambda x: (x[0], x[1].name))]

    def _load_fallback(self) -> CatalogSnapshot:
        try:
            data = json.loads(asset_path("parameter-catalog.json").read_text(
                encoding="utf-8"))
            specs = [ParameterSpec.from_dict(x) for x in data.get("parameters", [])]
            source = data.get("source", "bundled")
            version = str(data.get("source_version", ""))
        except (OSError, ValueError):
            specs = [
                ParameterSpec("model", aliases=("m",), value_type="path",
                              source_role="primary_model", source="built-in"),
                ParameterSpec("hf-file", aliases=("hff",), value_type="path",
                              source_role="primary_model", source="built-in"),
                ParameterSpec("hf-repo", aliases=("hf", "hfr"),
                              value_type="string", source_role="primary_model",
                              source="built-in"),
                ParameterSpec("model-url", value_type="string",
                              source_role="primary_model", source="built-in"),
                ParameterSpec("docker-repo", value_type="string",
                              source_role="primary_model", source="built-in"),
                ParameterSpec("mmproj", value_type="path", source_role="mmproj",
                              source="built-in"),
                ParameterSpec("model-draft", value_type="path", source_role="draft",
                              source="built-in"),
                ParameterSpec("load-on-startup", value_type="boolean",
                              scopes=("model",), source="built-in"),
                ParameterSpec("stop-timeout", value_type="integer",
                              scopes=("model",), source="built-in"),
            ]
            source, version = "built-in", ""
        specs = self._decorate(specs)
        return CatalogSnapshot(tuple(specs), source, version)

    @staticmethod
    def _decorate(specs: Iterable[ParameterSpec]) -> list[ParameterSpec]:
        out: list[ParameterSpec] = []
        for spec in specs:
            managed = spec.name in ROUTER_CONTROLLED_OPTIONS
            reason = spec.blocked_reason
            if managed and not reason:
                reason = "Controlled by router Settings/command line"
            scopes = ("model",) if spec.source_role in _MODEL_SOURCE_ROLES \
                else spec.scopes
            out.append(ParameterSpec(
                **{**spec.__dict__, "router_controlled": managed or spec.router_controlled,
                   "blocked_reason": reason, "scopes": scopes}))
        known = {p.name for p in out}
        # Keep router-owned controls visible as disabled catalogue entries even
        # when a particular llama-server build omits them from ``--help``.
        # This is the contract the editor uses to explain why an option should
        # be changed in Settings instead of inserted into a preset.
        for name in sorted(ROUTER_CONTROLLED_OPTIONS):
            if name in known:
                continue
            out.append(ParameterSpec(
                name=name, scopes=("global",), router_controlled=True,
                blocked_reason="Controlled by router Settings/command line",
                source="router"))
            known.add(name)
        for name, aliases, scopes, desc in (
                ("load-on-startup", (), ("model",),
                 "Load this route when the router starts"),
                ("stop-timeout", (), ("model",),
                 "Seconds to wait before forcing an unload")):
            if name not in known:
                out.append(ParameterSpec(name, scopes=scopes, description=desc,
                                         value_type="boolean" if name.startswith("load") else "integer",
                                         source="policy"))
        return sorted(out, key=lambda p: p.name)

    def _runtime_identity(self, exe: Path, version: str) -> str:
        try:
            st = exe.stat()
            return f"{exe.resolve()}|{version}|{st.st_size}|{st.st_mtime_ns}"
        except OSError:
            return f"{exe}|{version}"

    def refresh_runtime(self, *, background: bool = True) -> None:
        def work() -> None:
            try:
                exe = self._runtimes.get_executable()
                if not exe:
                    with self._lock:
                        self._snapshot = self._load_fallback()
                    self._events.publish("parameter_catalog_changed", {
                        "source": self._snapshot.source,
                        "count": len(self._snapshot.parameters),
                    })
                    return
                flags = {"creationflags": subprocess.CREATE_NO_WINDOW} \
                    if sys.platform == "win32" else {}
                version_run = subprocess.run(
                    [str(exe), "--version"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=_HELP_TIMEOUT, **flags)
                version = (version_run.stdout or version_run.stderr).strip()
                identity = self._runtime_identity(exe, version)
                cache = db_read(self._paths.db_path, _CACHE_KEY, default={}) or {}
                cached = cache.get(identity)
                if cached:
                    specs = tuple(ParameterSpec.from_dict(x)
                                  for x in cached.get("parameters", ()))
                else:
                    help_run = subprocess.run(
                        [str(exe), "--help"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        timeout=_HELP_TIMEOUT, **flags)
                    output = (help_run.stdout or "") + "\n" + (help_run.stderr or "")
                    specs = tuple(self._decorate(parse_help(
                        output, source="runtime")))
                    if len(specs) < 5:
                        raise ValueError("runtime help produced too few parameters")
                    cache[identity] = {"parameters": [p.to_dict() for p in specs],
                                       "version": version,
                                       "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                    # ponytail: keep four runtime identities; evict oldest only
                    # when cache growth becomes visible.
                    if len(cache) > 4:
                        oldest = sorted(cache, key=lambda k: cache[k].get("updated_at", ""))
                        for key in oldest[:-4]:
                            cache.pop(key, None)
                    db_write(self._paths.db_path, _CACHE_KEY, cache)
                with self._lock:
                    self._snapshot = CatalogSnapshot(specs, "runtime", version)
                self._events.publish("parameter_catalog_changed", {
                    "source": "runtime", "version": version,
                    "count": len(specs),
                })
            except Exception as exc:
                log.info("Parameter catalog refresh failed: %s", exc)
                with self._lock:
                    self._snapshot = self._load_fallback()
                    fallback = self._snapshot
                self._events.publish("parameter_catalog_changed", {
                    "source": fallback.source,
                    "count": len(fallback.parameters),
                })
        if background:
            threading.Thread(target=work, daemon=True,
                             name="parameter-catalog").start()
        else:
            work()
