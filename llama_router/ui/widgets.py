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
                 radius: int = 6, fill: str | None = None) -> None:
        super().__init__(parent, bg=parent.cget("bg"))
        self._c = c
        self._pad = pad
        self._radius = radius
        self._fill = fill or c["surface"]

        self._canvas = tk.Canvas(self, bg=parent.cget("bg"),
                                 highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self._canvas, bg=self._fill)
        self._win = self._canvas.create_window(pad, pad, window=self.body,
                                               anchor="nw")
        self._rect: int | None = None
        self.body.bind("<Configure>", self._on_body)
        self._canvas.bind("<Configure>", self._on_canvas)

    def _on_body(self, _e=None) -> None:
        w = self.body.winfo_reqwidth() + 2 * self._pad
        h = self.body.winfo_reqheight() + 2 * self._pad
        self._canvas.configure(width=w, height=h)

    def _on_canvas(self, e) -> None:
        self._canvas.itemconfigure(self._win, width=e.width - 2 * self._pad)
        if self._rect is not None:
            self._canvas.delete(self._rect)
        r = self._radius
        self._rect = rounded_rect(self._canvas, 1, 1,
                                  e.width - 2, e.height - 2, r,
                                  fill=self._fill,
                                  outline=self._c["border"])
        self._canvas.tag_lower(self._rect)


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
        self._padx = padx
        self._font = theme.mono(size, "bold")

        text = text.upper()
        f = tkfont.Font(font=self._font)
        w = f.measure(text) + 2 * padx
        super().__init__(parent, width=w, height=height,
                         bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self._bw, self._bh = w, height
        self._text = text
        self._draw("normal")

        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<Button-1>", self._on_click)
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
        if state == "hover":
            return c["surface_hi"], c["muted"], c["text"]
        return c["surface"], c["border"], c["text"]

    def _draw(self, state: str) -> None:
        self.delete("all")
        fill, outline, fg = self._palette(state)
        r = self._RADIUS
        rounded_rect(self, 1, 1, self._bw - 2, self._bh - 2, r,
                     fill=fill, outline=outline)
        self.create_text(self._bw // 2, self._bh // 2, text=self._text,
                         fill=fg, font=self._font)

    def _hover(self, on: bool) -> None:
        if self._enabled:
            self._draw("hover" if on else "normal")

    def _on_click(self, _e) -> None:
        if self._enabled and self._command:
            self._command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw("normal")

    def set_text(self, text: str) -> None:
        self._text = text.upper()
        f = tkfont.Font(font=self._font)
        self._bw = f.measure(self._text) + 2 * self._padx
        self.configure(width=self._bw)
        self._draw("normal")

    def set_kind(self, kind: str) -> None:
        """Swap the button style in place (used by toggles/segmented controls)."""
        self._kind = kind
        self._draw("normal")


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


class NavItem(tk.Frame):
    """Top tab: mono uppercase; the active tab is a solid accent block with
    dark text. Inactive tabs get a subtle bottom-border glow on hover.

    Labels are rendered as plain uppercase (no letter-spacing): the panel's
    tracking style inserts a space between every glyph, which would balloon
    the strip past the window width and force the tabs to scroll/hide.
    """

    def __init__(self, parent: tk.Widget, c: dict, label: str,
                 command: Callable[[], None]) -> None:
        super().__init__(parent, bg=c["bg"], cursor="hand2")
        self._c = c
        self._command = command
        self._active = False

        self._label = tk.Label(self, text=label.upper(), bg=c["bg"],
                               fg=c["muted"], font=theme.mono(9, "bold"),
                               padx=14, pady=8)
        self._label.pack()

        for w in (self, self._label):
            w.bind("<Button-1>", lambda e: self._command())
            w.bind("<Enter>", lambda e: self._paint(hover=True))
            w.bind("<Leave>", lambda e: self._paint(hover=False))
        self._paint()

    def set_active(self, active: bool) -> None:
        self._active = active
        self._paint()

    def _paint(self, hover: bool = False) -> None:
        c = self._c
        if self._active:
            bg, fg = c["accent"], c["on_accent"]
        elif hover:
            bg, fg = c["surface_hi"], c["text"]
        else:
            bg, fg = c["bg"], c["muted"]
        self.configure(bg=bg)
        self._label.configure(bg=bg, fg=fg)


class PageHeader(tk.Frame):
    """Eyebrow + title + optional subtitle; actions dock on the right."""

    def __init__(self, parent: tk.Widget, c: dict, eyebrow: str, title: str,
                 subtitle: str = "") -> None:
        super().__init__(parent, bg=c["bg"])
        left = tk.Frame(self, bg=c["bg"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=theme.track(eyebrow), bg=c["bg"], fg=c["faint"],
                 font=theme.mono(8, "bold")).pack(anchor="w")
        tk.Label(left, text=theme.track(title), bg=c["bg"], fg=c["text"],
                 font=theme.mono(14, "bold")).pack(anchor="w", pady=(2, 0))
        if subtitle:
            tk.Label(left, text=subtitle, bg=c["bg"], fg=c["muted"],
                     font=theme.ui(10)).pack(anchor="w", pady=(3, 0))
        self.actions = tk.Frame(self, bg=c["bg"])
        self.actions.pack(side="right", anchor="s")


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

    def __init__(self, parent: tk.Widget, c: dict) -> None:
        super().__init__(parent, bg=c["bg"])
        self._canvas = tk.Canvas(self, bg=c["bg"], highlightthickness=0, bd=0)
        self._vbar = AutoScrollbar(self, orient="vertical",
                                   command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vbar.set)
        self._vbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self.body = tk.Frame(self._canvas, bg=c["bg"])
        self._win = self._canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self._canvas.bind("<Configure>", self._on_canvas)
        # Wheel scrolling anywhere over the frame
        self._canvas.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def _on_body(self, _e) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas(self, e) -> None:
        self._canvas.itemconfigure(self._win, width=e.width)

    def _on_wheel(self, e) -> None:
        # bind_all outlives the page: guard against destroyed instances, and
        # only react while the pointer is inside this scroller.
        try:
            if not self.winfo_exists():
                return
            x, y = self.winfo_pointerxy()
            w = self.winfo_containing(x, y)
        except tk.TclError:
            return
        while w is not None:
            if w is self:
                self._canvas.yview_scroll(-1 * (e.delta // 120), "units")
                return
            w = getattr(w, "master", None)


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


def section_label(parent: tk.Widget, c: dict, text: str) -> tk.Label:
    """Uppercase mono card heading."""
    return tk.Label(parent, text=theme.track(text), bg=parent.cget("bg"),
                    fg=c["faint"], font=theme.mono(8, "bold"))


def key_value(parent: tk.Widget, c: dict, key: str, value: str,
              value_fg: str | None = None) -> tk.Frame:
    """One spec row: muted label left, mono value right."""
    row = tk.Frame(parent, bg=parent.cget("bg"))
    tk.Label(row, text=key, bg=row.cget("bg"), fg=c["muted"],
             font=theme.ui(9)).pack(side="left")
    tk.Label(row, text=value, bg=row.cget("bg"), fg=value_fg or c["text"],
             font=theme.mono(9)).pack(side="right")
    return row
