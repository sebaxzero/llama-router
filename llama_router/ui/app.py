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
from llama_router.ui.widgets import AppMark, NavItem, StatusDot

_DRAIN_MS = 100          # EventBus drain cadence
_STATE_KEY = "ui_state"  # KV key for the persisted active page
_DEFAULT_GEOMETRY = "960x640"  # fixed startup size (matches tools/screenshots.py)
_PREWARM_DELAY_MS = 150  # let the initial page paint before hidden pages build
_PREWARM_STEP_MS = 50    # yield to input and redraws between page builds

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
        self._prewarm_id: str | None = None
        self._prewarm_queue: list[str] = []
        self._nav_hbar_visible = False
        self._rebuilding = False   # guards resize handlers during theme teardown
        self._closing = False

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

        # ── System tray (Windows, opt-in) ────────────────────────────────────
        # The tray lives outside the widget tree, so theme flips never touch
        # it; it is created lazily on the first minimize and torn down in
        # _on_close. Its events arrive through the same pump as everything.
        self._tray = None
        from llama_router.services import tray as _tray_mod
        if ctx.enable_tray and _tray_mod.is_supported():
            self.root.bind("<Unmap>", self._on_unmap)
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
        self._cancel_prewarm()
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
        self._relayout_chrome()
        self._on_nav_resize()

    def _scroll_to_nav(self, key: str) -> None:
        """Bring the active tab into view when the strip is scrolled."""
        item = self._nav.get(key)
        if not item or not getattr(self, "_nav_canvas", None):
            return
        self._nav_canvas.update_idletasks()
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
        self._start_prewarm()

    def _create_page(self, key: str):
        """Build and cache one page without making it visible."""
        started = time.perf_counter()
        cls = PAGES[key][1]
        page = cls(self.content, self.ctx)
        self._pages[key] = page
        log.debug("Built %s page in %.1f ms", key,
                  (time.perf_counter() - started) * 1000)
        return page

    def _start_prewarm(self) -> None:
        """Build hidden pages incrementally after the first page has painted.

        A separate ``after`` callback is used for every page so Tk can process
        redraws and input between builds.  Page ``on_show`` hooks deliberately
        remain navigation-only: prewarming must not trigger scans or network
        work.
        """
        self._cancel_prewarm()
        keys = list(PAGES)
        active = keys.index(self._active) if self._active in PAGES else 0
        ordered = keys[active + 1:] + keys[:active]
        self._prewarm_queue = [key for key in ordered
                               if key not in self._pages]
        if not self._prewarm_queue:
            return
        self._set_prewarm_status(0, len(self._prewarm_queue))
        self._prewarm_id = self.root.after(
            _PREWARM_DELAY_MS, self._prewarm_next)

    def _prewarm_next(self) -> None:
        self._prewarm_id = None
        total = len(PAGES) - 1
        while self._prewarm_queue:
            key = self._prewarm_queue.pop(0)
            if key not in self._pages:
                self._create_page(key)
                break

        remaining = sum(key not in self._pages for key in self._prewarm_queue)
        done = total - remaining
        if self._prewarm_queue:
            self._set_prewarm_status(done, total)
            self._prewarm_id = self.root.after(
                _PREWARM_STEP_MS, self._prewarm_next)
        else:
            self._sb_right.configure(text=t("Interface ready"))
            self._prewarm_id = self.root.after(
                1200, lambda: self._sb_right.configure(text=""))

    def _set_prewarm_status(self, done: int, total: int) -> None:
        self._sb_right.configure(
            text=t("Preparing interface… {done}/{total}",
                   done=done, total=total))

    def _cancel_prewarm(self) -> None:
        prewarm_id = getattr(self, "_prewarm_id", None)
        if prewarm_id is not None:
            try:
                self.root.after_cancel(prewarm_id)
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

        self.ctx.events.subscribe("server_status", self._on_server_status)

    def _on_server_status(self, data: dict) -> None:
        c = self.ctx.colors
        status = (data or {}).get("status", "stopped")
        # Most publishers send ServerStatus.value, but apply_theme() re-syncs
        # from `server.status`, which is the enum itself. Since Python 3.11 an
        # f-string on a str-mixin enum renders "ServerStatus.STOPPED" instead
        # of "stopped", so normalise here — the one place every path converges.
        status = getattr(status, "value", status)
        self._sb_dot.set(status)
        self._sb_text.configure(text=f"server · {status}")
        self._head_dot.configure(fg={
            "running": c["ok"], "starting": c["warn"], "stopping": c["warn"],
            "error": c["error"]}.get(status, c["faint"]))
        self._head_mark.set_running(status == "running")
        if self._tray is not None:
            self._tray.set_colors(self.ctx.colors)
            self._tray.set_server_status(status)

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
        if self._active:
            self._pages[self._active].pack_forget()
            self._nav[self._active].set_active(False)

        if key not in self._pages:
            self._create_page(key)
        page = self._pages[key]
        page.pack(fill="both", expand=True)
        self._nav[key].set_active(True)
        self._scroll_to_nav(key)
        self._active = key
        if hasattr(page, "on_show"):
            page.on_show()

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
        if event.widget is not self.root or self.root.state() != "iconic":
            return
        cfg = self.ctx.services.get("config")
        if cfg is None or not cfg.get().minimize_to_tray:
            return
        if self._ensure_tray():
            self._tray.announce_hidden()
            self.root.withdraw()

    def _on_tray_restore(self, _data) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()

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
