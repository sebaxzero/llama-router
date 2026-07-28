"""Design tokens + ttk styling.

Direction: instrument panel, not web page. Deep graphite surfaces, a single
hot accent, mono type for every piece of machine data. All custom chrome
(cards, pills, nav) is drawn in ui/widgets.py; this module owns the tokens and
the ttk styles for stock widgets (Treeview, Entry, Scrollbar).

Every widget reads its colours from the token dict returned by `apply()`, so
flipping the theme is a one-line call that the App turns into a full chrome +
page rebuild. Adding a theme = adding one entry to THEMES; its `_label` and
`_dark` keys drive the selector and the OS titlebar, and dict order is the
display order.
"""
from __future__ import annotations

import ctypes
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ── Palettes ────────────────────────────────────────────────────────────────
# Each theme is a complete token set. Keys used across the UI:
#   bg surface surface_hi inset border text muted faint
#   accent accent_hi accent_dn on_accent title
#   ok ok_dim warn warn_dim error error_dim request num
# `apply()` derives quieter panel_* variants for large borders and headings.
# Plus two non-colour keys: _label (selector caption), _dark (titlebar mode).

THEMES: dict[str, dict] = {
    "forge": {
        "_label":     "Forge",
        "_dark":      True,
        "bg":         "#0a0c0e",   # window — deep warm charcoal forge floor
        "surface":    "#111518",   # panels — slightly warmer
        "surface_hi": "#161b1f",   # hover / raised
        "inset":      "#0c0f12",   # log wells, inputs
        "border":     "#1f272b",   # hairlines
        "text":       "#cdd6d8",   # ink — slightly brighter
        "muted":      "#6f7e82",
        "faint":      "#48565c",   # eyebrows, disabled
        "accent":     "#ffb000",   # forge amber — the one hot thing
        "accent_hi":  "#ffc233",
        "accent_dn":  "#7a5600",   # dim amber: focus rings, inactive chrome
        "on_accent":  "#0a0c0e",
        "title":      "#ffffff",
        "ok":         "#39d98a",
        "ok_dim":     "#123826",
        "warn":       "#ffb000",
        "warn_dim":   "#4a3a1d",
        "error":      "#ff5c57",
        "error_dim":  "#3d1a18",
        "request":    "#3fd7e6",
        "num":        "#3fd7e6",   # cyan — every live figure reads in this
    },
    "midnight": {
        "_label":     "Midnight",
        "_dark":      True,
        "bg":         "#070b12",   # near-black with a blue cast
        "surface":    "#0e151f",
        "surface_hi": "#15212f",
        "inset":      "#0a1018",
        "border":     "#223445",
        "text":       "#d4e0ec",
        "muted":      "#6c8095",
        "faint":      "#485c70",
        "accent":     "#3fd7e6",   # cold cyan — the hot thing here
        "accent_hi":  "#74e8f4",
        "accent_dn":  "#1c6b75",
        "on_accent":  "#05222a",
        "title":      "#ffffff",
        "ok":         "#39d98a",
        "ok_dim":     "#10331f",
        "warn":       "#ffc24d",
        "warn_dim":   "#4a3a1d",
        "error":      "#ff6b5e",
        "error_dim":  "#3a1a18",
        "request":    "#ffb000",
        "num":        "#ffb000",   # amber figures pop against the blue
    },
    "carbon": {
        "_label":     "Carbon",
        "_dark":      True,
        "bg":         "#0c0a0a",   # near-black with a hint of warm ash
        "surface":    "#161212",   # slightly warmer than bg
        "surface_hi": "#1e1818",   # hover raise
        "inset":      "#0e0b0b",   # log wells, inputs
        "border":     "#2a2020",   # hairline — warm dark
        "text":       "#d6c8c4",   # off-white, slightly pink-warm
        "muted":      "#7a6565",   # dusty rose-grey
        "faint":      "#4e3e3e",   # eyebrows
        "accent":     "#ff4040",   # forge-red — the hot thing
        "accent_hi":  "#ff6e6e",
        "accent_dn":  "#7a1a1a",   # dim red: focus rings, inactive
        "on_accent":  "#0c0a0a",
        "title":      "#ffffff",
        "ok":         "#3dd98a",   # cool green contrasts the warm palette
        "ok_dim":     "#123826",
        "warn":       "#ffaa33",   # warning amber
        "warn_dim":   "#4a3018",
        "error":      "#ff5c57",   # slightly redder than accent
        "error_dim":  "#3d1816",
        "request":    "#66ccff",   # cold cyan figures pop against warm bg
        "num":        "#66ccff",
    },
    "vapor": {
        "_label":     "Vapor",
        "_dark":      True,
        "bg":         "#0a0810",   # near-black with violet cast
        "surface":    "#120e1c",   # violet-infused panel
        "surface_hi": "#1a1428",   # hover raise
        "inset":      "#0c0914",   # log wells, inputs
        "border":     "#282040",   # hairline violet dark
        "text":       "#d8d0e8",   # cool lavender-white
        "muted":      "#706888",   # dusty violet-grey
        "faint":      "#4a4460",   # eyebrows
        "accent":     "#b44aff",   # electric violet — the hot thing
        "accent_hi":  "#cc7aff",
        "accent_dn":  "#5a2090",   # dim violet
        "on_accent":  "#0a0810",
        "title":      "#ffffff",
        "ok":         "#39e8a0",   # mint green against violet
        "ok_dim":     "#103a2a",
        "warn":       "#ffd94d",   # warning gold
        "warn_dim":   "#4a3a18",
        "error":      "#ff5e80",   # hot pink-red
        "error_dim":  "#3d1428",
        "request":    "#3fd7e6",   # cyan figures for synthwave contrast
        "num":        "#3fd7e6",
    },
    "ember": {
        "_label":     "Ember",
        "_dark":      True,
        "bg":         "#0d0b09",   # deepest warm black
        "surface":    "#181410",   # warm dark panel
        "surface_hi": "#221c16",   # hover raise
        "inset":      "#100d0a",   # log wells, inputs
        "border":     "#2e2418",   # hairline warm dark
        "text":       "#d8cfc0",   # warm off-white
        "muted":      "#7a7060",   # dusty brown-grey
        "faint":      "#4e4840",   # eyebrows
        "accent":     "#e8652f",   # ember orange — the hot thing
        "accent_hi":  "#ff884d",
        "accent_dn":  "#7a3818",   # dim ember
        "on_accent":  "#0d0b09",
        "title":      "#ffffff",
        "ok":         "#39d98a",   # green contrasts warm palette
        "ok_dim":     "#123826",
        "warn":       "#ffc24d",   # warning amber
        "warn_dim":   "#4a3a1d",
        "error":      "#ff5c57",   # red for errors
        "error_dim":  "#3d1a18",
        "request":    "#7ab8ff",   # cool blue figures pop against warm bg
        "num":        "#7ab8ff",
    },
    "light": {
        "_label":     "Light",
        "_dark":      False,
        "bg":         "#eef1f4",
        "surface":    "#ffffff",
        "surface_hi": "#f1f4f7",
        "inset":      "#e6eaee",
        "border":     "#d2d9df",
        "text":       "#1c2329",
        "muted":      "#5d6b76",
        "faint":      "#6f7c86",
        "accent":     "#d98a00",   # amber that survives on white
        "accent_hi":  "#ffb000",
        "accent_dn":  "#a86a00",
        "on_accent":  "#ffffff",
        "title":      "#11161b",
        "ok":         "#1f9d6b",
        "ok_dim":     "#d6f0e4",
        "warn":       "#b57400",
        "warn_dim":   "#f3e2bf",
        "error":      "#d23b2e",
        "error_dim":  "#f6dcd8",
        "request":    "#0b7c8a",
        "num":        "#0b7c8a",
    },
    "sage": {
        "_label":     "Sage",
        "_dark":      False,
        "bg":         "#e8ebe6",   # warm off-white with green tint
        "surface":    "#ffffff",
        "surface_hi": "#eef2ec",
        "inset":      "#dde2da",
        "border":     "#c2c9be",
        "text":       "#1c2118",   # near-black, warm green undertone
        "muted":      "#5e6b58",   # sage green-grey
        "faint":      "#8a9684",   # eyebrows
        "accent":     "#3d8b5e",   # forest sage — the hot thing
        "accent_hi":  "#52a872",
        "accent_dn":  "#2a6644",   # deep sage: focus rings, inactive
        "on_accent":  "#ffffff",
        "title":      "#111810",
        "ok":         "#2e8060",   # deeper green for success
        "ok_dim":     "#d6ede0",
        "warn":       "#b07800",   # warning amber
        "warn_dim":   "#f0e4c0",
        "error":      "#c03030",   # classic red
        "error_dim":  "#f0d8d8",
        "request":    "#2a7a8a",   # teal for data
        "num":        "#2a7a8a",
    },
    "arctic": {
        "_label":     "Arctic",
        "_dark":      False,
        "bg":         "#e4eaf2",   # cold off-white with blue tint
        "surface":    "#ffffff",
        "surface_hi": "#eaf0f8",
        "inset":      "#d8e2ee",
        "border":     "#bfcad8",
        "text":       "#181e28",   # near-black with blue undertone
        "muted":      "#5a6878",   # steel blue-grey
        "faint":      "#8896a6",   # eyebrows
        "accent":     "#2a7cbf",   # ice blue — the hot thing
        "accent_hi":  "#4a99d9",
        "accent_dn":  "#1a5a8a",   # deep ice: focus rings, inactive
        "on_accent":  "#ffffff",
        "title":      "#0e1420",
        "ok":         "#2a9070",   # cold green
        "ok_dim":     "#d4eae0",
        "warn":       "#c08000",   # warning amber
        "warn_dim":   "#f5e8c8",
        "error":      "#d03838",   # red
        "error_dim":  "#f5d8d8",
        "request":    "#1a6e8a",   # deep teal for data
        "num":        "#1a6e8a",
    },
}


def theme_names() -> list[str]:
    """Theme keys in selector display order (the dict's own order)."""
    return list(THEMES)


def label(name: str) -> str:
    return THEMES.get(name, THEMES["midnight"])["_label"]


def is_dark(name: str) -> bool:
    return bool(THEMES.get(name, THEMES["midnight"])["_dark"])


def _mix(a: str, b: str, weight: float = 0.55) -> str:
    """Blend two #rrggbb colours; used for quieter panel accents."""
    av = tuple(int(a[i:i + 2], 16) for i in (1, 3, 5))
    bv = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x * weight + y * (1 - weight)):02x}"
                          for x, y in zip(av, bv))


_MONO_FAMILY: str | None = None
_UI_FAMILY: str | None = None


def _pick(families: set[str], *candidates: str) -> str | None:
    for c in candidates:
        if c in families:
            return c
    return None


def init_fonts(root: tk.Tk) -> None:
    """Resolve the best installed families once. Call after Tk() exists."""
    global _MONO_FAMILY, _UI_FAMILY
    fams = set(tkfont.families(root))
    _UI_FAMILY = _pick(fams, "Segoe UI Variable Text", "Segoe UI") or "TkDefaultFont"
    _MONO_FAMILY = _pick(fams, "Cascadia Mono", "Cascadia Code", "Consolas") or "TkFixedFont"


def ui(size: int = 10, weight: str = "normal") -> tuple:
    return (_UI_FAMILY or "Segoe UI", size, weight)


def mono(size: int = 9, weight: str = "normal") -> tuple:
    return (_MONO_FAMILY or "Consolas", size, weight)


def track(text: str, gap: int = 1) -> str:
    """Fake letter-spacing: uppercase with spaces between glyphs.

    Tk has no tracking; LlamaForge-style display text is short enough that
    literal spaces read fine ("MODELS" -> "M O D E L S").
    """
    return (" " * gap).join(text.upper())


def enable_windows_niceties(root: tk.Tk, dark: bool = True) -> None:
    """Match the OS titlebar to the theme via DWM. No-op off Windows."""
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (19 on older builds)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def set_dpi_aware() -> None:
    """Ask Windows for real pixels so text renders crisp. Call before Tk()."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def apply(root: tk.Tk, name: str = "midnight") -> dict:
    """Configure ttk styles on *root* and return the token palette."""
    c = dict(THEMES.get(name, THEMES["midnight"]))
    for token in ("accent", "ok", "warn", "error", "request", "num"):
        c[f"panel_{token}"] = _mix(c[token], c["muted"])
    init_fonts(root)
    root.configure(bg=c["bg"])

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=c["bg"], foreground=c["text"],
                    fieldbackground=c["inset"], bordercolor=c["border"],
                    lightcolor=c["surface"], darkcolor=c["surface"],
                    troughcolor=c["inset"], font=ui(10),
                    focuscolor=c["accent_dn"])

    style.configure("TFrame", background=c["bg"])
    style.configure("TLabel", background=c["bg"], foreground=c["text"])

    # Data tables — hairline rounded panel feel
    style.configure("Treeview", background=c["surface"], foreground=c["text"],
                    fieldbackground=c["surface"], borderwidth=0, rowheight=30,
                    font=mono(9))
    style.configure("Treeview.Heading", background=c["bg"],
                    foreground=c["faint"], borderwidth=0,
                    font=mono(8, "bold"))
    style.map("Treeview.Heading", background=[("active", c["surface"])])
    style.map("Treeview",
              background=[("selected", c["surface_hi"])],
              foreground=[("selected", c["text"])])

    # Inputs
    style.configure("TEntry", insertcolor=c["text"], padding=6,
                    bordercolor=c["border"])
    style.map("TEntry", bordercolor=[("focus", c["accent_dn"])])
    style.configure("TCombobox", padding=6, arrowcolor=c["muted"])
    style.map("TCombobox", fieldbackground=[("readonly", c["inset"])],
              foreground=[("readonly", c["text"])])
    style.configure("TCheckbutton", background=c["surface"],
                    foreground=c["text"], font=ui(10),
                    indicatorcolor=c["border"])
    style.map("TCheckbutton",
              background=[("active", c["surface"])],
              indicatorcolor=[("selected", c["accent"])])

    # Scrollbars need a practical hit target: they are used on tables, editors
    # and page forms, not just as a visual hint for mouse-wheel scrolling.
    style.configure("Vertical.TScrollbar", background=c["border"],
                    troughcolor=c["bg"], borderwidth=0, arrowsize=12, width=12)
    style.map("Vertical.TScrollbar", background=[("active", c["faint"])],
              troughcolor=[("active", c["bg"])])
    style.configure("Horizontal.TScrollbar", background=c["border"],
                    troughcolor=c["bg"], borderwidth=0, arrowsize=12, width=12)
    style.map("Horizontal.TScrollbar", background=[("active", c["faint"])])

    style.configure("Horizontal.TProgressbar", background=c["accent"],
                    troughcolor=c["inset"], borderwidth=0, thickness=3)
    style.configure("Horizontal.TScale", background=c["surface"],
                    troughcolor=c["inset"], borderwidth=0)
    style.map("Horizontal.TScale",
              background=[("active", c["accent_hi"])])

    return c
