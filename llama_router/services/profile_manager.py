"""Per-model inference profiles — sync port of pi-test's ProfileManager.
Profiles become sections of models-preset.ini via preset.generate()."""
from __future__ import annotations

import logging
import time

from llama_router.core.events import EventBus
from llama_router.core.paths import PathManager
from llama_router.core.storage import db_read, db_write
from llama_router.core.utils import sanitise, uid
from llama_router.schemas import Profile
from llama_router.services.models_manager import ModelsManager

log = logging.getLogger(__name__)

_DEFAULTS = [
    {
        "name": "Default",
        "active": True,
        "params": {"n-gpu-layers": -1, "reasoning": "off"},
    },
    {
        "name": "Agent",
        "active": False,
        "params": {
            "n-gpu-layers": -1,
            "jinja": "true",
            "reasoning": "on",
            "chat-template-kwargs": '{"preserve_thinking": true}',
        },
    },
]


class ProfileManager:
    def __init__(self, paths: PathManager, models: ModelsManager,
                 events: EventBus) -> None:
        self._paths = paths
        self._models = models
        self._events = events
        self._profiles: dict[str, Profile] = {}

    # ── Public API ───────────────────────────────────────────────────────────

    def load(self) -> None:
        rows = db_read(self._paths.db_path, "profiles", default=[])
        self._profiles = {}
        for row in rows:
            try:
                p = Profile.from_dict(row)
                self._profiles[p.id] = p
            except Exception:
                log.warning("Skipping invalid profile entry")
        log.debug("Loaded %d profiles", len(self._profiles))

    def ensure_defaults(self, model_id: str) -> None:
        """Create default profiles for *model_id* if it has none."""
        existing = [p for p in self._profiles.values() if p.model_id == model_id]
        if existing:
            return
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        model = self._models.get(model_id)
        base_alias = sanitise(model.name) if model else model_id[:12]
        for tpl in _DEFAULTS:
            p = Profile(
                id=uid("profile"),
                name=tpl["name"],
                model_id=model_id,
                active=tpl["active"],
                route_alias=base_alias if tpl["name"] == "Default"
                else f"{base_alias}_{sanitise(tpl['name']).lower()}",
                params=dict(tpl["params"]),
                created_at=now,
                updated_at=now,
            )
            self._profiles[p.id] = p
        self._persist()

    @staticmethod
    def template_params(name: str) -> dict:
        """Return a fresh copy of the built-in profile parameters."""
        folded = name.casefold()
        for template in _DEFAULTS:
            if template["name"].casefold() == folded:
                return dict(template["params"])
        return {}

    def list(self, model_id: str | None = None) -> list[Profile]:
        profiles = list(self._profiles.values())
        if model_id:
            profiles = [p for p in profiles if p.model_id == model_id]
        return sorted(profiles, key=lambda p: p.name)

    def get(self, profile_id: str) -> Profile | None:
        return self._profiles.get(profile_id)

    def create(self, model_id: str, name: str, params: dict,
               route_alias: str = "") -> Profile:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        p = Profile(
            id=uid("profile"),
            name=name,
            model_id=model_id,
            active=False,
            route_alias=route_alias,
            params=params,
            created_at=now,
            updated_at=now,
        )
        self._profiles[p.id] = p
        self._persist()
        self._events.publish("profile_created", p.to_dict())
        return p

    def update(self, profile_id: str, patch: dict) -> Profile:
        p = self._require(profile_id)
        data = {**p.to_dict(), **patch,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        previous = p
        self._profiles[profile_id] = Profile.from_dict(data)
        from llama_router.preset import section_conflicts
        conflicts = section_conflicts(self._models.list(), self.by_model())
        if conflicts:
            self._profiles[profile_id] = previous
            raise ValueError("Duplicate active route alias: " +
                             ", ".join(conflicts))
        self._persist()
        self._events.publish("profile_updated", self._profiles[profile_id].to_dict())
        return self._profiles[profile_id]

    def delete(self, profile_id: str) -> None:
        self._require(profile_id)
        del self._profiles[profile_id]
        self._persist()
        self._events.publish("profile_deleted", {"id": profile_id})

    def delete_for_model(self, model_id: str) -> int:
        """Delete every profile belonging to *model_id* (model removed)."""
        doomed = [pid for pid, p in self._profiles.items()
                  if p.model_id == model_id]
        for pid in doomed:
            del self._profiles[pid]
        if doomed:
            self._persist()
            self._events.publish("profiles_reset", {"count": len(doomed)})
        return len(doomed)

    def set_active(self, profile_id: str, active: bool) -> Profile:
        return self.update(profile_id, {"active": active})

    def set_active_all(self, model_id: str | None, active: bool) -> int:
        """Activate/deactivate every profile of *model_id* in one persist.

        With ``model_id=None`` the change applies to every model's profiles.
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        changed = 0
        for p in self._profiles.values():
            if (model_id is None or p.model_id == model_id) \
                    and p.active != active:
                p.active = active
                p.updated_at = now
                changed += 1
        if changed:
            self._persist()
            self._events.publish("profiles_reset", {"count": changed})
        return changed

    def by_model(self) -> dict[str, list[Profile]]:
        """Return profiles grouped by model_id."""
        result: dict[str, list[Profile]] = {}
        for p in self._profiles.values():
            result.setdefault(p.model_id, []).append(p)
        return result

    def apply_preset_params(self, updates: dict[str, dict]) -> int:
        """Replace params for profiles imported from models-preset.ini."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        changed = 0
        for profile_id, params in updates.items():
            profile = self._profiles.get(profile_id)
            if profile is None or profile.params == params:
                continue
            profile.params = dict(params)
            profile.updated_at = now
            changed += 1
        if changed:
            self._persist()
            self._events.publish("preset_imported", {"count": changed})
        return changed

    # ── Internal ─────────────────────────────────────────────────────────────

    def _require(self, profile_id: str) -> Profile:
        p = self._profiles.get(profile_id)
        if not p:
            raise KeyError(f"Profile not found: {profile_id}")
        return p

    def _persist(self) -> None:
        rows = [p.to_dict() for p in self._profiles.values()]
        db_write(self._paths.db_path, "profiles", rows)
