"""Shared page scaffolding."""
from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

from llama_router.ui.widgets import PageHeader

PAGE_PAD = 26   # outer gutter for every page


class Page(tk.Frame):
    """Base page: dark background + standard gutter. Subclasses build content.

    Every event subscription and every `after` timer a page schedules is
    tracked so it can be torn down cleanly when the page is rebuilt (theme
    switch). Without this, destroyed widgets keep firing handlers, which
    both floods the log with TclErrors and — because handlers accumulate on
    every rebuild — progressively slows the app down.
    """

    def __init__(self, parent: tk.Widget, ctx) -> None:
        super().__init__(parent, bg=ctx.colors["bg"])
        self.ctx = ctx
        self.c = ctx.colors
        self._subs: list[tuple[str, Callable[[Any], None]]] = []
        self._after_ids: list[str] = []

    def subscribe(self, event: str, handler: Callable[[Any], None]) \
            -> Callable[[Any], None]:
        """Subscribe to an event and remember it for teardown()."""
        self.ctx.events.subscribe(event, handler)
        self._subs.append((event, handler))
        return handler

    def after(self, ms, func=None, *args):  # type: ignore[override]
        """Track scheduled callbacks so teardown() can cancel them."""
        if func is None:
            return super().after(ms)
        aid = super().after(ms, func, *args)
        self._after_ids.append(aid)
        return aid

    def teardown(self) -> None:
        """Drop every subscription + pending timer so a destroyed page stops
        reacting to events. Safe to call once."""
        for event, handler in self._subs:
            self.ctx.events.unsubscribe(event, handler)
        self._subs.clear()
        for aid in self._after_ids:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        self._after_ids.clear()

    def header(self, eyebrow: str, title: str, subtitle: str = "") -> PageHeader:
        h = PageHeader(self, self.c, eyebrow, title, subtitle)
        h.pack(fill="x", padx=PAGE_PAD, pady=(22, 16))
        return h

    def on_show(self) -> None:
        """Called every time the page becomes visible."""
