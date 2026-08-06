"""Hand-drawn chrome: rounded cards, pill buttons, badges, nav items.

Tkinter has no border-radius, so anything that should look designed is drawn
on a Canvas. Every widget takes the token palette `c` from theme.apply().
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Callable

from llama_router.i18n import t
from llama_router.ui import theme


# Server status → (i18n label, status-dot key). The one place the app maps a
# ServerStatus value to something a human reads.
_STATUS_LABELS = {
    "stopped": "Stopped", "starting": "Starting…", "running": "Running",
    "stopping": "Stopping…", "error": "Error",
}


def status_label(status: str) -> str:
    """Translated caption for a server status value."""
    return t(_STATUS_LABELS.get(status, status))


def fmt_uptime(seconds: float) -> str:
    """Seconds → ``hh:mm:ss``."""
    up = int(seconds)
    return f"{up // 3600:02d}:{(up % 3600) // 60:02d}:{up % 60:02d}"


def rounded_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int,
                 r: int, **kw) -> int:
    """Draw a rounded rectangle; returns the canvas item id."""
    r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
    pts = [
        x1 + r, y1,  x2 - r, y1,
        x2, y1,      x2, y1 + r,
        x2, y2 - r,  x2, y2,
        x2 - r, y2,  x1 + r, y2,
        x1, y2,      x1, y2 - r,
        x1, y1 + r,  x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


class AppMark(tk.Canvas):
    """Theme-aware llama mark with a subtle animated Wi-Fi pulse."""

    def __init__(self, parent: tk.Widget, c: dict, size: int = 48) -> None:
        self._c = c
        self._size = size
        self._phase = 0
        self._running = False
        self._after_id: str | None = None
        super().__init__(parent, width=size, height=size,
                         bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self.bind("<Configure>", self._draw)
        self.bind("<Destroy>", self._on_destroy)
        self._draw()
        self._after_id = self.after(240, self._animate)

    def set_running(self, running: bool) -> None:
        running = bool(running)
        if running == self._running:
            return
        self._running = running
        if running and self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        elif not running and self._after_id is None:
            self._after_id = self.after(240, self._animate)
        self._draw()

    def _animate(self) -> None:
        self._after_id = None
        if not self.winfo_exists():
            return
        self._phase = (self._phase + 1) % 4
        self._draw()
        self._after_id = self.after(240, self._animate)

    def _on_destroy(self, event) -> None:
        if event.widget is self and self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _draw(self, _event=None) -> None:
        self.delete("all")
        s = self._size
        k = (s - 2) / 350

        def x(value: float) -> float:
            return 1 + (value - 110) * k

        def y(value: float) -> float:
            return 1 + (value - 75) * k

        c = self._c
        llama = c["accent"]
        self.create_polygon(
            x(145), y(175), x(165), y(255), x(135), y(255),
            fill=llama, outline="")
        self.create_polygon(
            x(205), y(165), x(225), y(245), x(195), y(245),
            fill=llama, outline="")
        self.create_polygon(
            x(135), y(255), x(250), y(245), x(310), y(295),
            x(310), y(335), x(235), y(335), x(235), y(415),
            x(165), y(415), x(165), y(305), x(135), y(285),
            fill=llama, outline="")
        self.create_oval(x(245), y(270), x(260), y(285),
                         fill=self.cget("bg"), outline="")

        ox, oy = x(280), y(250)
        self.create_oval(x(285), y(235), x(297), y(247),
                         fill=c["accent_hi"], outline="")
        waves = ((50, 8), (90, 8), (130, 8), (170, 7))
        for index, (radius, width) in enumerate(waves):
            if self._running:
                color = c["accent_hi"]
            elif index == self._phase:
                color = c["accent_hi"]
            elif index == (self._phase - 1) % len(waves):
                color = c["accent"]
            elif index < self._phase:
                color = c["muted"]
            else:
                color = c["faint"]
            r = radius * k
            self.create_arc(ox - r, oy - r, ox + r, oy + r,
                            start=0, extent=75, style=tk.ARC,
                            outline=color, width=max(1, round(width * k)))


class Card(tk.Frame):
    """Flat panel with a hairline border. Put content in `self.body`.

    Forge look: gently rounded corners (6px), 1px hair outline on a
    slightly raised surface. The canvas resizes with the card; the inner
    frame is pinned with padding and stretched to the canvas width so
    `pack(fill="x")` behaves like a div.
    """

    def __init__(self, parent: tk.Widget, c: dict, pad: int = 16,
                 radius: int = 6, fill: str | None = None,
                 border: str | None = None) -> None:
        super().__init__(parent, bg=parent.cget("bg"))
        self._c = c
        self._pad = pad
        self._radius = radius
        self._fill = fill or c["surface"]
        self._border = border or c["border"]

        self._canvas = tk.Canvas(self, bg=parent.cget("bg"),
                                 highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self._canvas, bg=self._fill)
        self._win = self._canvas.create_window(pad, pad, window=self.body,
                                               anchor="nw")
        self._rect = rounded_rect(self._canvas, 1, 1, 3, 3, radius,
                                  fill=self._fill, outline=self._border)
        self._canvas.tag_lower(self._rect)
        self.body.bind("<Configure>", self._on_body)
        self._canvas.bind("<Configure>", self._on_canvas)

    def _on_body(self, _e=None) -> None:
        w = self.body.winfo_reqwidth() + 2 * self._pad
        h = self.body.winfo_reqheight() + 2 * self._pad
        options = {}
        if int(float(self._canvas.cget("height"))) != h:
            options["height"] = h
        # Once packed, the parent owns the card width. Re-requesting it every
        # time heavy content is hidden creates a resize loop and visible flash.
        if (self._canvas.winfo_width() <= 1
                and int(float(self._canvas.cget("width"))) != w):
            options["width"] = w
        if options:
            self._canvas.configure(**options)

    def _on_canvas(self, e) -> None:
        self._canvas.itemconfigure(
            self._win, width=max(1, e.width - 2 * self._pad))
        x1, y1 = 1, 1
        x2, y2 = max(3, e.width - 2), max(3, e.height - 2)
        r = min(self._radius, (x2 - x1) // 2, (y2 - y1) // 2)
        self._canvas.coords(
            self._rect,
            x1 + r, y1, x2 - r, y1,
            x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r,
            x1, y1 + r, x1, y1)


class PillButton(tk.Canvas):
    """Forge button: rounded-rect, mono uppercase. kind: 'primary' (amber
    fill), 'accent' (amber outline) or 'ghost' (hair outline)."""

    _RADIUS = 4

    def __init__(self, parent: tk.Widget, c: dict, text: str,
                 command: Callable[[], None] | None = None,
                 kind: str = "ghost", size: int = 9,
                 padx: int = 16, height: int = 30) -> None:
        self._c = c
        self._kind = kind
        self._command = command
        self._enabled = True
        self._focused = False
        self._keyboard_nav = True
        self._padx = padx
        self._font = theme.mono(size, "bold")

        text = text.upper()
        f = tkfont.Font(font=self._font)
        w = f.measure(text) + 2 * padx
        super().__init__(parent, width=w, height=height,
                         bg=parent.cget("bg"), highlightthickness=0, bd=0,
                         takefocus=bool(command))
        self._bw, self._bh = w, height
        self._text = text
        self._draw("normal")

        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<Button-1>", self._on_click)
        self.bind("<Key-space>", self._on_click)
        self.bind("<Key-Return>", self._on_click)
        self.bind("<FocusIn>", lambda _e: self._set_focused(True))
        self.bind("<FocusOut>", lambda _e: self._set_focused(False))
        self.configure(cursor="hand2")

    def _palette(self, state: str) -> tuple[str, str, str]:
        c = self._c
        if not self._enabled:
            return c["surface"], c["border"], c["faint"]
        if self._kind == "primary":
            fill = c["accent_hi"] if state == "hover" else c["accent"]
            return fill, fill, c["on_accent"]
        if self._kind == "accent":
            fg = c["accent_hi"] if state == "hover" else c["accent"]
            fill = c["surface_hi"] if state == "hover" else c["surface"]
            return fill, fg, fg
        if state in ("hover", "focus"):
            return c["surface_hi"], c["muted"], c["text"]
        return c["surface"], c["border"], c["text"]

    def _draw(self, state: str) -> None:
        self.delete("all")
        fill, outline, fg = self._palette(state)
        r = self._RADIUS
        rounded_rect(self, 1, 1, self._bw - 2, self._bh - 2, r,
                     fill=fill, outline=outline)
        if state == "focus":
            ring = (self._c["on_accent"] if self._kind == "primary"
                    else self._c["accent_hi"])
            rounded_rect(self, 4, 4, self._bw - 5, self._bh - 5,
                         max(1, r - 1), fill="", outline=ring, width=2)
        self.create_text(self._bw // 2, self._bh // 2, text=self._text,
                         fill=fg, font=self._font)

    def _hover(self, on: bool) -> None:
        if self._enabled:
            self._draw("hover" if on else ("focus" if self._focused else "normal"))

    def _set_focused(self, focused: bool) -> None:
        self._focused = focused
        self._draw("focus" if focused else "normal")

    def _on_click(self, _e) -> None:
        if self._enabled and self._command:
            self.focus_set()
            self._command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw("focus" if self._focused else "normal")

    def set_text(self, text: str) -> None:
        self._text = text.upper()
        f = tkfont.Font(font=self._font)
        self._bw = f.measure(self._text) + 2 * self._padx
        self.configure(width=self._bw)
        self._draw("focus" if self._focused else "normal")

    def set_kind(self, kind: str) -> None:
        """Swap the button style in place (used by toggles/segmented controls)."""
        self._kind = kind
        self._draw("focus" if self._focused else "normal")


class Tooltip:
    """Small delayed, theme-aware hint attached to any widget."""

    def __init__(self, widget: tk.Widget, c: dict, text: str,
                 delay: int = 450) -> None:
        self.widget = widget
        self.c = c
        self.text = text
        self.delay = delay
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def _schedule(self, _event=None) -> None:
        self.hide()
        self._after_id = self.widget.after(self.delay, self.show)

    def show(self) -> None:
        self._after_id = None
        if self._tip is not None or not self.widget.winfo_exists():
            return
        x, y = self.widget.winfo_pointerxy()
        tip = self._tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x + 12}+{y + 18}")
        tk.Label(tip, text=self.text, bg=self.c["surface_hi"],
                 fg=self.c["text"], font=theme.ui(9), justify="left",
                 wraplength=320, padx=9, pady=6,
                 highlightbackground=self.c["panel_accent"],
                 highlightthickness=1).pack()

    def hide(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class StatusDot(tk.Canvas):
    """LED with a soft halo — the panel's heartbeat."""

    def __init__(self, parent: tk.Widget, c: dict, size: int = 18) -> None:
        super().__init__(parent, width=size, height=size,
                         bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self._c = c
        self._size = size
        self.set("stopped")

    def set(self, status: str) -> None:
        c = self._c
        color, halo = {
            "running":  (c["ok"], c["ok_dim"]),
            "starting": (c["warn"], c["warn_dim"]),
            "stopping": (c["warn"], c["warn_dim"]),
            "error":    (c["error"], c["error_dim"]),
        }.get(status, (c["faint"], c["surface_hi"]))
        s, m = self._size, self._size // 2
        self.delete("all")
        self.create_oval(1, 1, s - 1, s - 1, fill=halo, outline="")
        self.create_oval(m - 4, m - 4, m + 4, m + 4, fill=color, outline="")


class NavItem(tk.Button):
    """Top tab: mono uppercase; the active tab is a solid accent block with
    dark text. Inactive tabs get a subtle bottom-border glow on hover.

    Labels are rendered as plain uppercase (no letter-spacing): the panel's
    tracking style inserts a space between every glyph, which would balloon
    the strip past the window width and force the tabs to scroll/hide.
    """

    def __init__(self, parent: tk.Widget, c: dict, label: str,
                 command: Callable[[], None]) -> None:
        self._c = c
        self._command = command
        self._active = False
        self._focused = False
        self._keyboard_nav = True
        super().__init__(
            parent, text=label.upper(), command=self._activate,
            bg=c["bg"], fg=c["muted"], activebackground=c["surface_hi"],
            activeforeground=c["text"], font=theme.mono(9, "bold"),
            padx=14, pady=8, relief="flat", overrelief="flat", bd=0,
            highlightthickness=2, highlightbackground=c["bg"],
            highlightcolor=c["accent_hi"], takefocus=True, cursor="hand2")

        self.bind("<Enter>",
                  lambda _e: self._paint(hover=True, focused=self._focused))
        self.bind("<Leave>",
                  lambda _e: self._paint(focused=self._focused))
        self.bind("<Key-space>", self._activate)
        self.bind("<Key-Return>", self._activate)
        self.bind("<FocusIn>", lambda _e: self._set_focused(True))
        self.bind("<FocusOut>", lambda _e: self._set_focused(False))
        self._paint()

    def _activate(self, event=None) -> str | None:
        self.focus_set()
        self._command()
        return "break" if event is not None else None

    def set_active(self, active: bool) -> None:
        self._active = active
        self._paint(focused=self._focused)

    def _set_focused(self, focused: bool) -> None:
        self._focused = focused
        self._paint(focused=focused)

    def _paint(self, hover: bool = False, focused: bool = False) -> None:
        c = self._c
        if self._active:
            bg, fg = c["accent"], c["on_accent"]
        elif hover or focused:
            bg, fg = c["surface_hi"], c["text"]
        else:
            bg, fg = c["bg"], c["muted"]
        ring = c["on_accent"] if self._active else c["accent_hi"]
        self.configure(bg=bg, fg=fg, activebackground=bg,
                       activeforeground=fg, highlightbackground=bg,
                       highlightcolor=ring)


class PageHeader(tk.Frame):
    """Eyebrow + title + optional subtitle; actions dock on the right."""

    _COMPACT_WIDTH = 860

    def __init__(self, parent: tk.Widget, c: dict, eyebrow: str, title: str,
                 subtitle: str = "") -> None:
        super().__init__(parent, bg=c["bg"])
        self.columnconfigure(0, weight=1)
        self._left = left = tk.Frame(self, bg=c["bg"])
        left.grid(row=0, column=0, sticky="ew")
        tk.Label(left, text=theme.track(eyebrow), bg=c["bg"], fg=c["faint"],
                 font=theme.mono(8, "bold")).pack(anchor="w")
        tk.Label(left, text=theme.track(title), bg=c["bg"], fg=c["text"],
                 font=theme.mono(14, "bold")).pack(anchor="w", pady=(2, 0))
        if subtitle:
            tk.Label(left, text=subtitle, bg=c["bg"], fg=c["muted"],
                     font=theme.ui(10)).pack(anchor="w", pady=(3, 0))
        self.actions = tk.Frame(self, bg=c["bg"])
        self.actions.grid(row=0, column=1, sticky="se")
        self._compact: bool | None = None
        self.bind("<Configure>", self._on_resize, add="+")

    def _on_resize(self, event) -> None:
        compact = event.width < self._COMPACT_WIDTH
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self._left.grid_configure(columnspan=2)
            self.actions.grid_configure(row=1, column=0, columnspan=2,
                                        sticky="w", pady=(10, 0))
        else:
            self._left.grid_configure(columnspan=1)
            self.actions.grid_configure(row=0, column=1, columnspan=1,
                                        sticky="se", pady=0)


class AutoScrollbar(ttk.Scrollbar):
    """A packed scrollbar that disappears when the whole range is visible."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pack_options: dict = {}

    def pack(self, **kwargs) -> None:  # type: ignore[override]
        self._pack_options = dict(kwargs)
        super().pack(**kwargs)

    def set(self, first, last) -> None:
        needed = float(first) > 0.0 or float(last) < 1.0
        if needed and not self.winfo_ismapped():
            super().pack(**self._pack_options)
        elif not needed and self.winfo_ismapped():
            super().pack_forget()
        super().set(first, last)


class ScrollFrame(tk.Frame):
    """Vertical scroll container. Put content in `self.body`."""

    def __init__(self, parent: tk.Widget, c: dict,
                 fill_height: bool = False) -> None:
        super().__init__(parent, bg=c["bg"])
        self._fill_height = fill_height
        self._canvas = tk.Canvas(self, bg=c["bg"], highlightthickness=0, bd=0)
        # Keep the gutter reserved even while the auto-scrollbar is hidden.
        # Otherwise crossing the overflow threshold changes every card's
        # width and produces a full second layout pass (the visible flash).
        self._vbar_host = tk.Frame(self, bg=c["bg"])
        self._vbar = AutoScrollbar(self._vbar_host, orient="vertical",
                                   command=self._canvas.yview)
        self._vbar_host.configure(width=self._vbar.winfo_reqwidth())
        self._vbar_host.pack(side="right", fill="y")
        self._vbar_host.pack_propagate(False)
        self._canvas.configure(yscrollcommand=self._vbar.set)
        self._vbar.pack(fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self.body = tk.Frame(self._canvas, bg=c["bg"])
        self._win = self._canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self._canvas.bind("<Configure>", self._on_canvas)
        # Global bindings make wheel/page scrolling work from any descendant.
        # Keep each Tcl command id so rebuilding a page can remove only the
        # callbacks owned by this instance.
        self._global_bindings: list[tuple[str, str]] = []
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._bind_global(sequence, self._on_wheel)
        for sequence, action in (("<Prior>", "page-up"),
                                 ("<Next>", "page-down"),
                                 ("<Home>", "home"),
                                 ("<End>", "end")):
            self._bind_global(
                sequence, lambda event, a=action: self._on_scroll_key(event, a))
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _bind_global(self, sequence: str, handler: Callable) -> None:
        funcid = self.bind_all(sequence, handler, add="+")
        if funcid:
            self._global_bindings.append((sequence, funcid))

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        for sequence, funcid in self._global_bindings:
            try:
                self._root()._unbind(("bind", "all", sequence), funcid)
            except tk.TclError:
                pass
        self._global_bindings.clear()

    def _on_body(self, _e) -> None:
        self._fit_height()
        region = self._canvas.bbox("all")
        self._canvas.configure(scrollregion=region)
        if region and region[3] - region[1] <= self._canvas.winfo_height():
            self._canvas.yview_moveto(0)

    def _on_canvas(self, e) -> None:
        self._canvas.itemconfigure(self._win, width=e.width)
        self._fit_height()

    def _fit_height(self) -> None:
        """Let an optional page body fill the viewport before it overflows."""
        if self._fill_height:
            height = max(self._canvas.winfo_height(), self.body.winfo_reqheight())
            self._canvas.itemconfigure(self._win, height=height)

    def scroll_units(self, steps: int) -> None:
        """Move the viewport by units for child controls that consume wheels."""
        if steps:
            self._canvas.yview_scroll(steps, "units")

    def scroll_to_start(self) -> None:
        self._canvas.yview_moveto(0)

    def see(self, widget: tk.Widget, margin: int = 12) -> None:
        """Scroll just enough to reveal a focused descendant."""
        try:
            top = 0
            current: tk.Widget | None = widget
            while current is not self.body:
                if current is None:
                    return
                top += current.winfo_y()
                current = getattr(current, "master", None)
            bottom = top + widget.winfo_height()
            view_top = self._canvas.canvasy(0)
            view_height = self._canvas.winfo_height()
            view_bottom = view_top + view_height
            region = self._canvas.bbox("all")
            if not region or region[3] <= view_height:
                return
            target = None
            if top - margin < view_top:
                target = top - margin
            elif bottom + margin > view_bottom:
                target = bottom + margin - view_height
            if target is not None:
                content_height = max(1, region[3] - region[1])
                self._canvas.yview_moveto(
                    max(0.0, min(1.0,
                                 (target - region[1]) / content_height)))
        except tk.TclError:
            pass

    def _on_wheel(self, e) -> None:
        # bind_all outlives the page: guard against destroyed instances, and
        # only react while the pointer is inside this scroller.
        try:
            if not self.winfo_exists():
                return
            x, y = self.winfo_pointerxy()
            w = self.winfo_containing(x, y)
        # ttk Combobox popdowns are Tcl-only windows, so Tk can return a
        # widget path that tkinter cannot resolve through ``children``.
        except (tk.TclError, KeyError):
            return
        while w is not None:
            # ScrollFrames can be nested (the model workspace contains a
            # scrollable editor).  The first one in the widget ancestry owns
            # this wheel event; letting an outer frame continue would move
            # both viewports for a single gesture.
            if isinstance(w, ScrollFrame):
                if w is not self:
                    return
                delta = getattr(e, "delta", 0)
                steps = -1 if getattr(e, "num", None) == 4 else (
                    1 if getattr(e, "num", None) == 5 else -1 * (delta // 120))
                self.scroll_units(steps)
                return
            w = getattr(w, "master", None)

    def _on_scroll_key(self, _event, action: str) -> str | None:
        """Page-scroll when focus is in this scroller but not in an editor."""
        try:
            focus = self.focus_get()
        except (tk.TclError, KeyError):
            return None
        if focus is None or isinstance(
                focus, (tk.Text, tk.Entry, ttk.Entry, ttk.Treeview,
                        ttk.Combobox)):
            return None
        owner: tk.Widget | None = focus
        while owner is not None and not isinstance(owner, ScrollFrame):
            owner = getattr(owner, "master", None)
        if owner is not self:
            return None
        if action == "page-up":
            self._canvas.yview_scroll(-1, "pages")
        elif action == "page-down":
            self._canvas.yview_scroll(1, "pages")
        elif action == "home":
            self._canvas.yview_moveto(0)
        else:
            self._canvas.yview_moveto(1)
        return "break"


class SegmentBar(tk.Canvas):
    """Block meter: a row of amber cells, LlamaForge's VRAM gauge.

    `set(frac)` lights the leading cells; empty cells stay as dark
    hair-outlined sockets.
    """

    def __init__(self, parent: tk.Widget, c: dict, segments: int = 24,
                 seg_w: int = 12, seg_h: int = 14, gap: int = 3) -> None:
        self._c = c
        self._n = segments
        self._sw, self._sh, self._gap = seg_w, seg_h, gap
        w = segments * seg_w + (segments - 1) * gap
        super().__init__(parent, width=w, height=seg_h,
                         bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self._frac = 0.0
        self._redraw()

    def set(self, frac: float) -> None:
        self._frac = min(1.0, max(0.0, frac))
        self._redraw()

    def _redraw(self) -> None:
        c = self._c
        self.delete("all")
        lit = round(self._frac * self._n)
        r = min(2, self._sh // 2)
        for i in range(self._n):
            x = i * (self._sw + self._gap)
            if i < lit:
                rounded_rect(self, x, 0, x + self._sw, self._sh, r,
                             fill=c["accent"], outline=c["accent"])
            else:
                rounded_rect(self, x, 0, x + self._sw, self._sh, r,
                             fill=c["inset"], outline=c["border"])


def enable_row_hover(tree: ttk.Treeview, c: dict) -> None:
    """Highlight the Treeview row under the cursor (no native row hover).

    Uses a `hover` tag; selection styling is left untouched because both
    share the raised-surface colour, so a hovered selected row still reads
    as selected.
    """
    tree.tag_configure("hover", background=c["surface_hi"])
    tree._hover_row = None  # type: ignore[attr-defined]

    def _on_motion(e) -> None:
        row = tree.identify_row(e.y)
        if row == tree._hover_row:
            return
        if tree._hover_row and tree.exists(tree._hover_row):
            tags = [t for t in tree.item(tree._hover_row, "tags") if t != "hover"]
            tree.item(tree._hover_row, tags=tags)
        tree._hover_row = row
        if row:
            tree.item(row, tags=list(tree.item(row, "tags")) + ["hover"])

    def _on_leave(_e) -> None:
        if tree._hover_row and tree.exists(tree._hover_row):
            tags = [t for t in tree.item(tree._hover_row, "tags") if t != "hover"]
            tree.item(tree._hover_row, tags=tags)
        tree._hover_row = None

    tree.bind("<Motion>", _on_motion)
    tree.bind("<Leave>", _on_leave)


def section_label(parent: tk.Widget, c: dict, text: str,
                  accent: str | None = None) -> tk.Frame:
    """Uppercase mono card heading with an optional accent marker."""
    heading = tk.Frame(parent, bg=parent.cget("bg"))
    if accent:
        marker = tk.Frame(heading, bg=accent, width=4, height=16)
        marker.pack(side="left", padx=(0, 8))
        marker.pack_propagate(False)
    tk.Label(heading, text=theme.track(text), bg=heading.cget("bg"),
             fg=accent or c["faint"],
             font=theme.mono(8, "bold")).pack(side="left")
    return heading


class CollapsibleCard(Card):
    """Standard card header with a dashboard-style disclosure control."""

    def __init__(self, parent: tk.Widget, c: dict, title: str,
                 expanded: bool = True, pad: int = 16,
                 on_toggle: Callable[[bool], None] | None = None,
                 state_key: str | None = None,
                 accent: str | None = None) -> None:
        super().__init__(parent, c, pad=pad, border=accent)
        self._state_key = state_key
        self._state_store: dict[str, bool] | None = None
        owner = parent
        while owner is not None:
            ctx = getattr(owner, "ctx", None)
            if ctx is not None:
                self._state_store = getattr(ctx, "collapsible_states", None)
                break
            owner = getattr(owner, "master", None)
        if state_key and self._state_store is not None:
            expanded = bool(self._state_store.get(state_key, expanded))
        self._open = expanded
        self._on_toggle = on_toggle
        self._refresh_id: str | None = None
        self.header = tk.Frame(self.body, bg=c["surface"])
        self.header.pack(fill="x")
        heading = section_label(self.header, c, title, accent)
        heading.pack(side="left")
        self._toggle = PillButton(self.header, c, "▾" if expanded else "▸", size=9,
                                  padx=7, height=26, command=self.toggle)
        self._toggle.pack(side="right")
        self.actions = tk.Frame(self.header, bg=c["surface"])
        self.actions.pack(side="right", padx=(0, 8))
        self.content = tk.Frame(self.body, bg=c["surface"])
        if expanded:
            self.content.pack(fill="both", expand=True, pady=(10, 0))
        self.bind("<Destroy>", self._on_destroy, add="+")

    def toggle(self) -> None:
        self.set_open(not self._open)

    @property
    def is_open(self) -> bool:
        return self._open

    def set_open(self, open_: bool) -> None:
        if open_ == self._open:
            return
        self._open = open_
        self._toggle.set_text("▾" if open_ else "▸")
        if open_:
            # Lazy card contents must be complete before the frame is mapped;
            # mapping an empty frame first produces a visible intermediate
            # layout and a second resize pass.
            if self._on_toggle is not None:
                self._on_toggle(True)
            self.content.pack(fill="both", expand=True, pady=(10, 0))
        else:
            self.content.pack_forget()
            if self._on_toggle is not None:
                self._on_toggle(False)
        # A fill-height ScrollFrame can keep its body at the old viewport
        # height when only a child's requested height changes. Refresh the
        # nearest owner once, after lazy content has reached its final size.
        if self._refresh_id is not None:
            self.after_cancel(self._refresh_id)
        self._refresh_id = self.after_idle(self._refresh_scroll_layout)
        if self._state_key and self._state_store is not None:
            self._state_store[self._state_key] = open_

    def _on_destroy(self, event) -> None:
        if event.widget is not self or self._refresh_id is None:
            return
        try:
            self.after_cancel(self._refresh_id)
        except tk.TclError:
            pass
        self._refresh_id = None

    def _refresh_scroll_layout(self) -> None:
        self._refresh_id = None
        owner = self.master
        while owner is not None:
            if isinstance(owner, ScrollFrame):
                if owner._fill_height:
                    owner._on_body(None)
                break
            owner = getattr(owner, "master", None)
