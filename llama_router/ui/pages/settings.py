"""Settings — application preferences and llama-server launch settings."""
from __future__ import annotations

import secrets
import tkinter as tk
from tkinter import ttk

from llama_router.i18n import LANGUAGES, t
from llama_router.ui import theme
from llama_router.ui.pages.base import PAGE_PAD, Page
from llama_router.ui.widgets import CollapsibleCard, PillButton, ScrollFrame

_FIELD_W = 26   # entry width in chars
_SERVER_COMPACT_WIDTH = 780
_API_ACTIONS_COMPACT_WIDTH = 360


class SettingsPage(Page):
    def __init__(self, parent: tk.Widget, ctx) -> None:
        super().__init__(parent, ctx)
        self._autosave_id: str | None = None
        self._layout_id: str | None = None
        self._form_dirty = False
        self._saved_form: dict = {}
        self._server_built = False
        self._server_draft: dict | None = None
        self._common_autosave_wired = False
        self._server_autosave_wired = False
        self._build()

    def _build(self) -> None:
        c = self.c
        self._form_dirty = False
        self._server_built = False
        self._server_draft = None
        self._common_autosave_wired = False
        self._server_autosave_wired = False
        head = self.header(t("configuration"), t("Settings"))
        self._save_state = tk.Label(head.actions, text=t("Saved automatically"),
                                    bg=c["bg"], fg=c["faint"],
                                    font=theme.ui(8))
        self._save_state.pack(pady=7)

        scroll = ScrollFrame(self, c)
        scroll.pack(fill="both", expand=True)
        body = scroll.body

        cfg = self._config()

        # ── Appearance ──────────────────────────────────────────────────────
        theme_card = CollapsibleCard(body, c, t("Appearance"),
                                     state_key="settings.appearance",
                                     on_toggle=self._on_appearance_toggle,
                                     accent=c["panel_accent"])
        theme_card.pack(fill="x", padx=PAGE_PAD, pady=(0, 14))
        self._appearance_card = theme_card
        self._appearance_built = False
        self._theme_btns = {}
        if theme_card.is_open:
            self._build_appearance()

        # ── Application ──────────────────────────────────────────────────────
        app_card = CollapsibleCard(body, c, t("Application"),
                                   state_key="settings.application",
                                   accent=c["panel_num"])
        app_card.pack(fill="x", padx=PAGE_PAD, pady=(0, 14))
        grid = self._grid(app_card.content)

        self._language = self._combo(grid, 0, t("Language"),
                                     list(LANGUAGES.values()),
                                     LANGUAGES.get(cfg.language, "English"))
        self._lang_hint = tk.Label(grid, text=t("Applies instantly"),
                                   bg=c["surface"], fg=c["faint"], font=theme.ui(8))
        self._lang_hint.grid(row=0, column=2, sticky="w", padx=(10, 0))
        self._autostart = self._check(grid, 1, t("Start server on launch"),
                                      cfg.autostart_server)
        self._max_dl = self._entry(grid, 2, t("Concurrent downloads"),
                                   str(cfg.max_concurrent_downloads))
        self._auto_check = self._check(
            grid, 3, t("Auto-check for runtime updates"),
            cfg.auto_check_releases)
        self._show_api_details = self._check(
            grid, 4, t("Show API key in dashboard and examples"),
            cfg.show_api_details)
        self._show_api_details.trace_add(
            "write", lambda *_: self._apply_api_details_visibility())
        from llama_router.services import tray
        self._tray_var = None
        if tray.is_supported():
            self._tray_var = self._check(grid, 5, t("Minimize to tray"),
                                          cfg.minimize_to_tray)

        # ── Server ───────────────────────────────────────────────────────────
        srv_card = CollapsibleCard(body, c, t("Server"),
                                   state_key="settings.server",
                                   on_toggle=self._on_server_toggle,
                                   accent=c["panel_warn"])
        srv_card.pack(fill="x", padx=PAGE_PAD, pady=(0, 14))
        self._server_card = srv_card
        reset = PillButton(srv_card.header, c, t("Reset to defaults"), size=9, padx=12,
                           height=28, command=self._reset_server)
        reset.pack(side="right", padx=(0, 6))
        self._server_grid = None
        self._server_compact: bool | None = None
        if srv_card.is_open:
            self._build_server_form()
        self._saved_form = self._serialize()
        self._wire_autosave()

        tk.Frame(body, bg=c["bg"], height=PAGE_PAD).pack()

    def _on_server_toggle(self, open_: bool) -> None:
        if open_:
            self._build_server_form()

    def _build_server_form(self) -> None:
        if self._server_built:
            return
        cfg = self._config()
        s = cfg.server
        grid = self._grid(self._server_card.content, two_columns=True)
        self._server_grid = grid
        grid.bind("<Configure>", self._schedule_layout, add="+")
        self._expose = self._combo(
            grid, 0, t("Network exposure"),
            [t("This machine only"), t("Local network"), t("Custom host")],
            self._expose_label(s.expose))
        self._expose.bind("<<ComboboxSelected>>", lambda _e: self._sync_host())
        self._host = self._entry(grid, 1, t("Host"), s.host)
        self._port = self._entry(grid, 2, t("Port"), str(s.port))
        self._max_models = self._entry(
            grid, 3, t("Models in memory"), str(s.max_models))
        self._parallel = self._entry(
            grid, 4, t("Parallel slots"), str(s.parallel_slots))
        self._threads = self._entry(
            grid, 5, t("CPU threads"), str(s.cpu_threads))
        self._batch_threads = self._entry(
            grid, 6, t("Batch CPU threads"), str(s.batch_threads))
        self._api_key_real = s.api_key
        self._api_key_visible = cfg.show_api_details
        self._api_key = self._api_key_control(grid, 7)
        self._stop_timeout = self._entry(
            grid, 8, t("Stop timeout (s)"), str(s.stop_timeout))
        self._cont_batching = self._check(
            grid, 9, t("Continuous batching"), s.cont_batching)
        self._models_autoload = self._check(
            grid, 10, t("Autoload models"), s.models_autoload)
        self._metrics = self._check(
            grid, 11, t("Prometheus metrics"), s.metrics)
        self._restart_crash = self._check(
            grid, 12, t("Restart on crash"), s.restart_on_crash)
        self._extra = self._entry(
            grid, 13, t("Extra arguments"), s.extra_args, wide=True)
        self._server_built = True
        draft = self._server_draft
        if draft:
            self._restore_server_values(draft)
            self._server_draft = None
        else:
            self._sync_host()
        self._wire_autosave()
        self._schedule_layout()

    def _server_values(self) -> dict:
        cfg = self._config()
        s = cfg.server
        values = {
            "expose": self._expose_label(s.expose),
            "host": s.host,
            "port": str(s.port),
            "max_models": str(s.max_models),
            "parallel": str(s.parallel_slots),
            "threads": str(s.cpu_threads),
            "batch_threads": str(s.batch_threads),
            "api_key": s.api_key,
            "stop_timeout": str(s.stop_timeout),
            "cont_batching": s.cont_batching,
            "models_autoload": s.models_autoload,
            "metrics": s.metrics,
            "restart_crash": s.restart_on_crash,
            "extra": s.extra_args,
        }
        if self._server_built:
            values.update({
                "expose": self._expose.get(),
                "host": self._host.get(),
                "port": self._port.get(),
                "max_models": self._max_models.get(),
                "parallel": self._parallel.get(),
                "threads": self._threads.get(),
                "batch_threads": self._batch_threads.get(),
                "api_key": self._current_api_key(),
                "stop_timeout": self._stop_timeout.get(),
                "cont_batching": self._cont_batching.get(),
                "models_autoload": self._models_autoload.get(),
                "metrics": self._metrics.get(),
                "restart_crash": self._restart_crash.get(),
                "extra": self._extra.get(),
            })
        elif self._server_draft:
            values.update({key: self._server_draft.get(key, value)
                           for key, value in values.items()})
        return values

    def _restore_server_values(self, d: dict) -> None:
        for widget, key in ((self._host, "host"), (self._port, "port"),
                            (self._max_models, "max_models"),
                            (self._parallel, "parallel"),
                            (self._threads, "threads"),
                            (self._batch_threads, "batch_threads"),
                            (self._stop_timeout, "stop_timeout"),
                            (self._extra, "extra")):
            widget.delete(0, "end")
            widget.insert(0, d[key])
        self._expose.set(d["expose"])
        self._cont_batching.set(d["cont_batching"])
        self._models_autoload.set(d["models_autoload"])
        self._metrics.set(d["metrics"])
        self._restart_crash.set(d["restart_crash"])
        self._api_key_real = d.get("api_key", "")
        self._api_key_visible = self._show_api_details.get()
        self._render_api_key()
        self._sync_host()

    # ── Theme ────────────────────────────────────────────────────────────────

    def _pick_theme(self, name: str) -> None:
        self.ctx.apply_theme(name)

    def _on_appearance_toggle(self, open_: bool) -> None:
        if open_:
            self._build_appearance()

    def _build_appearance(self) -> None:
        if self._appearance_built:
            return
        c = self.c
        pick = tk.Frame(self._appearance_card.content, bg=c["surface"])
        pick.pack(fill="x", pady=(10, 2))
        for name in theme.theme_names():
            btn = PillButton(pick, c, theme.label(name), kind="ghost",
                             size=9, padx=18, height=30,
                             command=lambda n=name: self._pick_theme(n))
            btn.pack(side="left", padx=(0, 8))
            self._theme_btns[name] = btn
        tk.Label(
            self._appearance_card.content,
            text=t("Theme applies instantly and is saved automatically"),
            bg=c["surface"], fg=c["faint"], font=theme.ui(8),
        ).pack(anchor="w", pady=(4, 0))
        self._appearance_built = True
        self._set_theme_active(self._config().theme)

    def _set_theme_active(self, name: str) -> None:
        for _n, btn in self._theme_btns.items():
            btn.set_kind("primary" if _n == name else "ghost")

    def _serialize(self) -> dict:
        """Capture current field values so a theme flip can't wipe them."""
        form = {
            "language": self._language.get(),
            "autostart": self._autostart.get(),
            "max_dl": self._max_dl.get(),
            "auto_check": self._auto_check.get(),
            "show_api_details": self._show_api_details.get(),
            "tray": self._tray_var.get() if self._tray_var else None,
        }
        form.update(self._server_values())
        return form

    def _restore(self, d: dict) -> None:
        """Re-apply previously captured field values after a rebuild."""
        self._language.set(d["language"])
        self._autostart.set(d["autostart"])
        self._auto_check.set(d["auto_check"])
        self._show_api_details.set(d.get("show_api_details", False))
        self._max_dl.delete(0, "end")
        self._max_dl.insert(0, d["max_dl"])
        if self._tray_var and d.get("tray") is not None:
            self._tray_var.set(d["tray"])
        if self._server_built:
            self._restore_server_values(d)
        else:
            self._server_draft = dict(d)

    # ── Form helpers ─────────────────────────────────────────────────────────

    def _grid(self, parent: tk.Widget, two_columns: bool = False) -> tk.Frame:
        g = tk.Frame(parent, bg=self.c["surface"])
        g.pack(fill="x", pady=(12, 2))
        g._two_columns = two_columns
        self._configure_grid_columns(g, two_columns)
        return g

    @staticmethod
    def _configure_grid_columns(grid: tk.Frame, two_columns: bool) -> None:
        for column in range(4):
            grid.columnconfigure(column, minsize=0, weight=0, uniform="")
        if two_columns:
            grid.columnconfigure(0, minsize=130)
            grid.columnconfigure(1, weight=1, uniform="settings-values")
            grid.columnconfigure(2, minsize=130)
            grid.columnconfigure(3, weight=1, uniform="settings-values")
        else:
            grid.columnconfigure(0, minsize=130)
            grid.columnconfigure(1, weight=1)

    @staticmethod
    def _cell(grid: tk.Frame, row: int) -> tuple[int, int]:
        if getattr(grid, "_two_columns", False):
            return row // 2, (row % 2) * 2
        return row, 0

    def _schedule_layout(self, _event=None) -> None:
        if self._layout_id is not None:
            self.after_cancel(self._layout_id)
        self._layout_id = self.after(40, self._layout)

    def _layout(self) -> None:
        self._layout_id = None
        self._layout_server_grid()
        self._layout_api_actions()

    def _cancel_layout(self) -> None:
        if self._layout_id is not None:
            self.after_cancel(self._layout_id)
            self._layout_id = None

    def _layout_server_grid(self) -> None:
        grid = self._server_grid
        if grid is None:
            return
        width = grid.winfo_width()
        if width <= 1:
            return
        compact = width < _SERVER_COMPACT_WIDTH
        if compact == self._server_compact:
            return
        self._server_compact = compact
        grid._two_columns = not compact
        self._configure_grid_columns(grid, not compact)
        for widget in grid.winfo_children():
            logical_row = getattr(widget, "_settings_row", None)
            if logical_row is None:
                continue
            row, column = self._cell(grid, logical_row)
            if getattr(widget, "_settings_role", "") == "label":
                widget.grid_configure(row=row, column=column,
                                      padx=(0, 16))
            else:
                widget.grid_configure(
                    row=row, column=column + 1,
                    padx=(0, 22 if not compact and column == 0 else 0))
    def _label(self, grid: tk.Frame, row: int, text: str) -> None:
        logical_row = row
        row, col = self._cell(grid, logical_row)
        label = tk.Label(grid, text=text, bg=self.c["surface"],
                         fg=self.c["muted"], font=theme.ui(9), anchor="w")
        label._settings_row = logical_row
        label._settings_role = "label"
        label.grid(row=row, column=col, sticky="w", pady=5, padx=(0, 16))

    def _entry(self, grid: tk.Frame, row: int, label: str, value: str,
               secret: bool = False, wide: bool = False) -> ttk.Entry:
        self._label(grid, row, label)
        grid_row, col = self._cell(grid, row)
        e = ttk.Entry(grid, width=60 if wide else _FIELD_W, font=theme.mono(9),
                      show="•" if secret and value else "")
        e._settings_row = row
        e._settings_role = "value"
        e.insert(0, value)
        e.grid(row=grid_row, column=col + 1, sticky="ew", pady=5,
               padx=(0, 22 if col == 0 else 0))
        if secret:
            e.bind("<FocusIn>", lambda _e: e.configure(show="•"))
        return e

    def _api_key_control(self, grid: tk.Frame, row: int) -> ttk.Entry:
        self._label(grid, row, t("API key"))
        grid_row, col = self._cell(grid, row)
        wrap = tk.Frame(grid, bg=self.c["surface"])
        wrap._settings_row = row
        wrap._settings_role = "value"
        wrap.grid(row=grid_row, column=col + 1, sticky="ew", pady=5,
                  padx=(0, 22 if col == 0 else 0))
        entry = ttk.Entry(wrap, width=18, font=theme.mono(9))
        actions = tk.Frame(wrap, bg=self.c["surface"])
        self._api_wrap = wrap
        self._api_entry_widget = entry
        self._api_actions = actions
        self._api_actions_compact: bool | None = None
        self._api_show_btn = PillButton(
            actions, self.c, t("Show"), size=7, padx=7, height=24,
            command=self._toggle_api_key)
        self._api_show_btn.pack(side="left", padx=(5, 0))
        PillButton(actions, self.c, t("Copy"), size=7, padx=7, height=24,
                   command=self._copy_api_key).pack(side="left", padx=(4, 0))
        PillButton(actions, self.c, t("Generate"), size=7, padx=7, height=24,
                   command=self._generate_api_key).pack(side="left", padx=(4, 0))
        wrap.bind("<Configure>", self._schedule_layout, add="+")
        self._render_api_key(entry)
        return entry

    def _layout_api_actions(self) -> None:
        if not hasattr(self, "_api_wrap"):
            return
        compact = (bool(self._server_compact)
                   or self._api_wrap.winfo_width() < _API_ACTIONS_COMPACT_WIDTH)
        if compact == self._api_actions_compact:
            return
        self._api_actions_compact = compact
        self._api_entry_widget.pack_forget()
        self._api_actions.pack_forget()
        if compact:
            self._api_entry_widget.pack(fill="x")
            self._api_actions.pack(anchor="w", pady=(5, 0))
        else:
            self._api_actions.pack(side="right")
            self._api_entry_widget.pack(side="left", fill="x", expand=True)

    @staticmethod
    def _partial_key(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 10:
            return "\u2022" * len(value)
        return value[:6] + "\u2022" * 8 + value[-4:]

    def _render_api_key(self, entry=None) -> None:
        entry = entry or self._api_key
        entry.configure(state="normal")
        entry.delete(0, "end")
        entry.insert(0, self._api_key_real if self._api_key_visible
                     else self._partial_key(self._api_key_real))
        entry.configure(state="normal" if self._api_key_visible else "readonly")
        if hasattr(self, "_api_show_btn"):
            self._api_show_btn.set_text(
                t("Hide") if self._api_key_visible else t("Show"))

    def _current_api_key(self) -> str:
        if self._api_key_visible:
            self._api_key_real = self._api_key.get().strip()
        return self._api_key_real

    def _toggle_api_key(self) -> None:
        self._current_api_key()
        self._api_key_visible = not self._api_key_visible
        self._render_api_key()

    def _apply_api_details_visibility(self) -> None:
        """Apply the persisted API-details preference to the key field."""
        if not hasattr(self, "_api_key"):
            return
        self._current_api_key()
        self._api_key_visible = self._show_api_details.get()
        self._render_api_key()

    def _copy_api_key(self) -> None:
        value = self._current_api_key()
        if value:
            self.clipboard_clear()
            self.clipboard_append(value)
            self._save_state.configure(text=t("Copied"), fg=self.c["ok"])

    def _generate_api_key(self) -> None:
        self._api_key_real = "lr_" + secrets.token_urlsafe(32)
        self._render_api_key()
        self._schedule_save(0)

    def _combo(self, grid: tk.Frame, row: int, label: str,
               values: list[str], current: str) -> ttk.Combobox:
        self._label(grid, row, label)
        grid_row, col = self._cell(grid, row)
        cb = ttk.Combobox(grid, values=values, state="readonly",
                          width=_FIELD_W - 2, font=theme.ui(9))
        cb._settings_row = row
        cb._settings_role = "value"
        cb.set(current)
        cb.grid(row=grid_row, column=col + 1, sticky="ew", pady=5,
                padx=(0, 22 if col == 0 else 0))
        return cb

    def _check(self, grid: tk.Frame, row: int, label: str,
               value: bool) -> tk.BooleanVar:
        self._label(grid, row, label)
        grid_row, col = self._cell(grid, row)
        var = tk.BooleanVar(value=value)
        check = ttk.Checkbutton(grid, variable=var, style="TCheckbutton",
                                takefocus=False)
        check._settings_row = row
        check._settings_role = "value"
        check.grid(row=grid_row, column=col + 1, sticky="w")
        return var

    # ── Expose mapping ───────────────────────────────────────────────────────

    def _expose_label(self, expose: str) -> str:
        return {"local": t("This machine only"), "lan": t("Local network"),
                "custom": t("Custom host")}.get(expose, t("This machine only"))

    def _expose_value(self, selection: str | None = None) -> str:
        sel = selection if selection is not None else self._expose.get()
        if sel == t("Local network"):
            return "lan"
        if sel == t("Custom host"):
            return "custom"
        return "local"

    def _sync_host(self) -> None:
        state = "normal" if self._expose_value() == "custom" else "disabled"
        self._host.configure(state=state)

    def _wire_autosave(self) -> None:
        if not self._common_autosave_wired:
            entries = (self._max_dl,)
            combos = (self._language,)
            variables = (self._autostart, self._auto_check,
                         self._show_api_details)
            if self._tray_var is not None:
                variables += (self._tray_var,)
            for entry in entries:
                entry.bind("<KeyRelease>",
                           lambda _e: self._schedule_save(), add="+")
                entry.bind("<FocusOut>",
                           lambda _e: self._schedule_save(0), add="+")
            for combo in combos:
                combo.bind("<<ComboboxSelected>>",
                           lambda _e: self._schedule_save(0), add="+")
            for var in variables:
                var.trace_add("write", lambda *_: self._schedule_save(0))
            self._common_autosave_wired = True

        if self._server_built and not self._server_autosave_wired:
            entries = (self._host, self._port, self._max_models,
                       self._parallel, self._threads, self._batch_threads,
                       self._api_key, self._stop_timeout, self._extra)
            variables = (self._cont_batching, self._models_autoload,
                         self._metrics, self._restart_crash)
            for entry in entries:
                entry.bind("<KeyRelease>",
                           lambda _e: self._schedule_save(), add="+")
                entry.bind("<FocusOut>",
                           lambda _e: self._schedule_save(0), add="+")
            self._expose.bind("<<ComboboxSelected>>",
                              lambda _e: self._schedule_save(0), add="+")
            for var in variables:
                var.trace_add("write", lambda *_: self._schedule_save(0))
            self._server_autosave_wired = True

    def _schedule_save(self, delay: int = 650) -> None:
        self._form_dirty = True
        if self._autosave_id is not None:
            self.after_cancel(self._autosave_id)
        self._save_state.configure(text=t("Saving…"), fg=self.c["warn"])
        self._autosave_id = self.after(delay, self._save)

    # ── Actions ──────────────────────────────────────────────────────────────

    def _config(self):
        return self.ctx.services["config"].get()

    def _int(self, entry: ttk.Entry | str, fallback: int, lo: int = 0,
             hi: int = 1 << 16) -> int:
        try:
            raw = entry if isinstance(entry, str) else entry.get()
            v = int(raw.strip())
            return v if lo <= v <= hi else fallback
        except ValueError:
            return fallback

    def _save(self) -> None:
        self._autosave_id = None
        if not self._form_dirty:
            return
        form = self._serialize()
        if form == self._saved_form:
            self._form_dirty = False
            self._save_state.configure(text=t("Saved automatically"),
                                       fg=self.c["faint"])
            return
        cfg = self._config()
        previous_language = cfg.language
        lang_code = {v: k for k, v in LANGUAGES.items()}.get(
            self._language.get(), "en")
        server = self._server_values()
        patch = {
            "language": lang_code,
            "autostart_server": self._autostart.get(),
            "max_concurrent_downloads": self._int(self._max_dl,
                                                  cfg.max_concurrent_downloads,
                                                  1, 10),
            "auto_check_releases": self._auto_check.get(),
            "show_api_details": self._show_api_details.get(),
            "server": {
                "expose": self._expose_value(server["expose"]),
                "host": server["host"].strip() or "127.0.0.1",
                "port": self._int(server["port"], cfg.server.port, 1, 65535),
                "max_models": self._int(server["max_models"],
                                        cfg.server.max_models, 1, 64),
                "parallel_slots": self._int(server["parallel"],
                                            cfg.server.parallel_slots, 1, 128),
                "cpu_threads": self._int(server["threads"],
                                         cfg.server.cpu_threads, 1, 256),
                "batch_threads": self._int(server["batch_threads"],
                                            cfg.server.batch_threads, 1, 256),
                "api_key": server["api_key"],
                "stop_timeout": self._int(server["stop_timeout"],
                                          cfg.server.stop_timeout, 1, 300),
                "cont_batching": server["cont_batching"],
                "models_autoload": server["models_autoload"],
                "metrics": server["metrics"],
                "restart_on_crash": server["restart_crash"],
                "extra_args": server["extra"].strip(),
            },
        }
        if self._tray_var is not None:
            patch["minimize_to_tray"] = self._tray_var.get()
        self.ctx.services["config"].update(patch)
        self._saved_form = form
        self._form_dirty = False
        if lang_code != previous_language:
            self.ctx.apply_language(lang_code)
            return
        self._save_state.configure(text=t("Saved automatically"),
                                   fg=self.c["faint"])

    def _reset_server(self) -> None:
        self._cancel_layout()
        if self._autosave_id is not None:
            self.after_cancel(self._autosave_id)
            self._autosave_id = None
        self.ctx.services["config"].reset_server()
        self._server_draft = None
        for w in self.winfo_children():
            w.destroy()
        self._build()

    def teardown(self) -> None:
        self._cancel_layout()
        if self._autosave_id is not None:
            self.after_cancel(self._autosave_id)
            self._autosave_id = None
        if self._form_dirty:
            self._save()
        super().teardown()
