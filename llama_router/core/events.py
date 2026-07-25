"""Thread-safe pub/sub bus for a Tk application.

Worker threads (downloads, health checks, log readers) call `publish()` from
any thread; events land in an internal queue. The Tk mainloop calls `drain()`
periodically (via `root.after`) so every subscriber callback runs on the UI
thread and may touch widgets freely.
"""
from __future__ import annotations

import logging
import queue
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._subs: dict[str, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Any], None]) -> None:
        """Register *handler* for *event_type*. Call from the UI thread only."""
        self._subs[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable[[Any], None]) -> None:
        try:
            self._subs[event_type].remove(handler)
        except ValueError:
            pass

    def publish(self, event_type: str, data: Any = None) -> None:
        """Queue an event. Safe from any thread; never blocks."""
        self._q.put((event_type, data))

    def drain(self, max_events: int = 200) -> int:
        """Dispatch queued events to subscribers on the calling (UI) thread.

        Bounded per call so a flood can't freeze a frame; leftovers are picked
        up on the next tick. Returns the number of events dispatched.
        """
        count = 0
        while count < max_events:
            try:
                event_type, data = self._q.get_nowait()
            except queue.Empty:
                break
            count += 1
            for handler in list(self._subs.get(event_type, ())):
                try:
                    handler(data)
                except Exception:
                    log.exception("Event handler error [%s]", event_type)
        return count
