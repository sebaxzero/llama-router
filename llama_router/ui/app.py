"""Root window: nav rail + page container + status bar.

The shell is deliberately quiet — one amber accent on the active nav item and
the primary action; everything else is graphite. Pages live in ui/pages/ and
replace their placeholders phase by phase.
"""
from __future__ import annotations

import logging
import time
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk
from typing import Any, Callable

from llama_router import __version__
from llama_router.core.events import EventBus
from llama_router.i18n import LANGUAGES, set_language, t
from llama_router.core.logs import LogService
from llama_router.core.paths import PathManager
from llama_router.core.paths import asset_path
from llama_router.core.windows import configure_app_identity
from llama_router.ui import theme
from llama_router.ui.pages.dashboard import DashboardPage
from llama_router.ui.pages.playground import PlaygroundPage
from llama_router.ui.pages.profiles import ProfilesPage
from llama_router.ui.pages.runtime import RuntimePage
from llama_router.ui.pages.settings import SettingsPage
from llama_router.ui.widgets import (AppMark, NavItem, ScrollFrame, StatusDot,
                                     Tooltip, status_label)

_DRAIN_MS = 100          # EventBus drain cadence
_STATE_KEY = "ui_state"  # KV key for the persisted active page
_DEFAULT_GEOMETRY = "960x640"  # fixed startup size (matches tools/screenshots.py)
_PREWARM_START_MS = 400  # first page is already painted before hidden work
_STATUS_CONTEXT_EVENTS = (
    "runtime_activated", "runtime_added", "runtime_deleted",
    "model_updated", "model_removed", "models_scanned",
    "profile_created", "profile_updated", "profile_deleted",
    "profiles_reset", "preset_imported",
)

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Everything a page needs, in one bag. Services join in later phases."""
    paths: PathManager
    events: EventBus
    logs: LogService
    colors: dict[str, str]
    services: dict[str, Any]
    enable_tray: bool = True
    collapsible_states: dict[str, bool] = field(default_factory=dict)
    # Wired by App.__init__ immediately after construction — never call these
    # before the App exists.
    navigate: Callable[[str], None] = field(init=False)
    apply_theme: Callable[[str], None] = field(init=False)
    apply_language: Callable[[str], None] = field(init=False)


# nav key → (label key, page class).
# Labels are English catalog keys; t() resolves them when the rail is built.
PAGES: dict[str, tuple[str, type]] = {
    "dashboard":  ("Dashboard",  DashboardPage),
    "playground": ("Playground", PlaygroundPage),
    "profiles":  ("Models & Profiles", ProfilesPage),
    "runtime":   ("Runtime",   RuntimePage),
    "settings":  ("Settings",  SettingsPage),
}

class App:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        theme.set_dpi_aware()
        configure_app_identity(asset_path("app_icon.ico"))

        self.root = tk.Tk()
        self.root.title("Llama Router")
        self.root.minsize(760, 500)

        # Set app window icon
        icon_path = asset_path("app_icon.png")
        if icon_path.exists():
            try:
                self._icon_img = tk.PhotoImage(file=str(icon_path))
                self.root.iconphoto(True, self._icon_img)
            except Exception:
                pass

        ctx.navigate = self.show_page
        ctx.apply_theme = self.apply_theme
        ctx.apply_language = self.apply_language
        self._clock_id: int | None = None
        self._resize_id: str | None = None
        self._prewarm_id: str | None = None
        self._prewarm_queue: list[str] = []
        self._page_build_ms: dict[str, float] = {}
        self._nav_hbar_visible = False
        self._rebuilding = False   # guards resize handlers during theme teardown
        self._closing = False
        self._last_nav_focus: tk.Widget | None = None

        from llama_router.core.storage import db_read
        state = db_read(ctx.paths.db_path, _STATE_KEY, default={}) or {}
        saved_cards = state.get("collapsible_cards", {})
        ctx.collapsible_states = (saved_cards if isinstance(saved_cards, dict)
                                  else {})
        self.root.geometry(_DEFAULT_GEOMETRY)  # always open at the reference size

        theme_name = self._current_theme_name()
        ctx.colors.update(theme.apply(self.root, theme_name))
        theme.enable_windows_niceties(self.root, dark=theme.is_dark(theme_name))

        # ── Layout: header / tab bar above, content, status bar below ───────
        self._build_chrome()
        self._build_content(state.get("page", "dashboard"))

        # ── Event pump + lifecycle ───────────────────────────────────────────
        self.root.after(_DRAIN_MS, self._pump)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Keep chrome fluid as the window is resized (header + tab strip).
        self.root.bind("<Configure>", self._on_resize)
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.bind("<Map>", self._on_map)
        self._bind_shortcuts()

        # ── System tray (Windows, opt-in) ────────────────────────────────────
        # The tray lives outside the widget tree, so theme flips never touch
        # it; it is created lazily on the first minimize and torn down in
        # _on_close. Its events arrive through the same pump as everything.
        self._tray = None
        from llama_router.services import tray as _tray_mod
        if ctx.enable_tray and _tray_mod.is_supported():
            ctx.events.subscribe("tray_restore", self._on_tray_restore)
            ctx.events.subscribe("tray_start", self._on_tray_start)
            ctx.events.subscribe("tray_stop", self._on_tray_stop)
            ctx.events.subscribe("tray_restart", self._on_tray_restart)
            ctx.events.subscribe("tray_quit", lambda _d: self._on_close())
            self._ensure_tray()

    # ── Theme ───────────────────────────────────────────────────────────────

    def _current_theme_name(self) -> str:
        cfg = self.ctx.services.get("config")
        name = cfg.get().theme if cfg else "midnight"
        return name if name in theme.THEMES else "midnight"

    def apply_theme(self, name: str) -> None:
        """Switch themes live: repaint ttk styles and rebuild chrome + pages.

        Custom widgets snapshot their colours from the token dict at build
        time, so the only reliable way to recolour them is to rebuild. Pages
        are recreated lazily from `ctx.colors`, which is updated in place.
        """
        if name not in theme.THEMES:
            return
        cfg = self.ctx.services.get("config")
        if cfg is not None:
            cfg.update({"theme": name})
        self._rebuild_ui(name)

    def apply_language(self, language: str) -> None:
        """Switch the UI language immediately and persist the selection."""
        if language not in LANGUAGES:
            return
        cfg = self.ctx.services.get("config")
        if cfg is not None:
            cfg.update({"language": language})
        set_language(language)
        self._rebuild_ui(self._current_theme_name())

    def _rebuild_ui(self, theme_name: str) -> None:
        """Rebuild chrome and pages after a live appearance change."""
        self._rebuilding = True
        prev = self._active
        self._cancel_prewarm()
        if self._resize_id is not None:
            self.root.after_cancel(self._resize_id)
            self._resize_id = None

        # Snapshot the active page's unsaved input so a theme flip can't wipe
        # it (the picker lives on Settings, which rebuilds on theme change).
        draft = None
        cur = self._pages.get(prev) if prev else None
        if cur is not None and hasattr(cur, "_serialize"):
            try:
                draft = cur._serialize()
            except Exception:
                draft = None

        # Tear down: drop chrome subscriptions + the per-second clock timer.
        self.ctx.events.unsubscribe("server_status", self._on_server_status)
        for event in _STATUS_CONTEXT_EVENTS:
            self.ctx.events.unsubscribe(event, self._update_status_context)
        if self._clock_id is not None:
            self.root.after_cancel(self._clock_id)
            self._clock_id = None
        # Drop every page's event subscriptions + pending timers so destroyed
        # widgets stop firing handlers. Without this, handlers accumulate on
        # every theme flip — that's what slowed switches and flooded the log
        # with TclErrors.
        for p in self._pages.values():
            if hasattr(p, "teardown"):
                try:
                    p.teardown()
                except Exception:
                    pass
        for w in self.root.winfo_children():
            w.destroy()

        # Repaint and rebuild from the fresh palette.
        self.ctx.colors.clear()
        self.ctx.colors.update(theme.apply(self.root, theme_name))
        theme.enable_windows_niceties(
            self.root, dark=theme.is_dark(theme_name))
        self._build_chrome()
        self._build_content(prev or "dashboard")

        # Restore any unsaved input captured above.
        if draft is not None:
            restored = self._pages.get(prev)
            if restored is not None and hasattr(restored, "_restore"):
                try:
                    restored._restore(draft)
                except Exception:
                    pass

        # Re-sync the server heartbeat so the dot isn't stuck on 'stopped'.
        server = self.ctx.services.get("server")
        if server is not None:
            status = getattr(server, "status", "stopped")
            self._on_server_status({"status": status})
        self._rebuilding = False

    # ── Header + tab bar ─────────────────────────────────────────────────────

    def _build_header(self) -> None:
        c = self.ctx.colors
        head = tk.Frame(self.root, bg=c["bg"])
        head.pack(fill="x", padx=24, pady=(16, 10))

        self._head_mark = AppMark(head, c, size=48)
        self._head_mark.pack(side="left")

        name = tk.Frame(head, bg=c["bg"])
        name.pack(side="left", padx=(12, 0))
        title = tk.Frame(name, bg=c["bg"])
        title.pack(anchor="w")
        tk.Label(title, text=theme.track("LLAMA"), bg=c["bg"], fg=c["title"],
                 font=theme.mono(15, "bold")).pack(side="left")
        tk.Label(title, text=theme.track("ROUTER"), bg=c["bg"], fg=c["accent"],
                 font=theme.mono(15, "bold")).pack(side="left", padx=(6, 0))
        sub = tk.Frame(name, bg=c["bg"])
        sub.pack(anchor="w", pady=(1, 0))
        self._head_sub = sub
        self._head_dot = tk.Label(sub, text="●", bg=c["bg"], fg=c["faint"],
                                  font=theme.mono(7))
        self._head_dot.pack(side="left")
        tk.Label(sub,
                 text=theme.track(t("a control panel for llama.cpp")
                                  + f" · v{__version__}"),
                 bg=c["bg"], fg=c["faint"],
                 font=theme.mono(7, "bold")).pack(side="left", padx=(6, 0))

        self._clock = tk.Label(head, text="", bg=c["bg"], fg=c["muted"],
                               font=theme.mono(9, "bold"))
        self._clock.pack(side="right", anchor="n", pady=2)
        self._tick_clock()

    def _relayout_chrome(self) -> None:
        """As the window narrows, drop the subtitle then the clock so the
        header never clips the logo + title (and nothing silently vanishes)."""
        if not getattr(self, "_head_sub", None) or not self._head_sub.winfo_exists():
            return
        if not self._clock.winfo_exists():
            return
        w = self.root.winfo_width()
        if w < 560:
            self._head_sub.pack_forget()
            self._clock.pack_forget()
        elif w < 700:
            self._head_sub.pack_forget()
            self._clock.pack(side="right", anchor="n", pady=2)
        else:
            self._head_sub.pack(anchor="w", pady=(1, 0))
            self._clock.pack(side="right", anchor="n", pady=2)

    def _tick_clock(self) -> None:
        import time
        self._clock.configure(
            text=theme.track(time.strftime("%H:%M:%S") + " local"))
        self._clock_id = self.root.after(1000, self._tick_clock)

    def _build_tabs(self) -> None:
        c = self.ctx.colors
        bar = tk.Frame(self.root, bg=c["bg"])
        bar.pack(fill="x", padx=24)

        # Tabs live on a canvas so the strip can scroll horizontally instead
        # of clipping the last tabs when the window is too narrow to fit them.
        self._nav_canvas = tk.Canvas(bar, bg=c["bg"], highlightthickness=0, bd=0)
        self._nav_frame = tk.Frame(self._nav_canvas, bg=c["bg"])
        self._nav_canvas.create_window(0, 0, window=self._nav_frame, anchor="nw")
        self._nav_hbar = ttk.Scrollbar(bar, orient="horizontal",
                                       command=self._nav_canvas.xview)
        self._nav_canvas.configure(xscrollcommand=self._nav_hbar.set)
        self._nav_canvas.pack(fill="x", side="top")
        self._nav_hbar.pack_forget()

        self._nav: dict[str, NavItem] = {}
        for key, (label, _cls) in PAGES.items():
            item = NavItem(self._nav_frame, c, t(label),
                           command=lambda k=key: self.show_page(k))
            item.pack(side="left", padx=(0, 4))
            self._nav[key] = item

        self._nav_frame.bind(
            "<Configure>",
            lambda _e: self._nav_canvas.configure(
                scrollregion=self._nav_canvas.bbox("all")))
        self._nav_canvas.bind("<Configure>", self._on_nav_resize)

        tk.Frame(self.root, bg=c["border"], height=1).pack(fill="x")
        self._on_nav_resize()

    def _on_nav_resize(self, _e=None) -> None:
        """Keep the nav one row tall; reveal a scrollbar only when the tabs
        don't all fit, so no tab is ever hidden."""
        if not getattr(self, "_nav_canvas", None) or not self._nav_canvas.winfo_exists():
            return
        self._nav_canvas.configure(height=self._nav_frame.winfo_reqheight())
        self._nav_canvas.configure(scrollregion=self._nav_canvas.bbox("all"))
        overflow = self._nav_frame.winfo_reqwidth() > self._nav_canvas.winfo_width()
        if overflow and not self._nav_hbar_visible:
            self._nav_hbar.pack(fill="x", side="top")
            self._nav_hbar_visible = True
        elif not overflow and self._nav_hbar_visible:
            self._nav_hbar.pack_forget()
            self._nav_hbar_visible = False

    def _build_chrome(self) -> None:
        """Header + tab rail + status bar. Safe to call after a full teardown."""
        self._statusbar()
        self._build_header()
        self._build_tabs()
        self._relayout_chrome()

    def _on_resize(self, _e=None) -> None:
        """Window-level resize hook: keep header + tab strip fluid.

        Skipped while a theme flip is rebuilding the chrome — otherwise a
        stray <Configure> between teardown and rebuild would touch destroyed
        widgets and raise TclError.
        """
        if getattr(self, "_rebuilding", False):
            return
        if self._resize_id is not None:
            self.root.after_cancel(self._resize_id)
        self._resize_id = self.root.after(25, self._apply_resize)

    def _apply_resize(self) -> None:
        self._resize_id = None
        if self._rebuilding or self._closing:
            return
        self._relayout_chrome()
        self._on_nav_resize()

    def _scroll_to_nav(self, key: str) -> None:
        """Bring the active tab into view when the strip is scrolled."""
        if self._active != key:
            return
        item = self._nav.get(key)
        if not item or not getattr(self, "_nav_canvas", None):
            return
        fw = self._nav_frame.winfo_width()
        if fw <= self._nav_canvas.winfo_width():
            return
        x = item.winfo_x()
        frac = (x + item.winfo_width() / 2
                - self._nav_canvas.winfo_width() / 2) / fw
        self._nav_canvas.xview_moveto(max(0.0, min(1.0, frac)))

    def _build_content(self, active_key: str = "dashboard") -> None:
        """Page container + first page. Safe to call after a full teardown."""
        c = self.ctx.colors
        self.content = tk.Frame(self.root, bg=c["bg"])
        self.content.pack(fill="both", expand=True)
        self._pages = {}
        self._active = None
        self.show_page(active_key)
        self._schedule_prewarm(_PREWARM_START_MS)

    def _bind_shortcuts(self) -> None:
        """Small, discoverable keyboard layer for the desktop shell."""
        keys = tuple(PAGES)
        for index, key in enumerate(keys, 1):
            self.root.bind(f"<Control-Key-{index}>",
                           lambda _e, k=key: self.show_page(k))
        self.root.bind("<Control-comma>",
                       lambda _e: self.show_page("settings"))
        self.root.bind("<Control-Tab>",
                       lambda _e: self._cycle_page(1))
        self.root.bind("<Control-Shift-Tab>",
                       lambda _e: self._cycle_page(-1))
        self.root.bind("<Tab>", lambda _e: self._focus_step(1))
        self.root.bind("<Shift-Tab>", lambda _e: self._focus_step(-1))
        self.root.bind("<Escape>", self._restore_navigation_focus)
        self.root.bind_all("<FocusIn>", self._remember_navigation_focus,
                           add="+")
        for sequence, direction in (
            ("<Left>", (-1, 0)), ("<Right>", (1, 0)),
            ("<Up>", (0, -1)), ("<Down>", (0, 1)),
        ):
            self.root.bind(
                sequence,
                lambda _e, d=direction: self._start_direction(*d))

        # Text's class binding consumes Tab before the toplevel sees it.
        # Override only that class so multiline editors join normal app
        # navigation instead of inserting whitespace.
        for sequence, action in (
            ("<Tab>", lambda _e: self._focus_step(1)),
            ("<Shift-Tab>", lambda _e: self._focus_step(-1)),
            ("<Control-Tab>", lambda _e: self._cycle_page(1)),
            ("<Control-Shift-Tab>", lambda _e: self._cycle_page(-1)),
        ):
            self.root.bind_class("Text", sequence, action)
        for widget_class in ("Text", "Entry", "TEntry", "TCombobox"):
            self.root.bind_class(widget_class, "<Escape>",
                                 self._restore_navigation_focus)

        # Only our hand-drawn controls are focusable Canvas/Frame widgets.
        # Inputs and tables keep their native arrow-key behaviour.
        for widget_class in ("Canvas", "Frame"):
            for sequence, direction in (
                ("<Left>", (-1, 0)), ("<Right>", (1, 0)),
                ("<Up>", (0, -1)), ("<Down>", (0, 1)),
            ):
                self.root.bind_class(
                    widget_class, sequence,
                    lambda _e, d=direction: self._focus_direction(*d))
        # Single-line fields behave as cells in the control panel: arrows
        # leave them spatially. Multiline Text and Treeview keep native arrows.
        for widget_class in ("Entry", "TEntry", "TCombobox", "TCheckbutton"):
            for sequence, direction in (
                ("<Left>", (-1, 0)), ("<Right>", (1, 0)),
                ("<Up>", (0, -1)), ("<Down>", (0, 1)),
            ):
                self.root.bind_class(
                    widget_class, sequence,
                    lambda _e, d=direction: self._focus_direction(*d))
        self.root.bind_class("TCombobox", "<Key-space>",
                             self._open_combobox)
        self.root.bind_class("TCombobox", "<Key-Return>",
                             self._open_combobox)
        self.root.bind_class("Treeview", "<FocusIn>",
                             self._prepare_treeview, add="+")
        self.root.bind_class("Treeview", "<Key-space>",
                             self._select_treeview_item)
        self.root.bind_class("Treeview", "<Key-Return>",
                             self._select_treeview_item)
        self.root.bind_class("Treeview", "<Key-Up>",
                             lambda e: self._move_treeview(e, -1))
        self.root.bind_class("Treeview", "<Key-Down>",
                             lambda e: self._move_treeview(e, 1))

    @staticmethod
    def _open_combobox(event) -> str:
        """Open a focused ttk Combobox without requiring the mouse."""
        try:
            event.widget.tk.call("ttk::combobox::Post", event.widget._w)
        except tk.TclError:
            pass
        return "break"

    def _prepare_treeview(self, event) -> None:
        tree = event.widget
        self.root.after_idle(lambda: self._select_treeview_item_for(tree))

    def _select_treeview_item(self, event) -> str:
        self._select_treeview_item_for(event.widget)
        return "break"

    @staticmethod
    def _select_treeview_item_for(tree: ttk.Treeview) -> None:
        """Ensure a keyboard-focused table has one usable active row."""
        try:
            item = tree.focus()
            if not item:
                rows = tree.get_children()
                item = rows[0] if rows else ""
            if item:
                tree.focus(item)
                tree.selection_set(item)
                tree.see(item)
        except tk.TclError:
            pass

    def _move_treeview(self, event, step: int) -> str:
        """Move within a table, then leave it at the first/last row."""
        tree = event.widget
        try:
            rows: list[str] = []

            def append_visible(parent: str = "") -> None:
                for item in tree.get_children(parent):
                    rows.append(item)
                    if tree.item(item, "open"):
                        append_visible(item)

            append_visible()
            current = tree.focus()
            if not rows:
                return self._focus_direction(0, step)
            index = rows.index(current) if current in rows else (
                -1 if step > 0 else len(rows))
            target = index + step
            if 0 <= target < len(rows):
                item = rows[target]
                tree.focus(item)
                tree.selection_set(item)
                tree.see(item)
            else:
                return self._focus_direction(0, step)
        except tk.TclError:
            pass
        return "break"

    def _remember_navigation_focus(self, event) -> None:
        if getattr(event.widget, "_keyboard_nav", False):
            self._last_nav_focus = event.widget
        self.root.after_idle(lambda w=event.widget: self._reveal_focus(w))

    @staticmethod
    def _reveal_focus(widget: tk.Widget) -> None:
        """Reveal a focused control through every nested scroll container."""
        target = widget
        owner = getattr(widget, "master", None)
        while owner is not None:
            if isinstance(owner, ScrollFrame):
                owner.see(target)
                target = owner
            owner = getattr(owner, "master", None)

    def _restore_navigation_focus(self, _event=None) -> str:
        """Leave an editor and return to the last button/tab used."""
        target = self._last_nav_focus
        try:
            if target is not None and target in self._focus_widgets():
                self._focus_widget(target)
                return "break"
        except tk.TclError:
            pass
        for widget in self._focus_widgets():
            if getattr(widget, "_keyboard_nav", False):
                self._focus_widget(widget)
                self._last_nav_focus = widget
                break
        return "break"

    def _focus_widgets(self) -> list[tk.Widget]:
        """Return visible, useful controls; skip Tk's phantom focus stops."""
        widgets: list[tk.Widget] = []
        inputs = (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox,
                  ttk.Checkbutton, ttk.Treeview)
        page = self._pages.get(self._active) if self._active else None
        if page is None:
            return widgets

        def managed(widget: tk.Widget) -> bool:
            current = widget
            while current is not page:
                try:
                    if not current.winfo_manager():
                        return False
                except tk.TclError:
                    return False
                current = getattr(current, "master", None)
                if current is None:
                    return False
            return True

        def visit(parent: tk.Widget) -> None:
            for widget in parent.winfo_children():
                visit(widget)
                if not (getattr(widget, "_keyboard_nav", False)
                        or isinstance(widget, inputs)):
                    continue
                if getattr(widget, "_enabled", True) is False:
                    continue
                try:
                    if not managed(widget):
                        continue
                    if isinstance(widget, ttk.Widget):
                        if widget.instate(["disabled"]):
                            continue
                except (KeyError, tk.TclError):
                    continue
                if not isinstance(widget, ttk.Widget):
                    try:
                        if str(widget.cget("state")) == "disabled":
                            continue
                    except tk.TclError:
                        pass
                widgets.append(widget)

        try:
            visit(page)
        except tk.TclError:
            return []
        return widgets

    def _focus_step(self, step: int) -> str:
        """Move focus in visual reading order, not Tk creation order."""
        widgets = self._focus_widgets()
        if not widgets:
            return "break"
        widgets.sort(key=lambda w: self._widget_rect(w)[1::-1])
        focused = self.root.focus_get()
        index = widgets.index(focused) if focused in widgets else (-1 if step > 0 else 0)
        self._focus_widget(widgets[(index + step) % len(widgets)])
        return "break"

    def _focus_widget(self, widget: tk.Widget) -> None:
        """Reveal an offscreen control before asking Tk to focus it."""
        self._reveal_focus(widget)
        try:
            self.root.update_idletasks()
            widget.focus_set()
        except tk.TclError:
            pass

    def _focus_first_action(self, page_key: str | None = None) -> str:
        """Focus the first visible button on the current page."""
        if page_key is not None and page_key != self._active:
            return "break"
        actions = [w for w in self._focus_widgets()
                   if getattr(w, "_keyboard_nav", False)]
        if actions:
            actions.sort(key=lambda w: self._widget_rect(w)[1::-1])
            self._focus_widget(actions[0])
        return "break"

    def _start_direction(self, dx: int, dy: int) -> str | None:
        """Let an arrow start navigation when no visible control has focus."""
        if self.root.focus_get() not in self._focus_widgets():
            return self._focus_first_action()
        return None

    def _focus_direction(self, dx: int, dy: int) -> str:
        """Move through the visual row/column containing the focused control."""
        focused = self.root.focus_get()
        widgets = self._focus_widgets()
        if focused not in widgets:
            return self._focus_step(1)
        left, top, right, bottom = self._widget_rect(focused)
        x = (left + right) / 2
        y = (top + bottom) / 2
        choices = []
        for widget in widgets:
            if widget is focused:
                continue
            wl, wt, wr, wb = self._widget_rect(widget)
            wx = (wl + wr) / 2
            wy = (wt + wb) / 2
            forward = (wx - x) * dx + (wy - y) * dy
            if forward <= 0:
                continue
            if dx:
                aligned = min(bottom, wb) > max(top, wt)
                gap = max(0, wl - right) if dx > 0 else max(0, left - wr)
                sideways = abs(wy - y)
            else:
                aligned = min(right, wr) > max(left, wl)
                gap = max(0, wt - bottom) if dy > 0 else max(0, top - wb)
                sideways = abs(wx - x)
            choices.append((0 if aligned else 1, gap, sideways, widget))
        if choices:
            self._focus_widget(min(choices, key=lambda item: item[:3])[3])
        return "break"

    def _widget_rect(self, widget: tk.Widget) -> tuple[int, int, int, int]:
        """Page-local geometry that remains valid for offscreen widgets."""
        x = y = 0
        current: tk.Widget | None = widget
        page = self._pages.get(self._active) if self._active else None
        while current is not None and current is not page:
            try:
                x += current.winfo_x()
                y += current.winfo_y()
            except tk.TclError:
                break
            current = getattr(current, "master", None)
        return x, y, x + widget.winfo_width(), y + widget.winfo_height()

    def _cycle_page(self, step: int) -> str:
        keys = tuple(PAGES)
        current = keys.index(self._active) if self._active in keys else 0
        self.show_page(keys[(current + step) % len(keys)])
        return "break"

    def _create_page(self, key: str):
        """Build and cache one page without making it visible."""
        started = time.perf_counter()
        cls = PAGES[key][1]
        page = cls(self.content, self.ctx)
        self._pages[key] = page
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._page_build_ms[key] = elapsed_ms
        log.info("Built %s page in %.1f ms", key, elapsed_ms)
        return page

    def _schedule_prewarm(self, delay: int = 200) -> None:
        self._cancel_prewarm()
        priority = ("profiles", "playground", "runtime", "settings",
                    "dashboard")
        self._prewarm_queue = [key for key in priority
                               if key not in self._pages]
        if self._prewarm_queue:
            self._prewarm_id = self.root.after(delay, self._prewarm_next)

    def _prewarm_next(self) -> None:
        self._prewarm_id = None
        if not self._prewarm_queue or self._closing or self._rebuilding:
            return
        key = self._prewarm_queue.pop(0)
        if key not in self._pages:
            self._create_page(key)
        if self._prewarm_queue:
            # Give Tk at least one short interaction window; expensive pages
            # earn proportionally more recovery time before the next build.
            pause = max(40, min(200, round(self._page_build_ms.get(key, 40))))
            self._prewarm_id = self.root.after(pause, self._prewarm_next)

    def _cancel_prewarm(self) -> None:
        if self._prewarm_id is not None:
            try:
                self.root.after_cancel(self._prewarm_id)
            except Exception:
                pass
        self._prewarm_id = None
        self._prewarm_queue = []

    # ── Status bar ───────────────────────────────────────────────────────────

    def _statusbar(self) -> None:
        c = self.ctx.colors
        bar = tk.Frame(self.root, bg=c["bg"], height=30)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(self.root, bg=c["border"], height=1).pack(side="bottom", fill="x")

        self._sb_dot = StatusDot(bar, c, size=12)
        self._sb_dot.pack(side="left", padx=(14, 6), pady=9)
        self._sb_text = tk.Label(bar, text="server · stopped", bg=c["bg"],
                                 fg=c["muted"], font=theme.mono(8))
        self._sb_text.pack(side="left")

        self._sb_right = tk.Label(bar, text="", bg=c["bg"], fg=c["faint"],
                                  font=theme.mono(8))
        self._sb_right.pack(side="right", padx=14)
        Tooltip(self._sb_right, c,
                t("Active runtime and routes available to llama-server."))

        self.ctx.events.subscribe("server_status", self._on_server_status)
        for event in _STATUS_CONTEXT_EVENTS:
            self.ctx.events.subscribe(event, self._update_status_context)
        self._last_server_status = self._server_status()
        self._update_status_context()

    def _on_server_status(self, data: dict) -> None:
        c = self.ctx.colors
        status = (data or {}).get("status", "stopped")
        # Most publishers send ServerStatus.value, but apply_theme() re-syncs
        # from `server.status`, which is the enum itself. Since Python 3.11 an
        # f-string on a str-mixin enum renders "ServerStatus.STOPPED" instead
        # of "stopped", so normalise here — the one place every path converges.
        status = getattr(status, "value", status)
        self._last_server_status = status
        self._sb_dot.set(status)
        self._sb_text.configure(text=f"server · {status_label(status).lower()}")
        self._head_dot.configure(fg={
            "running": c["ok"], "starting": c["warn"], "stopping": c["warn"],
            "error": c["error"]}.get(status, c["faint"]))
        self._head_mark.set_running(status == "running")
        self._update_status_context()
        if self._tray is not None:
            self._tray.set_colors(self.ctx.colors)
            self._tray.set_server_status(status)

    def _update_status_context(self, _data=None) -> None:
        parts: list[str] = []
        runtime = self.ctx.services["runtimes"].get_active()
        if runtime:
            parts.append(f"runtime · {runtime.backend.upper()}")

        models = self.ctx.services["models"]
        profiles = self.ctx.services["profiles"]
        available = []
        for model in models.list():
            active = [p for p in profiles.list(model.id) if p.active]
            if model.enabled and model.state != "missing" and active:
                available.append((model, active))
        if len(available) == 1:
            model, active = available[0]
            parts.append(f"model · {model.name}")
            if len(active) == 1:
                parts.append(f"profile · {active[0].name}")
            elif len(active) > 1:
                parts.append(t("{count} active profiles", count=len(active)))
        elif len(available) > 1:
            label = ("{count} models loaded"
                     if getattr(self, "_last_server_status", "stopped") == "running"
                     else "{count} models available")
            parts.append(t(label, count=len(available)))
        self._sb_right.configure(text="   ".join(parts))

    # ── Navigation ───────────────────────────────────────────────────────────

    def show_page(self, key: str) -> None:
        # Migrate the formerly standalone Preset page into Profiles.
        if key in ("models", "preset"):
            key = "profiles"
        elif key == "server":
            key = "dashboard"
        if key not in PAGES:
            key = "dashboard"
        if self._active == key:
            return
        self._cancel_prewarm()
        if key not in self._pages:
            # Keep the current page painted while the first build completes.
            self._create_page(key)
        if self._active:
            old_page = self._pages[self._active]
            old_page._visible = False
            if hasattr(old_page, "on_hide"):
                old_page.on_hide()
            old_page.pack_forget()
            self._nav[self._active].set_active(False)

        page = self._pages[key]
        page.pack(fill="both", expand=True)
        self._nav[key].set_active(True)
        self._active = key
        page._visible = True
        self._set_monitoring_active(key == "dashboard")
        if hasattr(page, "on_show"):
            page.on_show()
        self.root.after_idle(lambda k=key: self._scroll_to_nav(k))
        self.root.after_idle(lambda k=key: self._focus_first_action(k))
        self._schedule_prewarm()

    def _set_monitoring_active(self, active: bool) -> None:
        for name in ("gpu_monitor", "system_monitor"):
            monitor = self.ctx.services.get(name)
            if monitor is not None:
                monitor.set_active(active)

    # ── Event pump / lifecycle ───────────────────────────────────────────────

    def _pump(self) -> None:
        self.ctx.events.drain()
        self.root.after(_DRAIN_MS, self._pump)

    # ── System tray ──────────────────────────────────────────────────────────

    def _server_status(self) -> str:
        server = self.ctx.services.get("server")
        status = getattr(server, "status", "stopped")
        return getattr(status, "value", status)

    def _ensure_tray(self) -> bool:
        if not self.ctx.enable_tray:
            return False
        if self._tray is None:
            from llama_router.services.tray import WinTray
            self._tray = WinTray(
                self.ctx.events, colors=self.ctx.colors,
                running=self._server_status() == "running")
        self._tray.set_colors(self.ctx.colors)
        self._tray.set_server_status(self._server_status())
        if self._tray.show():
            return True
        self._tray = None
        return False

    def _on_unmap(self, event) -> None:
        if event.widget is not self.root:
            return
        self._set_monitoring_active(False)
        if self.root.state() != "iconic":
            return
        cfg = self.ctx.services.get("config")
        if cfg is None or not cfg.get().minimize_to_tray:
            return
        if self._ensure_tray():
            self._tray.announce_hidden()
            self.root.withdraw()

    def _on_map(self, event) -> None:
        if event.widget is self.root:
            self._set_monitoring_active(self._active == "dashboard")

    def _on_tray_restore(self, _data) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self._set_monitoring_active(self._active == "dashboard")

    def _on_tray_start(self, _data) -> None:
        server = self.ctx.services.get("server")
        if server is not None:
            server.start()

    def _on_tray_stop(self, _data) -> None:
        server = self.ctx.services.get("server")
        if server is not None:
            server.stop_async()

    def _on_tray_restart(self, _data) -> None:
        server = self.ctx.services.get("server")
        if server is not None:
            server.restart_async()

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._cancel_prewarm()
        self._set_monitoring_active(False)
        from llama_router.core.storage import db_write
        try:
            db_write(self.ctx.paths.db_path, _STATE_KEY,
                     {"page": self._active,
                      "collapsible_cards": self.ctx.collapsible_states})
        except Exception:
            pass
        if self._tray is not None:
            try:
                self._tray.destroy()
            except Exception:
                pass
        server = self.ctx.services.get("server")
        if server is not None and server.is_running():
            self.root.withdraw()
            server.stop_async(timeout=10)
            self.root.after(100, self._finish_close_when_stopped)
            return
        self._finish_close()

    def _finish_close_when_stopped(self) -> None:
        server = self.ctx.services.get("server")
        proc = getattr(server, "_process", None)
        if proc is not None and proc.poll() is None:
            self.root.after(100, self._finish_close_when_stopped)
            return
        self._finish_close()

    def _finish_close(self) -> None:
        server = self.ctx.services.get("server")
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
