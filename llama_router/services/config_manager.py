"""App configuration — the ★ configs-manager. Sync port of pi-test's
ConfigService: one AppConfig blob in the SQLite KV store, deep-merged patches,
resets that preserve UI preferences."""
from __future__ import annotations

import logging

from llama_router.core.events import EventBus
from llama_router.core.paths import PathManager
from llama_router.core.secrets import SecretStore
from llama_router.core.storage import db_read, db_write
from llama_router.schemas import AppConfig, ServerSettings

log = logging.getLogger(__name__)


class ConfigManager:
    def __init__(self, paths: PathManager, events: EventBus) -> None:
        self._paths = paths
        self._events = events
        self._cfg = AppConfig()
        self._secrets = SecretStore(paths.config_dir)

    # ── Public API ───────────────────────────────────────────────────────────

    def load(self) -> None:
        raw = db_read(self._paths.db_path, "config", default=None)
        if raw is None:
            self._cfg = AppConfig()
            self.save()          # persist defaults on first run
            log.debug("Config initialised with defaults")
        else:
            try:
                self._cfg = AppConfig.from_dict(raw)
            except Exception:
                log.warning("Config corrupt — resetting to defaults")
                self._cfg = AppConfig()
                self.save()
            log.debug("Config loaded")
        stored_key = self._secrets.read()
        # API keys live only in the platform-backed secret store.  A fresh
        # test installation does not import plaintext values from old data.
        self._cfg.server.api_key = stored_key or ""

    def save(self) -> None:
        self._secrets.write(self._cfg.server.api_key)
        persisted = self._cfg.to_dict()
        persisted["server"]["api_key"] = ""
        db_write(self._paths.db_path, "config", persisted)
        self._events.publish("config_saved", self._cfg.to_dict())

    def get(self) -> AppConfig:
        return self._cfg

    def update(self, patch: dict) -> AppConfig:
        """Merge *patch* into the current config and persist."""
        merged = {**self._cfg.to_dict(), **patch}
        # server sub-object needs deep merge
        if "server" in patch and isinstance(patch["server"], dict):
            merged["server"] = {**self._cfg.server.to_dict(), **patch["server"]}
        updated = AppConfig.from_dict(merged)
        if updated == self._cfg:
            return self._cfg
        self._cfg = updated
        self.save()
        return self._cfg

    def reset_server(self) -> AppConfig:
        """Reset server settings to their schema defaults."""
        merged = self._cfg.to_dict()
        merged["server"] = ServerSettings().to_dict()
        self._cfg = AppConfig.from_dict(merged)
        self.save()
        return self._cfg
