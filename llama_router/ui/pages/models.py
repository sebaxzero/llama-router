"""Models — GGUF registry: scan folders, enable/disable, manage sources."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, ttk

from llama_router.core.utils import fmt_bytes
from llama_router.i18n import t
from llama_router.ui import theme
from llama_router.ui.pages.base import PAGE_PAD, Page
from llama_router.ui.widgets import AutoScrollbar, PillButton, enable_row_hover


def _fmt_ctx(n: int) -> str:
    if n <= 0:
        return "—"
    return f"{n // 1024}K" if n >= 1024 else str(n)


# Rough working-set estimate on top of the weights (runtime + KV cache).
_FIT_OVERHEAD = 1.15
_FIT_BASE = 1.5 * 1024 ** 3


def _fit(size: int, vram_bytes: int) -> str:
    """LlamaForge-style VRAM verdict: fits / tight / cpu."""
    if size <= 0 or vram_bytes <= 0:
        return ""
    if size * _FIT_OVERHEAD + _FIT_BASE <= vram_bytes:
        return "fits"
    if size <= vram_bytes:
        return "tight"
    return "cpu"


class ModelsPage(Page):
    def __init__(self, parent: tk.Widget, ctx, embedded: bool = False) -> None:
        super().__init__(parent, ctx)
        c = self.c
        if embedded:
            head = tk.Frame(self, bg=c["bg"])
            head.pack(fill="x", padx=PAGE_PAD, pady=(0, 12))
            actions = tk.Frame(head, bg=c["bg"])
            actions.pack(side="right")
        else:
            page_head = self.header(t("library"), t("Models"),
                                    t("GGUF files found in your model folders"))
            actions = page_head.actions
        self._add_btn = PillButton(actions, c, t("Add folder"),
                                   command=self._add_folder)
        self._add_btn.pack(side="left", padx=(0, 8))
        self._scan_btn = PillButton(actions, c, t("Scan folders"),
                                    kind="primary", command=self._scan)
        self._scan_btn.pack(side="left")

        # ── Folder chips ─────────────────────────────────────────────────────
        self._chips = tk.Frame(self, bg=c["bg"])
        self._chips.pack(fill="x", padx=PAGE_PAD, pady=(0, 10))

        # ── Table panel ──────────────────────────────────────────────────────
        panel = tk.Frame(self, bg=c["surface"],
                         highlightbackground=c["panel_accent"],
                         highlightthickness=1)
        panel.pack(fill="x", padx=PAGE_PAD, pady=(0, 10))

        cols = ("on", "name", "quant", "params", "ctx", "size", "fit", "state")
        self._tree = ttk.Treeview(panel, columns=cols, show="headings",
                                  selectmode="browse")
        self._tree.heading("on", text="")
        self._tree.heading("name", text=t("Name"))
        self._tree.heading("quant", text=t("Quant"))
        self._tree.heading("params", text=t("Params"))
        self._tree.heading("ctx", text=t("Ctx"))
        self._tree.heading("size", text=t("Size"))
        self._tree.heading("fit", text=t("VRAM"))
        self._tree.heading("state", text=t("State"))
        self._tree.column("on", width=44, anchor="center", stretch=False)
        self._tree.column("name", width=240, minwidth=180, stretch=True)
        self._tree.column("quant", width=90, anchor="center", stretch=True)
        self._tree.column("params", width=70, anchor="center", stretch=True)
        self._tree.column("ctx", width=70, anchor="e", stretch=False)
        self._tree.column("size", width=90, anchor="e", stretch=True)
        self._tree.column("fit", width=70, anchor="center", stretch=False)
        self._tree.column("state", width=90, anchor="center", stretch=True)
        vbar = AutoScrollbar(panel, orient="vertical", command=self._tree.yview)
        hbar = AutoScrollbar(panel, orient="horizontal",
                             command=self._tree.xview)
        self._tree.configure(yscrollcommand=vbar.set,
                             xscrollcommand=hbar.set)
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")
        self._tree.pack(fill="both", expand=True, padx=1, pady=1)

        self._tree.tag_configure("missing", foreground=c["error"])
        self._tree.tag_configure("disabled", foreground=c["faint"])
        self._tree.bind("<Button-1>", self._on_click)
        self._tree.bind("<Double-1>", self._on_double)
        enable_row_hover(self._tree, c)

        self._empty = tk.Label(panel, text="", bg=c["surface"], fg=c["muted"],
                               font=theme.ui(10), justify="center")

        # ── Footer actions ───────────────────────────────────────────────────
        foot = tk.Frame(self, bg=c["bg"])
        foot.pack(fill="x", padx=PAGE_PAD, pady=(0, PAGE_PAD))
        PillButton(foot, c, t("Enable all"), kind="accent",
                   size=9, padx=12, height=28,
                   command=lambda: self._set_all(True)).pack(side="left")
        PillButton(foot, c, t("Disable all"), size=9, padx=12, height=28,
                   command=lambda: self._set_all(False)
                   ).pack(side="left", padx=(6, 0))
        self._remove_btn = PillButton(foot, c, t("Remove from list"),
                                      size=9, padx=12, height=28,
                                      command=self._remove)
        self._remove_btn.pack(side="left", padx=(6, 0))
        self._status = tk.Label(foot, text="", bg=c["bg"], fg=c["muted"],
                                font=theme.ui(9))
        self._status.pack(side="right")

        self._vram_bytes = 0
        self.subscribe("models_scanned", self._on_scanned)
        self.subscribe("gpu_stats", self._on_gpu_stats)
        self._refresh()

    # ── Services ─────────────────────────────────────────────────────────────

    @property
    def _models(self):
        return self.ctx.services["models"]

    # ── Rendering ────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        c = self.c
        # Folder chips
        for w in self._chips.winfo_children():
            w.destroy()
        cfg = self.ctx.services["config"].get()
        folders = [(str(self.ctx.paths.models_dir), False)]
        folders += [(f, True) for f in cfg.model_folders]
        for folder, removable in folders:
            chip = tk.Frame(self._chips, bg=c["surface"],
                            highlightbackground=c["border"],
                            highlightthickness=1)
            chip.pack(side="left", padx=(0, 6))
            tk.Label(chip, text=folder, bg=c["surface"], fg=c["muted"],
                     font=theme.mono(8), padx=8, pady=3).pack(side="left")
            if removable:
                PillButton(
                    chip, c, "×", size=7, padx=5, height=22,
                    command=lambda f=folder: self._remove_folder(f)).pack(
                        side="left", padx=(0, 3))

        # Table
        self._tree.delete(*self._tree.get_children())
        models = self._models.list()
        self._tree.configure(height=max(3, min(10, len(models))))
        for m in models:
            on = "☑" if m.enabled else "☐"
            state = t("missing") if m.state == "missing" else t("ready")
            tags = []
            if m.state == "missing":
                tags.append("missing")
            elif not m.enabled:
                tags.append("disabled")
            meta = m.meta or {}
            self._tree.insert("", "end", iid=m.id, tags=tags, values=(
                on, m.name,
                meta.get("quant", "—"),
                meta.get("params", "—"),
                _fmt_ctx(int(meta.get("ctx", 0))),
                fmt_bytes(m.size),
                _fit(m.size, self._vram_bytes),
                state))
        if models:
            self._empty.place_forget()
        else:
            self._empty.configure(
                text=t("No models yet.\nDrop GGUF files in a folder and scan."))
            self._empty.place(relx=0.5, rely=0.4, anchor="center")

    # ── Actions ──────────────────────────────────────────────────────────────

    def _scan(self) -> None:
        self._scan_btn.set_enabled(False)
        self._scan_btn.set_text(t("Scanning…"))
        threading.Thread(target=self._models.scan, daemon=True,
                         name="model-scan").start()

    def _on_gpu_stats(self, gpus) -> None:
        # Total VRAM is all the fit column needs; refresh once when it lands.
        total = sum(g.get("mem_total", 0) for g in (gpus or [])) * 1024 ** 2
        if total and total != self._vram_bytes:
            self._vram_bytes = total
            self._refresh()

    def _on_scanned(self, data: dict) -> None:
        self._scan_btn.set_enabled(True)
        self._scan_btn.set_text(t("Scan folders"))
        self._status.configure(text=t("{total} models · {new} new",
                                      total=data.get("total", 0),
                                      new=data.get("new", 0)))
        self._refresh()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self)
        if folder:
            self._models.add_folder(folder)
            self._refresh()

    def _remove_folder(self, folder: str) -> None:
        self._models.remove_folder(folder)
        self._refresh()

    def _selected(self) -> str | None:
        sel = self._tree.selection()
        return sel[0] if sel else None

    def _on_click(self, e) -> None:
        # Click on the ● column toggles enabled
        if self._tree.identify_column(e.x) == "#1":
            row = self._tree.identify_row(e.y)
            if row:
                m = self._models.get(row)
                if m:
                    self._models.set_enabled(row, not m.enabled)
                    self._refresh()
                    self._tree.selection_set(row)

    def _on_double(self, e) -> None:
        row = self._tree.identify_row(e.y)
        if row and self._tree.identify_column(e.x) != "#1":
            m = self._models.get(row)
            if m:
                self._models.set_enabled(row, not m.enabled)
                self._refresh()
                self._tree.selection_set(row)

    def _set_all(self, enabled: bool) -> None:
        self._models.set_enabled_all(enabled)
        self._refresh()

    def _remove(self) -> None:
        row = self._selected()
        if not row:
            return
        self._models.remove(row)
        profiles = self.ctx.services.get("profiles")
        if profiles:
            profiles.delete_for_model(row)
        self._refresh()

    def on_show(self) -> None:
        self._refresh()
