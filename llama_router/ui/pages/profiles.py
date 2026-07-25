"""Profiles — per-model inference presets that become models-preset.ini
sections. Left: one tree of models with their profiles (checkbox = active).
Right: the full parameter editor, organised in sections."""
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from llama_router.i18n import t
from llama_router.ui import theme
from llama_router.ui.pages.base import PAGE_PAD, Page
from llama_router.ui.pages.models import ModelsPage
from llama_router.ui.pages.preset import PresetPage
from llama_router.ui.widgets import (AutoScrollbar, NavItem, PillButton, ScrollFrame, section_label,
                                    enable_row_hover)

_CACHE_TYPES = ["f16", "bf16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0",
                "iq4_nl"]
_DRAFT_CACHE_TYPES = ["f16", "f32", "bf16", "q8_0", "q5_1", "q5_0",
                      "q4_1", "q4_0", "iq4_nl"]
_SPEC_TYPES = ["none", "draft-simple", "draft-eagle3", "draft-mtp",
               "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod",
               "ngram-cache"]

# llama-server defaults (tools/server/README.md). A field showing its default
# is treated as unset: it is not stored in the profile and therefore never
# written to models-preset.ini. Choice fields encode their default as the
# omit value instead.
_LLAMA_DEFAULTS: dict[str, int | float | str] = {
    "n-predict": -1,
    "batch-size": 2048,
    "ubatch-size": 512,
    "seed": -1,
    "temp": 0.8,
    "top-k": 40,
    "top-p": 0.95,
    "min-p": 0.05,
    "typical-p": 1.0,
    "top-nsigma": -1.0,
    "xtc-probability": 0.0,
    "xtc-threshold": 0.1,
    "mirostat-lr": 0.1,
    "mirostat-ent": 5.0,
    "repeat-last-n": 64,
    "repeat-penalty": 1.1,
    "presence-penalty": 0.0,
    "frequency-penalty": 0.0,
    "dry-multiplier": 0.0,
    "dry-base": 1.75,
    "dry-allowed-length": 2,
    "dry-penalty-last-n": -1,
    "cache-reuse": 0,
    "main-gpu": 0,
}

# Field kinds: int | float | str | bool | choice | combo | file
# choice extra = (values, omit_value): omit_value is not stored in params
# (None means the value is always stored). combo = editable choice.
# file extra = optional detect callback name on this page.
# Sections mirror pi-test's profile editor (ui/js/pages/models.js).
_SECTIONS: list[tuple[str, list[tuple]]] = [
    ("Core", [
        ("ctx-size", "Context size", "int", None),
        ("n-predict", "Max tokens", "int", None),
        ("n-gpu-layers", "GPU layers", "int", None),
        ("batch-size", "Batch size", "int", None),
        ("ubatch-size", "Micro-batch size", "int", None),
        ("flash-attn", "Flash attention", "choice", (["auto", "on", "off"], "auto")),
        ("seed", "Seed", "int", None),
    ]),
    ("Sampling", [
        ("temp", "Temperature", "float", None),
        ("top-k", "Top-K", "int", None),
        ("top-p", "Top-P", "float", None),
        ("min-p", "Min-P", "float", None),
        ("typical-p", "Typical-P", "float", None),
        ("top-nsigma", "Top-N sigma", "float", None),
        ("xtc-probability", "XTC probability", "float", None),
        ("xtc-threshold", "XTC threshold", "float", None),
        ("mirostat", "Mirostat", "choice", (["0", "1", "2"], "0")),
        ("mirostat-lr", "Mirostat LR", "float", None),
        ("mirostat-ent", "Mirostat entropy", "float", None),
    ]),
    ("Repetition", [
        ("repeat-last-n", "Repeat last N", "int", None),
        ("repeat-penalty", "Repeat penalty", "float", None),
        ("presence-penalty", "Presence penalty", "float", None),
        ("frequency-penalty", "Frequency penalty", "float", None),
        ("dry-multiplier", "DRY multiplier", "float", None),
        ("dry-base", "DRY base", "float", None),
        ("dry-allowed-length", "DRY allowed length", "int", None),
        ("dry-penalty-last-n", "DRY penalty last N", "int", None),
        ("dry-sequence-breaker", "DRY sequence breaker", "str", None),
    ]),
    ("Chat & templates", [
        ("jinja", "Jinja templates", "choice", (["", "true", "false"], "")),
        ("reasoning", "Reasoning", "choice", (["", "auto", "on", "off"], "")),
        ("chat-template-file", "Chat template file", "file", None),
        ("chat-template-kwargs", "Template kwargs (JSON)", "str", None),
    ]),
    ("KV cache", [
        ("cache-type-k", "K cache type", "choice", (_CACHE_TYPES, "f16")),
        ("cache-type-v", "V cache type", "choice", (_CACHE_TYPES, "f16")),
        ("cache-reuse", "Cache reuse", "int", None),
        ("swa-checkpoints", "SWA checkpoints", "int", None),
        ("swa-full", "Full SWA cache", "bool", None),
        ("no-kv-offload", "Keep KV cache on CPU", "bool", None),
        ("no-cache-prompt", "Disable prompt cache", "bool", None),
    ]),
    ("Performance", [
        ("n-cpu-moe", "MoE CPU experts", "int", None),
        ("cpu-moe", "All MoE experts on CPU", "bool", None),
        ("mlock", "Lock model in RAM", "bool", None),
        ("no-mmap", "Disable mmap", "bool", None),
        ("fit", "Auto-fit to VRAM", "choice", (["on", "off"], "on")),
        ("fit-target", "Fit target (MiB)", "int", None),
        ("main-gpu", "Main GPU", "int", None),
        ("split-mode", "Split mode", "choice", (["layer", "row", "none"], "layer")),
        ("tensor-split", "Tensor split", "str", None),
    ]),
    ("RoPE", [
        ("rope-scaling", "RoPE scaling", "choice",
         (["", "none", "linear", "yarn"], "")),
        ("rope-freq-base", "RoPE freq base", "float", None),
        ("rope-freq-scale", "RoPE freq scale", "float", None),
    ]),
    ("Multimodal", [
        ("mmproj", "MMProj file", "file", "_detect_mmproj"),
        ("no-mmproj-offload", "Keep projector on CPU", "bool", None),
    ]),
    ("Speculative decoding", [
        ("spec-type", "Type", "combo", (_SPEC_TYPES, "none")),
        ("spec-draft-model", "Draft model", "file", "_detect_draft"),
        ("spec-draft-n-max", "Draft tokens (n-max)", "int", None),
        ("cache-type-k-draft", "Draft K cache", "choice", (_DRAFT_CACHE_TYPES, "")),
        ("cache-type-v-draft", "Draft V cache", "choice", (_DRAFT_CACHE_TYPES, "")),
    ]),
    ("Router", [
        ("load-on-startup", "Load on startup", "bool", None),
        ("embedding", "Embedding mode", "bool", None),
        ("stop-timeout", "Stop timeout (s)", "int", None),
        ("sleep-idle-seconds", "Sleep after idle (s)", "int", None),
    ]),
]

# Dense numeric groups read well three-across.  Sections with longer labels,
# editable strings or broader choices deliberately keep two columns.
_SECTION_COLUMNS = {
    "Core": 3,
    "Sampling": 3,
    "Repetition": 2,
    "Chat & templates": 2,
    "KV cache": 2,
    "Performance": 2,
    "RoPE": 3,
    "Multimodal": 2,
    "Speculative decoding": 2,
    "Router": 2,
}

# The sampler params a sampling preset owns — applying a preset clears all of
# them first, so presets combine cleanly with any profile (pi-test rule).
_SAMPLING_KEYS = [
    "temp", "top-k", "top-p", "min-p", "typical-p", "top-nsigma",
    "xtc-probability", "xtc-threshold", "mirostat", "mirostat-lr",
    "mirostat-ent",
    "repeat-last-n", "repeat-penalty", "presence-penalty", "frequency-penalty",
    "dry-multiplier", "dry-base", "dry-allowed-length", "dry-penalty-last-n",
]

# Keys owned by dedicated fields — kept out of the free-form box.
_STRUCTURED = {f[0] for _, fields in _SECTIONS for f in fields}


def _parse_extra(text: str) -> dict:
    """Parse 'key = value' lines into a params dict (str values)."""
    out: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        for sep in ("=", ":"):
            if sep in line:
                k, _, v = line.partition(sep)
                k, v = k.strip(), v.strip()
                if k:
                    out[k] = v
                break
    return out


def _format_extra(params: dict) -> str:
    return "\n".join(f"{k} = {v}" for k, v in params.items()
                     if k not in _STRUCTURED)


def _load_sampling_presets() -> list[dict]:
    """Curated per-model-family sampling presets, bundled in assets/."""
    fp = Path(__file__).resolve().parents[2] / "assets" / "sampling-presets.json"
    try:
        return json.loads(fp.read_text(encoding="utf-8")).get("presets", [])
    except (OSError, ValueError):
        return []


class ProfilesPage(Page):
    def __init__(self, parent: tk.Widget, ctx) -> None:
        super().__init__(parent, ctx)
        c = self.c
        self.header(t("model workspace"), t("Models & Profiles"),
                    t("Models, routes and generated preset in one place"))
        workspace_nav = tk.Frame(self, bg=c["bg"])
        workspace_nav.pack(fill="x", padx=PAGE_PAD, pady=(0, 12))
        self._workspace_nav = {
            "models": NavItem(workspace_nav, c, t("Models"),
                              command=lambda: self._show_workspace("models")),
            "profiles": NavItem(workspace_nav, c, t("Profiles"),
                                command=lambda: self._show_workspace("profiles")),
        }
        self._workspace_nav["models"].pack(side="left", padx=(0, 4))
        self._workspace_nav["profiles"].pack(side="left")
        self._workspace_nav["profiles"].set_active(True)

        cols = self._cols = tk.Frame(self, bg=c["bg"])
        cols.pack(fill="both", expand=True, padx=PAGE_PAD, pady=(0, PAGE_PAD))
        cols.columnconfigure(0, weight=0)
        cols.columnconfigure(1, weight=1, uniform="profile-detail")
        cols.columnconfigure(2, weight=1, uniform="profile-detail")
        cols.rowconfigure(0, weight=1)

        # ── Left: models → profiles tree ─────────────────────────────────────
        left = tk.Frame(cols, bg=c["bg"], width=230)
        left.grid(row=0, column=0, sticky="nw", padx=(0, 14))

        treepanel = tk.Frame(left, bg=c["surface"])
        treepanel.pack(fill="x")
        self._tree = ttk.Treeview(treepanel, columns=("on",), show="tree",
                                  selectmode="browse")
        self._tree.column("#0", width=162)
        self._tree.column("on", width=40, anchor="center", stretch=False)
        vbar = AutoScrollbar(treepanel, orient="vertical",
                             command=self._tree.yview)
        self._tree.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        self._tree.pack(fill="x", padx=1, pady=1)
        self._tree.tag_configure("model", foreground=c["muted"])
        self._tree.tag_configure("off", foreground=c["faint"])
        self._tree.bind("<Button-1>", self._on_tree_click)
        self._tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        enable_row_hover(self._tree, c)

        lbtns = tk.Frame(left, bg=c["bg"])
        lbtns.pack(fill="x", pady=(10, 0))
        PillButton(lbtns, c, t("New"), size=9, padx=12, height=28,
                   command=self._new).pack(side="left")
        PillButton(lbtns, c, t("Delete"), size=9, padx=12, height=28,
                   command=self._delete).pack(side="left", padx=(6, 0))

        lbtns2 = tk.Frame(left, bg=c["bg"])
        lbtns2.pack(fill="x", pady=(6, 0))
        PillButton(lbtns2, c, t("Activate all"), size=9, padx=12, height=28,
                   command=lambda: self._set_all_active(True)).pack(side="left")
        PillButton(lbtns2, c, t("Deactivate all"), size=9, padx=12, height=28,
                   command=lambda: self._set_all_active(False)
                   ).pack(side="left", padx=(6, 0))

        # ── Right: editor ────────────────────────────────────────────────────
        self._editor = tk.Frame(cols, bg=c["surface"],
                                highlightbackground=c["border"],
                                highlightthickness=1)
        self._editor.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        self._fields: dict[str, tuple] = {}  # key → (kind, widget-or-var, extra)
        self._autosave_id: str | None = None
        self._loading_profile = False
        self._build_editor()

        self._current: str | None = None
        self._preset_host = tk.Frame(cols, bg=c["bg"])
        self._preset_host.grid(row=0, column=2, sticky="nsew")
        self._preset_view: PresetPage | None = PresetPage(
            self._preset_host, self.ctx, embedded=True)
        self._preset_view.pack(fill="both", expand=True)
        self._library_view: ModelsPage | None = None
        self.subscribe("preset_imported", self._on_preset_imported)
        self._refresh_tree()

    def _show_workspace(self, name: str) -> None:
        showing_profiles = self._cols.winfo_ismapped()
        if name == "models" and showing_profiles:
            self._flush_autosave()
            self._cols.pack_forget()
            if self._library_view is None:
                self._library_view = ModelsPage(self, self.ctx, embedded=True)
            self._library_view.pack(fill="both", expand=True)
        elif name == "profiles" and not showing_profiles:
            if self._library_view is not None:
                self._library_view.pack_forget()
            self._cols.pack(fill="both", expand=True, padx=PAGE_PAD,
                            pady=(0, PAGE_PAD))
            self._refresh_tree(keep=(f"p:{self._current}"
                                     if self._current else None))
        for key, item in self._workspace_nav.items():
            item.set_active(key == name)

    # ── Services ─────────────────────────────────────────────────────────────

    @property
    def _profiles(self):
        return self.ctx.services["profiles"]

    @property
    def _models(self):
        return self.ctx.services["models"]

    # ── Editor construction ──────────────────────────────────────────────────

    def _build_editor(self) -> None:
        c = self.c
        outer = tk.Frame(self._editor, bg=c["surface"])
        outer.pack(fill="both", expand=True, padx=18, pady=16)
        self._editor_body = outer

        top = tk.Frame(outer, bg=c["surface"])
        top.pack(fill="x")
        section_label(top, c, t("Profile")).pack(side="left")
        self._save_state = tk.Label(top, text=t("Saved automatically"),
                                    bg=c["surface"], fg=c["faint"],
                                    font=theme.ui(8))
        self._save_state.pack(side="right")

        self._copy_map: dict[str, str] = {}  # display → profile_id
        self._presets = _load_sampling_presets()

        # Identity row stays fixed above the scrolling parameter form.
        ident = tk.Frame(outer, bg=c["surface"])
        ident.pack(fill="x", pady=(12, 6))
        ident.columnconfigure(1, weight=1)
        ident.columnconfigure(0, minsize=90)
        self._label(ident, 0, 0, t("Name"))
        self._name = ttk.Entry(ident, width=22, font=theme.mono(9))
        self._name.grid(row=0, column=1, sticky="ew")
        self._label(ident, 1, 0, t("Route alias"))
        self._alias = ttk.Entry(ident, width=22, font=theme.mono(9))
        self._alias.grid(row=1, column=1, sticky="ew")
        self._label(ident, 2, 0, t("Active"))
        self._active = tk.BooleanVar()
        ttk.Checkbutton(ident, variable=self._active,
                        takefocus=False).grid(row=2, column=1, sticky="w")

        toggle = tk.Frame(outer, bg=c["surface"], cursor="hand2")
        toggle.pack(fill="x", pady=(8, 0))
        self._params_toggle = tk.Label(
            toggle, text="▾  " + t("Advanced parameters"),
            bg=c["surface"], fg=c["muted"], font=theme.mono(8, "bold"),
            cursor="hand2")
        self._params_toggle.pack(anchor="w")
        for widget in (toggle, self._params_toggle):
            widget.bind("<Button-1>", lambda _e: self._toggle_params())

        sc = self._params_sc = ScrollFrame(outer, dict(c, bg=c["surface"]))
        body = sc.body

        pickers = tk.Frame(body, bg=c["surface"])
        pickers.pack(fill="x", pady=(10, 2))
        pickers.columnconfigure(0, weight=1, uniform="profile-picker")
        pickers.columnconfigure(1, weight=1, uniform="profile-picker")
        self._preset_cb = ttk.Combobox(pickers, state="readonly", width=18,
                                       font=theme.ui(8))
        self._preset_cb.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._preset_cb.bind("<<ComboboxSelected>>", self._on_sampling_preset)
        self._copy_cb = ttk.Combobox(pickers, state="readonly", width=18,
                                     font=theme.ui(8))
        self._copy_cb.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._copy_cb.bind("<<ComboboxSelected>>", self._on_copy_from)

        for title, fields in _SECTIONS:
            section_label(body, c, t(title)).pack(anchor="w", pady=(12, 2))
            grid = tk.Frame(body, bg=c["surface"])
            grid.pack(fill="x")
            columns = _SECTION_COLUMNS[title]
            for col in range(columns):
                grid.columnconfigure(col, weight=1, uniform="param-cell")

            # Boolean switches are easier to scan as one vertical group, so
            # render every other field first and append switches afterwards.
            values = [field for field in fields if field[2] != "bool"]
            switches = [field for field in fields if field[2] == "bool"]
            row = 0
            slot = 0
            for key, label, kind, extra in values:
                if kind == "file":
                    if slot:
                        row += 1
                        slot = 0
                    cell = tk.Frame(grid, bg=c["surface"])
                    cell.grid(row=row, column=0, columnspan=columns,
                              sticky="ew", padx=(0, 6))
                    cell.columnconfigure(0, weight=1)
                    self._label(cell, 0, 0, t(label))
                    self._make_field(cell, 1, 0, key, kind, extra)
                    row += 1
                    continue
                cell = tk.Frame(grid, bg=c["surface"])
                cell.grid(row=row, column=slot, sticky="ew", padx=(0, 6))
                self._label(cell, 0, 0, t(label))
                self._make_field(cell, 1, 0, key, kind, extra)
                slot += 1
                if slot == columns:
                    row += 1
                    slot = 0
            if slot:
                row += 1

            if switches:
                switch_grid = tk.Frame(grid, bg=c["surface"])
                switch_grid.grid(row=row, column=0, columnspan=columns,
                                 sticky="w", pady=(2, 0))
                switch_grid.columnconfigure(0, minsize=150)
                for switch_row, (key, label, kind, extra) in enumerate(switches):
                    self._label(switch_grid, switch_row, 0, t(label))
                    self._make_field(switch_grid, switch_row, 1, key, kind,
                                     extra)

        row2 = tk.Frame(body, bg=c["surface"])
        row2.pack(fill="x", pady=(10, 4))
        tk.Label(row2, text=t("Additional parameters (key = value per line)"),
                 bg=c["surface"], fg=c["muted"], font=theme.ui(9)).pack(side="left")
        tk.Label(row2, text=t("same flags as llama-server"),
                 bg=c["surface"], fg=c["faint"], font=theme.ui(8)).pack(side="right")
        self._extra = self._text(body, 6)
        self._wire_autosave()

        self._editor_hint = tk.Label(self._editor, text="", bg=c["surface"],
                                     fg=c["muted"], font=theme.ui(10))

    def _toggle_params(self) -> None:
        if self._params_sc.winfo_ismapped():
            self._params_sc.pack_forget()
            self._params_toggle.configure(
                text="▾  " + t("Advanced parameters"))
        else:
            self._params_sc.pack(fill="both", expand=True, pady=(4, 0))
            self._params_toggle.configure(
                text="▴  " + t("Advanced parameters"))

    def _label(self, parent, row, col, text) -> None:
        tk.Label(parent, text=text, bg=self.c["surface"], fg=self.c["muted"],
                 font=theme.ui(9)).grid(row=row, column=col, sticky="w",
                                        pady=4, padx=(0, 10))

    def _make_field(self, grid, row, col, key, kind, extra,
                    columnspan: int = 1) -> None:
        c = self.c
        if kind == "bool":
            var = tk.BooleanVar()
            ttk.Checkbutton(grid, variable=var, takefocus=False
                            ).grid(row=row, column=col, sticky="w",
                                   pady=4, padx=(0, 12),
                                   columnspan=columnspan)
            self._fields[key] = (kind, var, extra)
        elif kind in ("choice", "combo"):
            values, _omit = extra
            cb = ttk.Combobox(grid, values=values, width=9,
                              state="readonly" if kind == "choice" else "normal",
                              font=theme.ui(9))
            cb.grid(row=row, column=col, sticky="w", pady=4, padx=(0, 12),
                    columnspan=columnspan)
            self._fields[key] = (kind, cb, extra)
        elif kind == "file":
            cell = tk.Frame(grid, bg=c["surface"])
            cell.grid(row=row, column=col, sticky="ew", pady=4, padx=(0, 12),
                      columnspan=columnspan)
            en = ttk.Entry(cell, font=theme.mono(9))
            en.pack(side="left", fill="x", expand=True)
            PillButton(cell, c, "…", size=9, padx=8, height=24,
                       command=lambda e=en: self._browse(e)
                       ).pack(side="left", padx=(4, 0))
            if extra:  # auto-detect hook
                PillButton(cell, c, t("Auto"), size=8, padx=8, height=24,
                           command=lambda e=en, fn=extra:
                           getattr(self, fn)(e)).pack(side="left", padx=(4, 0))
            self._fields[key] = (kind, en, extra)
        else:  # int / float / str
            en = ttk.Entry(grid, width=8 if kind in ("int", "float") else 11,
                           font=theme.mono(9))
            en.grid(row=row, column=col, sticky="w", pady=4, padx=(0, 12),
                    columnspan=columnspan)
            self._fields[key] = (kind, en, extra)

    def _text(self, parent: tk.Widget, height: int) -> tk.Text:
        c = self.c
        txt = tk.Text(parent, height=height, bg=c["inset"], fg=c["text"],
                      insertbackground=c["text"], bd=0, padx=8, pady=6,
                      font=theme.mono(9), wrap="none",
                      highlightthickness=1, highlightbackground=c["border"],
                      highlightcolor=c["accent_dn"])
        txt.pack(fill="x")
        return txt

    def _browse(self, entry: ttk.Entry) -> None:
        path = filedialog.askopenfilename(
            parent=self, filetypes=[("GGUF / template", "*.gguf *.jinja *.*")])
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)
            self._schedule_save()

    def _detect_mmproj(self, entry: ttk.Entry) -> None:
        self._autofill(entry, self._models.detect_mmproj)

    def _detect_draft(self, entry: ttk.Entry) -> None:
        self._autofill(entry, self._models.detect_draft)

    def _autofill(self, entry: ttk.Entry, detector) -> None:
        p = self._profiles.get(self._current) if self._current else None
        if not p:
            return
        result = detector(p.model_id)
        if result.get("path"):
            entry.delete(0, "end")
            entry.insert(0, result["path"])
            self._schedule_save()
        else:
            msg = t("ambiguous — pick manually") if result.get("ambiguous") \
                else t("nothing found next to the model")
            old = entry.get()
            entry.delete(0, "end")
            entry.insert(0, msg)
            entry.after(1800, lambda: (entry.delete(0, "end"),
                                       entry.insert(0, old)))

    # ── Tree refresh / selection ─────────────────────────────────────────────

    def _refresh_tree(self, keep: str | None = None) -> None:
        self._tree.delete(*self._tree.get_children())
        models = [m for m in self._models.list() if m.enabled]
        row_count = sum(1 + len(self._profiles.list(m.id)) for m in models)
        self._tree.configure(height=max(3, min(12, row_count)))
        if not models:
            any_models = bool(self._models.list())
            message = (t("Enable a model first — then tune its profiles here.")
                       if any_models else
                       t("Add models first — then tune their profiles here."))
            self._show_hint(message)
            return
        for m in models:
            self._profiles.ensure_defaults(m.id)
            plist = self._profiles.list(m.id)
            n_active = sum(1 for p in plist if p.active)
            mid = f"m:{m.id}"
            # A disabled model never reaches models-preset.ini — show it (and
            # its profiles) dimmed so the Models toggle is visible here too.
            self._tree.insert("", "end", iid=mid, text=m.name, open=True,
                              values=(f"{n_active}/{len(plist)}",),
                              tags=("model",))
            for p in plist:
                self._tree.insert(mid, "end", iid=f"p:{p.id}", text=p.name,
                                  values=("☑" if p.active else "☐",),
                                  tags=())
        target = keep if keep and self._tree.exists(keep) else None
        if target is None:
            first = self._tree.get_children()
            kids = self._tree.get_children(first[0]) if first else ()
            target = kids[0] if kids else None
        if target:
            self._tree.selection_set(target)
            self._tree.see(target)

    def _selected(self) -> tuple[str, str] | None:
        """Return ('m'|'p', id) for the selected tree row."""
        sel = self._tree.selection()
        if not sel:
            return None
        kind, _, oid = sel[0].partition(":")
        return kind, oid

    def _selected_model_id(self) -> str | None:
        sel = self._selected()
        if not sel:
            return None
        kind, oid = sel
        if kind == "m":
            return oid
        p = self._profiles.get(oid)
        return p.model_id if p else None

    def _on_tree_click(self, e) -> None:
        # Click on the checkbox column toggles active state.
        if self._tree.identify_column(e.x) != "#1":
            return
        row = self._tree.identify_row(e.y)
        if not row:
            return
        kind, _, oid = row.partition(":")
        if kind == "p":
            p = self._profiles.get(oid)
            if p:
                self._profiles.set_active(oid, not p.active)
                self._refresh_tree(keep=row)
        elif kind == "m":
            plist = self._profiles.list(oid)
            self._profiles.set_active_all(
                oid, any(not p.active for p in plist))
            self._refresh_tree(keep=row)

    def _on_select(self) -> None:
        self._flush_autosave()
        sel = self._selected()
        if not sel:
            return
        kind, oid = sel
        if kind == "p":
            self._load_profile(oid)
        else:
            self._show_hint(t("Pick a profile to edit — or create one with New."))

    def _on_preset_imported(self, _data=None) -> None:
        """Reflect hand-edited INI values in the currently visible form."""
        if self._current:
            self._load_profile(self._current)

    # ── Editor load / save ───────────────────────────────────────────────────

    def _load_profile(self, pid: str) -> None:
        p = self._profiles.get(pid)
        if not p:
            return
        self._loading_profile = True
        try:
            self._current = pid
            self._show_editor()
            self._set(self._name, p.name)
            self._set(self._alias, p.route_alias)
            self._active.set(p.active)
            self._fill_params(p.params)
            self._refresh_pickers(p)
        finally:
            self._loading_profile = False

    def _fill_params(self, params: dict) -> None:
        for key, (kind, w, extra) in self._fields.items():
            raw = params.get(key)
            if kind == "bool":
                w.set(str(raw).lower() == "true")
            elif kind in ("choice", "combo"):
                _values, omit = extra
                w.set(str(raw) if raw is not None
                      else (omit if omit is not None else _values[0]))
            else:
                # An unset param shows the llama-server default; leaving it
                # untouched keeps it out of the profile (see _collect_params).
                if raw is None:
                    raw = _LLAMA_DEFAULTS.get(key)
                self._set(w, "" if raw is None else str(raw))
        self._extra.delete("1.0", "end")
        self._extra.insert("1.0", _format_extra(params))

    def _refresh_pickers(self, p) -> None:
        # Sampling presets: mark the ones matching the model name as suggested.
        model = self._models.get(p.model_id)
        mname = model.name.lower() if model else ""
        names = []
        for pr in self._presets:
            hit = any(s in mname for s in pr.get("match", []))
            names.append(pr["name"] + (" ★" if hit else ""))
        self._preset_cb.configure(values=names)
        self._preset_cb.set(t("Sampling preset…") if names else "")

        # Copy params from every profile of every *other* model.
        self._copy_map = {}
        for m in self._models.list():
            if m.id == p.model_id:
                continue
            for prof in self._profiles.list(m.id):
                self._copy_map[f"{m.name} · {prof.name}"] = prof.id
        self._copy_cb.configure(values=list(self._copy_map))
        self._copy_cb.set(t("Copy params from…") if self._copy_map else "")

    def _on_sampling_preset(self, _e) -> None:
        idx = self._preset_cb.current()
        if idx < 0 or idx >= len(self._presets) or not self._current:
            return
        params = self._collect_params()
        for k in _SAMPLING_KEYS:
            params.pop(k, None)
        params.update(self._presets[idx].get("params", {}))
        self._fill_params(params)
        self._preset_cb.set(t("Sampling preset…"))
        self._schedule_save()

    def _on_copy_from(self, _e) -> None:
        src_id = self._copy_map.get(self._copy_cb.get())
        src = self._profiles.get(src_id) if src_id else None
        if not src or not self._current:
            return
        self._fill_params(dict(src.params))
        self._copy_cb.set(t("Copy params from…"))
        self._schedule_save()

    @staticmethod
    def _set(entry: ttk.Entry, value: str) -> None:
        entry.delete(0, "end")
        entry.insert(0, value)

    def _collect_params(self) -> dict:
        params = _parse_extra(self._extra.get("1.0", "end"))
        for key, (kind, w, extra) in self._fields.items():
            if kind == "bool":
                if w.get():
                    params[key] = "true"
            elif kind in ("choice", "combo"):
                _values, omit = extra
                val = w.get().strip()
                if val and val != omit:
                    params[key] = val
            elif kind == "int":
                val = w.get().strip()
                if val:
                    try:
                        parsed = int(val)
                    except ValueError:
                        continue
                    if parsed != _LLAMA_DEFAULTS.get(key):
                        params[key] = parsed
            elif kind == "float":
                val = w.get().strip()
                if val:
                    try:
                        parsed = float(val)
                    except ValueError:
                        continue
                    if parsed != _LLAMA_DEFAULTS.get(key):
                        params[key] = parsed
            else:  # str / file
                val = w.get().strip()
                if val and val != _LLAMA_DEFAULTS.get(key):
                    params[key] = val
        return params

    def _wire_autosave(self) -> None:
        entries = [self._name, self._alias]
        for kind, widget, _extra in self._fields.values():
            if kind == "bool":
                widget.trace_add("write", lambda *_: self._schedule_save())
            else:
                entries.append(widget)
                if kind in ("choice", "combo"):
                    widget.bind("<<ComboboxSelected>>",
                                lambda _e: self._schedule_save(), add="+")
        self._active.trace_add("write", lambda *_: self._schedule_save())
        for entry in entries:
            entry.bind("<KeyRelease>", lambda _e: self._schedule_save(), add="+")
            entry.bind("<FocusOut>", lambda _e: self._schedule_save(0), add="+")
        self._extra.bind("<KeyRelease>",
                         lambda _e: self._schedule_save(), add="+")

    def _schedule_save(self, delay: int = 650) -> None:
        if self._loading_profile or not self._current:
            return
        if self._autosave_id is not None:
            self.after_cancel(self._autosave_id)
        self._save_state.configure(text=t("Saving…"), fg=self.c["warn"])
        self._autosave_id = self.after(delay, lambda: self._save(auto=True))

    def _flush_autosave(self) -> None:
        if self._autosave_id is None:
            return
        self.after_cancel(self._autosave_id)
        self._autosave_id = None
        if self._current:
            self._save(auto=True)

    def _save(self, auto: bool = False) -> None:
        self._autosave_id = None
        if not self._current:
            return
        try:
            self._profiles.update(self._current, {
                "name": self._name.get().strip() or "Profile",
                "route_alias": self._alias.get().strip(),
                "active": self._active.get(),
                "params": self._collect_params(),
            })
        except ValueError as exc:
            self._save_state.configure(text=str(exc), fg=self.c["error"])
            return
        self._save_state.configure(text=t("Saved automatically"),
                                   fg=self.c["faint"])
        if auto:
            iid = f"p:{self._current}"
            if self._tree.exists(iid):
                p = self._profiles.get(self._current)
                self._tree.item(iid, text=p.name,
                                values=("☑" if p.active else "☐",))
                parent = self._tree.parent(iid)
                plist = self._profiles.list(p.model_id)
                self._tree.item(parent, values=(
                    f"{sum(1 for item in plist if item.active)}/{len(plist)}",))
        else:
            self._refresh_tree(keep=f"p:{self._current}")

    def teardown(self) -> None:
        self._flush_autosave()
        if self._preset_view is not None:
            self._preset_view.teardown()
        if self._library_view is not None:
            self._library_view.teardown()
        super().teardown()

    def _show_hint(self, text: str) -> None:
        self._current = None
        self._editor_body.pack_forget()
        self._editor_hint.configure(text=text)
        self._editor_hint.place(relx=0.5, rely=0.4, anchor="center")

    def _show_editor(self) -> None:
        self._editor_hint.place_forget()
        if not self._editor_body.winfo_ismapped():
            self._editor_body.pack(fill="both", expand=True, padx=18, pady=16)

    # ── Actions ──────────────────────────────────────────────────────────────

    def _new(self) -> None:
        mid = self._selected_model_id()
        if not mid:
            return
        n = len(self._profiles.list(mid)) + 1
        p = self._profiles.create(mid, f"Profile {n}",
                                  {"n-gpu-layers": -1})
        self._refresh_tree(keep=f"p:{p.id}")

    def _delete(self) -> None:
        sel = self._selected()
        if not sel or sel[0] != "p":
            return
        mid = self._selected_model_id()
        self._profiles.delete(sel[1])
        self._current = None
        self._refresh_tree(keep=f"m:{mid}" if mid else None)

    def _set_all_active(self, active: bool) -> None:
        # Applies to every profile of every model, not just the selected one
        # (the per-model toggle lives on the tree's checkbox column).
        sel = self._tree.selection()
        self._profiles.set_active_all(None, active)
        self._refresh_tree(keep=sel[0] if sel else None)

    def on_show(self) -> None:
        sel = self._tree.selection()
        self._refresh_tree(keep=sel[0] if sel else None)
