"""Preset — the models-preset.ini editor. The INI is the project's source of
truth: it is regenerated from models + profiles on every change and shown here
for inspection and hand-editing (pi-test's preset editor)."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from llama_router.i18n import t
from llama_router.preset import parse_profile_params, strip_disabled_sections
from llama_router.core.storage import write_text
from llama_router.ui import theme
from llama_router.ui.pages.base import PAGE_PAD, Page
from llama_router.ui.widgets import PillButton


class PresetPage(Page):
    def __init__(self, parent: tk.Widget, ctx, embedded: bool = False,
                 actions_parent: tk.Widget | None = None) -> None:
        super().__init__(parent, ctx)
        c = self.c
        body_bg = c["surface"] if embedded else c["bg"]
        self.configure(bg=body_bg)
        pad = 0 if embedded else PAGE_PAD
        if embedded:
            if actions_parent is None:
                actions = tk.Frame(self, bg=body_bg)
                actions.pack(fill="x", pady=(0, 10))
            else:
                actions = actions_parent
        else:
            head = self.header(
                t("source of truth"), "models-preset.ini",
                t("What llama-server actually loads — regenerated on every change"))
            actions = head.actions
        self._reload_btn = PillButton(actions, c, t("Reload"),
                                      command=self._reload)
        self._reload_btn.pack(side="left", padx=(0, 8))
        self._save_btn = PillButton(actions, c, t("Save file"),
                                    kind="primary", command=self._save)
        self._save_btn.pack(side="left")

        bar = tk.Frame(self, bg=body_bg)
        bar.pack(fill="x", padx=pad, pady=(0, 8))
        self._path_lbl = tk.Label(bar, text=str(ctx.paths.preset_ini),
                                  bg=body_bg, fg=c["faint"], font=theme.mono(8))
        self._path_lbl.pack(side="left")
        self._state_lbl = tk.Label(bar, text="", bg=body_bg, fg=c["warn"],
                                   font=theme.ui(9))
        self._state_lbl.pack(side="right")

        panel = tk.Frame(self, bg=c["surface"],
                         highlightbackground=c["panel_request"],
                         highlightthickness=1)
        panel.pack(fill="both", expand=True, padx=pad, pady=(0, pad))
        self._text = tk.Text(panel, bg=c["inset"], fg=c["text"], bd=0,
                             padx=10, pady=8, font=theme.mono(9), wrap="none",
                             insertbackground=c["text"], undo=True,
                             highlightthickness=0,
                             height=14 if embedded else 24)
        vbar = ttk.Scrollbar(panel, orient="vertical", command=self._text.yview)
        hbar = ttk.Scrollbar(panel, orient="horizontal",
                             command=self._text.xview)
        self._text.configure(yscrollcommand=vbar.set,
                             xscrollcommand=hbar.set)
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")
        self._text.pack(fill="both", expand=True, padx=1, pady=1)
        self._text.bind("<<Modified>>", self._on_modified)

        self._dirty = False
        self._loading = False
        self._reload()

        # Auto-reload after regen (model/profile/config mutations) unless the
        # user has unsaved hand edits.
        for evt in ("model_updated", "model_removed", "models_scanned",
                    "profile_created", "profile_updated", "profile_deleted",
                    "profiles_reset", "config_saved"):
            self.subscribe(evt, self._on_external_change)

    # ── Load / save ──────────────────────────────────────────────────────────

    def _read_file(self) -> str:
        try:
            return self.ctx.paths.preset_ini.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _reload(self) -> None:
        self._loading = True
        content = self._read_file()
        self._text.delete("1.0", "end")
        self._text.insert("1.0", content or t("; empty — enable models and activate profiles"))
        self._text.edit_modified(False)
        self._loading = False
        self._set_dirty(False)

    def _save(self) -> None:
        # A disabled model must never survive in the preset, even if the
        # editor content predates the disable (pi-test rule).
        models = self.ctx.services["models"].list()
        content = strip_disabled_sections(
            self._text.get("1.0", "end-1c"), models)
        profiles = self.ctx.services["profiles"]
        try:
            global_params, profile_updates = parse_profile_params(
                content, models, profiles.by_model())
        except Exception:
            self._state_lbl.configure(text=t("invalid INI — changes not saved"))
            return
        try:
            write_text(self.ctx.paths.preset_ini, content)
        except OSError:
            self._state_lbl.configure(text=t("could not write file"))
            return
        self._loading = True
        self._text.delete("1.0", "end")
        self._text.insert("1.0", content)
        self._text.edit_modified(False)
        self._loading = False
        self._set_dirty(False)
        profiles.apply_preset_params(profile_updates)
        config = self.ctx.services["config"]
        if config.get().global_params != global_params:
            config.update({"global_params": global_params})
        self._save_btn.set_text(t("Saved ✓"))
        self.after(1600, lambda: self._save_btn.set_text(t("Save file")))

    # ── Dirty tracking ───────────────────────────────────────────────────────

    def _on_modified(self, _e) -> None:
        if self._loading:
            return
        if self._text.edit_modified():
            self._text.edit_modified(False)
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self._state_lbl.configure(
            text=t("edited — save or reload") if dirty else "")

    def _on_external_change(self, _data=None) -> None:
        if not self._dirty:
            self._reload()

    def on_show(self) -> None:
        if not self._dirty:
            self._reload()
