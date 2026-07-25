"""Playground — OpenAI-compatible chat client against the managed llama-server.

Same shape as ServerManager's health loop: urllib in a daemon thread, results
published on the EventBus so the UI thread (App._pump) picks them up.

Events
    pg_token  {"session": int, "text": str}          one streamed delta
    pg_done   {"session": int, "text": str, "stats": dict}
    pg_error  {"session": int, "error": str}

Sessions are persisted in the sqlite KV store under `pg_sessions` as a list of
`PlaygroundSession` dicts.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from llama_router.core.events import EventBus
from llama_router.core.paths import PathManager
from llama_router.core.storage import db_read, db_write
from llama_router.schemas import PlaygroundSession

log = logging.getLogger(__name__)

_KEY = "pg_sessions"
_TIMEOUT = 600.0
_CONNECT_TIMEOUT = 15.0


class PlaygroundService:
    def __init__(self, config, server, profiles, events: EventBus,
                 paths: PathManager) -> None:
        self._config = config
        self._server = server
        self._profiles = profiles
        self._events = events
        self._paths = paths
        # Bumped on every send/cancel — a worker whose id no longer matches
        # has been superseded and must publish nothing (ServerManager pattern).
        self._session = 0
        self._resp: Any = None
        self._resp_lock = threading.Lock()
        self._request_gate = threading.Lock()

    # ── Models ───────────────────────────────────────────────────────────────

    def models(self) -> list[str]:
        """Route names the server can serve, newest health probe first."""
        loaded = [m for m in getattr(self._server, "_loaded_models", []) or [] if m]
        if loaded:
            return loaded
        return sorted({p.route_alias or p.name
                       for plist in self._profiles.by_model().values()
                       for p in plist if p.active and (p.route_alias or p.name)})

    # ── Streaming ────────────────────────────────────────────────────────────

    @property
    def streaming(self) -> bool:
        with self._resp_lock:
            return self._resp is not None

    def send(self, messages: list[dict], model: str,
             params: dict | None = None) -> int:
        """Start a streamed completion. Returns the worker's session id."""
        self.cancel()
        session = self._session
        threading.Thread(
            target=self._work, args=(session, messages, model, params or {}),
            daemon=True, name="playground").start()
        return session

    def cancel(self) -> None:
        """Detach the current worker and drop its connection."""
        with self._resp_lock:
            self._session += 1
            current, self._resp = self._resp, None
        resp = current[1] if current is not None else None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def _work(self, session: int, messages: list[dict], model: str,
              params: dict) -> None:
        with self._request_gate:
            if session != self._session:
                return
            self._work_locked(session, messages, model, params)

    def _work_locked(self, session: int, messages: list[dict], model: str,
                     params: dict) -> None:
        body: dict[str, Any] = {"model": model, "messages": messages,
                                "stream": True}
        body.update(params)
        req = urllib.request.Request(
            f"{self._server.base_url()}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        key = self._config.get().server.api_key
        if key:
            req.add_header("Authorization", f"Bearer {key}")

        parts: list[str] = []
        stats: dict[str, Any] = {}
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=_CONNECT_TIMEOUT) as resp:
                try:
                    resp.fp.raw._sock.settimeout(_TIMEOUT)
                except (AttributeError, OSError):
                    pass
                with self._resp_lock:
                    if session != self._session:
                        return
                    self._resp = (session, resp)
                for raw in resp:
                    if session != self._session:
                        return          # cancelled or superseded
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        continue
                    for choice in chunk.get("choices") or ():
                        delta = (choice.get("delta") or {}).get("content") or ""
                        if delta:
                            parts.append(delta)
                            self._events.publish(
                                "pg_token", {"session": session, "text": delta})
                    for k in ("timings", "usage"):
                        if isinstance(chunk.get(k), dict):
                            stats[k] = chunk[k]
        except Exception as e:
            if session == self._session:
                log.info("playground request failed: %s", e)
                self._events.publish("pg_error",
                                     {"session": session, "error": _describe(e)})
            return
        finally:
            with self._resp_lock:
                if self._resp is not None and self._resp[0] == session:
                    self._resp = None

        if session != self._session:
            return
        self._events.publish("pg_done", {
            "session": session,
            "text": "".join(parts),
            "stats": _summarise(stats, len(parts), time.monotonic() - started),
        })

    # ── Session persistence ──────────────────────────────────────────────────

    def new_session(self, name: str = "", model: str = "") -> dict:
        now = time.strftime("%Y-%m-%d %H:%M")
        return PlaygroundSession(id=uuid.uuid4().hex[:12], name=name or now,
                                 created_at=now, updated_at=now,
                                 model=model).to_dict()

    def sessions(self) -> list[dict]:
        rows = db_read(self._paths.db_path, _KEY, default=[]) or []
        out = []
        for r in rows:
            try:
                out.append(PlaygroundSession.from_dict(r).to_dict())
            except Exception:
                continue
        return out

    def save_session(self, data: dict) -> None:
        data = dict(data)
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
        rows = [s for s in self.sessions() if s["id"] != data.get("id")]
        rows.insert(0, data)
        db_write(self._paths.db_path, _KEY, rows[:100])

    def delete_session(self, session_id: str) -> None:
        db_write(self._paths.db_path, _KEY,
                 [s for s in self.sessions() if s["id"] != session_id])


def _describe(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = json.loads(e.read().decode("utf-8"))
            msg = (detail.get("error") or {}).get("message")
            if msg:
                return f"HTTP {e.code}: {msg}"
        except Exception:
            pass
        return f"HTTP {e.code}"
    return str(e) or e.__class__.__name__


def _summarise(stats: dict, chunks: int, elapsed: float) -> dict:
    """Normalise llama.cpp `timings`/OpenAI `usage` into tokens + tokens/s.

    Neither is guaranteed to be present in a streamed response, so fall back
    to counting deltas over wall-clock time.
    """
    timings = stats.get("timings") or {}
    usage = stats.get("usage") or {}
    tokens = (timings.get("predicted_n")
              or usage.get("completion_tokens") or chunks)
    tps = timings.get("predicted_per_second")
    if not tps and elapsed > 0:
        tps = tokens / elapsed
    return {"tokens": int(tokens), "tps": float(tps or 0.0),
            "elapsed": elapsed}
