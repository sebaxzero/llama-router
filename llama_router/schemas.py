"""Dataclass schemas — the stdlib replacement for pi-test's pydantic models.

Conventions:
- `from_dict()` is tolerant: unknown keys are dropped, missing keys fall back
  to defaults, enum fields are coerced from their string value. Corrupt rows
  should raise so callers can skip them (same contract as model_validate).
- `to_dict()` returns JSON-serialisable plain data (enums as str values).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ────────────────────────────────────────────────────────────────────

class ModelState(str, Enum):
    VALID = "valid"
    MISSING = "missing"


class RuntimeState(str, Enum):
    INSTALLED = "installed"
    INVALID = "invalid"
    CUSTOM = "custom"


class DownloadState(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ServerStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


# ── Base helpers ─────────────────────────────────────────────────────────────

class _Base:
    """Shared to_dict/from_dict for all schema dataclasses."""

    def to_dict(self) -> dict[str, Any]:
        def convert(v: Any) -> Any:
            if isinstance(v, Enum):
                return v.value
            if dataclasses.is_dataclass(v):
                return {f.name: convert(getattr(v, f.name)) for f in dataclasses.fields(v)}
            if isinstance(v, list):
                return [convert(x) for x in v]
            if isinstance(v, dict):
                return {k: convert(x) for k, x in v.items()}
            return v
        return convert(self)  # type: ignore[arg-type]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Any":
        kwargs: dict[str, Any] = {}
        for f in dataclasses.fields(cls):  # type: ignore[arg-type]
            if f.name not in d:
                continue
            v = d[f.name]
            if isinstance(f.type, type) and issubclass(f.type, Enum):
                v = f.type(v)
            elif f.type in _ENUM_BY_NAME:
                v = _ENUM_BY_NAME[f.type](v)
            kwargs[f.name] = v
        return cls(**kwargs)


# Dataclass field types are strings under `from __future__ import annotations`,
# so enum coercion in from_dict resolves them by name.
_ENUM_BY_NAME = {
    "ModelState": ModelState,
    "RuntimeState": RuntimeState,
    "DownloadState": DownloadState,
    "ServerStatus": ServerStatus,
}


# ── Core data models ─────────────────────────────────────────────────────────

@dataclass
class ModelEntry(_Base):
    id: str
    name: str
    path: str
    size: int = 0          # bytes
    state: ModelState = ModelState.VALID
    enabled: bool = True
    added_at: str = ""
    # GGUF header metadata: arch, quant, params (size label), ctx (trained)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Profile(_Base):
    id: str
    name: str
    model_id: str
    active: bool = False
    route_alias: str = ""   # used as the INI section name / API model name
    params: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class RuntimeEntry(_Base):
    id: str
    name: str
    version: str
    backend: str            # cpu | cuda | vulkan | metal | custom
    path: str               # directory containing llama-server[.exe]
    source: str = "github"  # github | custom
    installed_at: str = ""
    state: RuntimeState = RuntimeState.INSTALLED


@dataclass
class PlaygroundSession(_Base):
    """One saved chat in the Playground. Persisted as JSON under the KV key
    `pg_sessions`; `messages` are plain dicts
    ``{role, content, attachments:[{name, chars}]}``."""
    id: str
    name: str = ""
    created_at: str = ""
    updated_at: str = ""
    system_prompt: str = ""
    model: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DownloadItem(_Base):
    id: str
    kind: str               # model | runtime
    name: str
    url: str
    destination: str
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed_bps: float = 0.0
    state: DownloadState = DownloadState.QUEUED
    error: str = ""
    # Durable instructions for the post-download step (for example runtime
    # extraction).  Unlike an in-memory callback this survives an app restart.
    meta: dict[str, Any] = field(default_factory=dict)


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class ServerSettings(_Base):
    expose: str = "local"      # local | lan | custom — resolved by effective_host()
    host: str = "127.0.0.1"    # only used when expose == "custom"
    port: int = 8080
    max_models: int = 1
    parallel_slots: int = 1
    cpu_threads: int = 8
    batch_threads: int = 8
    api_key: str = ""
    cont_batching: bool = True
    models_autoload: bool = True
    metrics: bool = False
    restart_on_crash: bool = False
    stop_timeout: int = 10
    extra_args: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ServerSettings":
        s: ServerSettings = super().from_dict(d)
        # Configs saved before `expose` existed may carry a hand-set 0.0.0.0 —
        # keep those users on the LAN instead of degrading them to loopback.
        if "expose" not in d and s.host == "0.0.0.0":
            s.expose = "lan"
        return s

    def effective_host(self) -> str:
        return {"local": "127.0.0.1", "lan": "0.0.0.0"}.get(
            self.expose, self.host or "127.0.0.1"
        )


@dataclass
class AppConfig(_Base):
    # UI / appearance
    language: str = "en"
    theme: str = "midnight"
    show_api_details: bool = False

    # Startup behaviour
    autostart_server: bool = False
    minimize_to_tray: bool = False   # Windows only (see services/tray.py)

    # Model sources
    model_folders: list[str] = field(default_factory=list)

    # Server
    server: ServerSettings = field(default_factory=ServerSettings)

    # Global parameters applied to every model in the preset [*] section
    global_params: dict[str, Any] = field(default_factory=dict)

    active_runtime_id: str | None = None

    # Downloads
    max_concurrent_downloads: int = 3

    # Release checks
    auto_check_releases: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AppConfig":
        d = dict(d)
        server = d.pop("server", None)
        cfg: AppConfig = super().from_dict(d)
        if isinstance(server, dict):
            cfg.server = ServerSettings.from_dict(server)
        return cfg
