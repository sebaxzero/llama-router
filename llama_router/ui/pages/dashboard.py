"""Dashboard — server state hero, endpoint card, getting-started checklist.

Reads config defaults and reacts to `server_status` events; live counts wire
in as each service lands.

The whole page is wrapped in a ScrollFrame so every card stays reachable no
matter how short the window is (without it, the lower cards get clipped with
no way to scroll to them). The endpoint + inventory row also restacks
vertically on narrow windows.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from llama_router.i18n import t
from llama_router.ui import theme
from llama_router.ui.pages.base import PAGE_PAD, Page
from llama_router.ui.widgets import (Card, CollapsibleCard, PillButton, ScrollFrame, StatusDot,
                                    fmt_uptime, key_value, section_label,
                                    status_label)

_LEVEL_COLORS = {"error": "error", "warning": "warn", "info": "muted",
                 "request": "request", "debug": "faint"}
_SOURCE_COLORS = {"server": "accent", "app": "ok", "downloads": "request"}


def _lan_address() -> str:
    """Return the best connectable IPv4 address for this machine."""
    # A UDP connect selects the interface Windows would use for LAN traffic;
    # it does not send a packet or require the destination to be reachable.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # RFC 5737 documentation address
        address = probe.getsockname()[0]
        if not ipaddress.ip_address(address).is_loopback:
            return address
    except OSError:
        pass
    finally:
        probe.close()

    # Fall back to addresses registered for the hostname (common on hosts
    # without a default route), preferring a private non-loopback address.
    try:
        addresses = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        addresses = []
    usable = [a for a in addresses
              if not ipaddress.ip_address(a).is_loopback]
    private = [a for a in usable if ipaddress.ip_address(a).is_private]
    return (private or usable or ["127.0.0.1"])[0]


class DashboardPage(Page):
    eyebrow = "panel"
    title = "Dashboard"

    def __init__(self, parent: tk.Widget, ctx) -> None:
        super().__init__(parent, ctx)
        c = self.c
        head = self.header(t("panel"), t("Dashboard"),
                           t("Router status and first steps"))

        # Live resource meters stay in the fixed portion with server state.
        self._fixed_usage = fixed_usage = tk.Frame(self, bg=c["bg"])
        # Match the scroll canvas width when its vertical scrollbar is shown.
        fixed_usage.pack(fill="x", padx=(PAGE_PAD, PAGE_PAD + 12), pady=(0, 10))

        # Scroll container: keeps the entire dashboard reachable on short
        # windows instead of clipping the lower cards.
        self._scroll = ScrollFrame(self, c, fill_height=True)
        self._scroll.pack(fill="both", expand=True)
        body = self._scroll.body

        # ── Hero: server state ───────────────────────────────────────────────
        hero = Card(body, c, pad=22, border=c["panel_accent"])
        hero.pack(fill="x", padx=PAGE_PAD)
        top = tk.Frame(hero.body, bg=c["surface"])
        top.pack(fill="x")

        self._dot = StatusDot(top, c, size=18)
        self._dot.configure(bg=c["surface"])
        self._dot.pack(side="left", pady=2)
        self._status_lbl = tk.Label(top, text=theme.track(t("Stopped")),
                                    bg=c["surface"], fg=c["text"],
                                    font=theme.mono(13, "bold"))
        self._status_lbl.pack(side="left", padx=(10, 0))
        self._uptime_lbl = tk.Label(top, text="", bg=c["surface"],
                                    fg=c["muted"], font=theme.mono(9))
        self._uptime_lbl.pack(side="left", padx=(10, 0), pady=(3, 0))

        self._hint = tk.Label(hero.body,
                              text=t("Install a runtime and add models before starting."),
                              bg=c["surface"], fg=c["muted"], font=theme.ui(9))
        self._hint.pack(anchor="center", pady=(12, 8))
        controls = tk.Frame(hero.body, bg=c["surface"])
        controls.pack(anchor="center", pady=(2, 0))
        self._start_btn = PillButton(controls, c, t("Start server"),
                                     kind="primary", size=10, padx=26,
                                     height=36, command=self._start)
        self._start_btn.pack(side="left")
        self._restart_btn = PillButton(controls, c, t("Restart"), size=10,
                                       padx=20, height=36,
                                       command=self._restart)
        self._restart_btn.pack(side="left", padx=(8, 0))
        self._reason = tk.Label(hero.body, text="", bg=c["surface"],
                                fg=c["warn"], font=theme.ui(9), anchor="w",
                                justify="left")
        cmd_head = tk.Frame(hero.body, bg=c["surface"])
        cmd_head.pack(fill="x", pady=(12, 4))
        tk.Label(cmd_head, text=t("Launch command").upper(), bg=c["surface"],
                 fg=c["panel_accent"], font=theme.mono(8, "bold")).pack(side="left")
        self._launch_cmd: list[str] = []
        cmd_box = tk.Frame(hero.body, bg=c["inset"])
        cmd_box.pack(fill="x")
        self._cmd_copy = PillButton(cmd_box, c, t("Copy"), size=8, padx=9,
                                    height=24, command=self._copy_launch_cmd)
        self._cmd_copy.pack(side="right", padx=4, pady=4, anchor="n")
        self._cmd_copy.set_enabled(False)
        self._cmd = tk.Text(cmd_box, height=1, bg=c["inset"], fg=c["muted"],
                            bd=0, padx=10, pady=6, font=theme.mono(8),
                            wrap="word", state="disabled", takefocus=False,
                            highlightthickness=0)
        self._cmd.pack(side="left", fill="x", expand=True)

        details = tk.Frame(body, bg=c["bg"])
        details.pack(fill="x", padx=PAGE_PAD, pady=(10, 0))
        self._inventory_btn = PillButton(details, c, t("Inventory"), size=8,
                                         padx=11, height=26,
                                         command=self._toggle_inventory)
        self._inventory_btn.pack(side="left")
        self._steps_btn = PillButton(details, c, t("First steps"), size=8,
                                     padx=11, height=26,
                                     command=self._toggle_steps)
        self._steps_btn.pack(side="left", padx=(6, 0))
        self._logs_btn = PillButton(details, c, t("Logs"), size=8,
                                    padx=11, height=26,
                                    command=self._toggle_logs)
        self._logs_btn.pack(side="left", padx=(6, 0))
        self._inventory_open = self._steps_open = self._logs_open = False
        self._logs_dirty = False
        self._tick_id = None

        # ── Client connection ───────────────────────────────────────────────
        # This card stays full-width.  Examples can be long, so sharing its
        # row with the optional inventory would make both cards cramped.
        ep = CollapsibleCard(body, c, t("Connect your client"),
                             state_key="dashboard.client",
                             accent=c["panel_request"])
        ep.pack(fill="x", padx=PAGE_PAD, pady=(14, 0))
        self._examples_btn = PillButton(ep.header, c, t("Examples"),
                                        command=self._toggle_client_examples,
                                        size=9, padx=14, height=30)
        self._examples_btn.pack(side="right", padx=(0, 6))
        self._endpoint_rows = tk.Frame(ep.content, bg=c["surface"])
        self._endpoint_rows.pack(fill="x")
        self._client_examples_box = tk.Frame(ep.content, bg=c["surface"])
        self._examples_open = False
        self._render_endpoints()

        self._ep = ep

        # ── Live resource strip ──────────────────────────────────────────────
        usage = Card(fixed_usage, c, border=c["panel_ok"])
        usage.pack(fill="x")
        self._usage_row = tk.Frame(usage.body, bg=c["surface"])
        self._usage_row.pack(fill="x")
        self._usage_row.columnconfigure(0, weight=1, uniform="usage")
        self._usage_row.columnconfigure(1, weight=1, uniform="usage")

        self._sys_card = tk.Frame(self._usage_row, bg=c["surface"])
        self._sys_card.grid(row=0, column=0, sticky="ew", padx=(0, 16))
        section_label(self._sys_card, c, t("System"), c["panel_ok"]).pack(
            anchor="w")
        self._cpu_bar, self._cpu_val = self._meter_row(self._sys_card, "CPU")
        self._ram_bar, self._ram_val = self._meter_row(self._sys_card, "RAM")

        self._gpu_card = tk.Frame(self._usage_row, bg=c["surface"])
        self._gpu_card.grid(row=0, column=1, sticky="nsew")
        section_label(self._gpu_card, c, "GPU", c["panel_request"]).pack(
            anchor="w")
        self._gpu_box = tk.Frame(self._gpu_card, bg=c["surface"])
        self._gpu_box.pack(fill="x")
        self._gpu_rows: list[tuple] = []
        self._usage_row.bind("<Configure>", self._relayout_usage, add="+")
        self.after_idle(lambda: self._relayout_usage())

        # ── Optional guidance: first steps + inventory ───────────────────────
        self._guidance_row = tk.Frame(body, bg=c["bg"])
        self._guidance_row.columnconfigure(0, weight=1, uniform="guidance")
        self._guidance_row.columnconfigure(1, weight=1, uniform="guidance")

        self._steps_card_open = bool(
            ctx.collapsible_states.get("dashboard.first_steps", True))
        steps = Card(self._guidance_row, c, border=c["panel_warn"])
        self._steps_card_w = steps
        steps_head = tk.Frame(steps.body, bg=c["surface"])
        steps_head.pack(fill="x")
        section_label(steps_head, c, t("First steps"), c["panel_warn"]).pack(
            side="left")
        self._steps_toggle = PillButton(
            steps_head, c, "▾" if self._steps_card_open else "▸", size=9, padx=7,
                                        height=26, command=self._toggle_steps_card)
        self._steps_toggle.pack(side="right")
        self._steps_box = tk.Frame(steps.body, bg=c["surface"])
        if self._steps_card_open:
            self._steps_box.pack(fill="x", pady=(10, 0))
        self._render_steps()

        summary = Card(self._guidance_row, c, border=c["panel_num"])
        tk.Label(summary.body, text=t("Inventory").upper(), bg=c["surface"],
                 fg=c["panel_num"], font=theme.mono(8, "bold")).pack(anchor="w")
        self._kv_rows = tk.Frame(summary.body, bg=c["surface"])
        self._kv_rows.pack(fill="x", pady=(8, 0))
        self._summary = summary
        self._refresh_summary()

        self._logpanel = Card(body, c, border=c["panel_request"])
        logbar = tk.Frame(self._logpanel.body, bg=c["surface"])
        self._logbar = logbar
        logbar.pack(fill="x")
        self._logs_title = section_label(
            logbar, c, t("Logs"), c["panel_request"])
        self._logs_title.pack(side="left")
        self._logs_toggle = PillButton(logbar, c, "▸", size=9, padx=7,
                                       height=26, command=self._toggle_logs)
        self._logs_toggle.pack(side="right")
        self._src = ttk.Combobox(
            logbar, state="readonly", width=12, font=theme.ui(9),
            values=[t("All"), "server", "app", "downloads"])
        self._src.current(0)
        self._src.pack(side="left", padx=(12, 0))
        self._src.bind("<<ComboboxSelected>>", lambda _e: self._reload_logs())
        clear_logs = PillButton(logbar, c, t("Clear"), size=9, padx=12,
                                height=26, command=self._clear_logs)
        clear_logs.pack(side="right")
        export_logs = PillButton(logbar, c, t("Export…"), size=9, padx=12,
                                 height=26, command=self._export_logs)
        export_logs.pack(side="right", padx=(0, 6))
        copy_logs = PillButton(logbar, c, t("Copy"), size=9, padx=12,
                               height=26, command=self._copy_logs)
        copy_logs.pack(side="right", padx=(0, 6))
        self._follow = tk.BooleanVar(value=True)
        follow_logs = ttk.Checkbutton(logbar, text=t("Follow"),
                                      variable=self._follow, takefocus=False)
        follow_logs.pack(side="right", padx=(0, 10))
        self._log_actions = (
            (clear_logs, {"side": "right"}),
            (export_logs, {"side": "right", "padx": (0, 6)}),
            (copy_logs, {"side": "right", "padx": (0, 6)}),
            (follow_logs, {"side": "right", "padx": (0, 10)}),
        )
        self._log_text = tk.Text(
            self._logpanel.body, height=10, bg=c["inset"], fg=c["text"], bd=0,
            padx=10, pady=8, font=theme.mono(8), wrap="none", state="disabled",
            highlightthickness=0)
        logscroll = ttk.Scrollbar(self._logpanel.body, orient="vertical",
                                  command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=logscroll.set)
        self._logscroll = logscroll
        # ScrollFrame listens globally to the wheel.  Handle it here first so
        # the cursor over the log moves the log, rather than the dashboard.
        self._log_text.bind("<MouseWheel>", self._scroll_logs)
        self._log_text.bind("<Button-4>", lambda _e: self._scroll_logs(-1))
        self._log_text.bind("<Button-5>", lambda _e: self._scroll_logs(1))
        logscroll.pack(side="right", fill="y")
        self._log_text.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        for level, token in _LEVEL_COLORS.items():
            self._log_text.tag_configure(level, foreground=c[token])
        self._log_text.tag_configure("meta", foreground=c["faint"])
        for source, token in _SOURCE_COLORS.items():
            self._log_text.tag_configure("source:" + source,
                                         foreground=c[token])
        self._reload_logs()

        # Recompose the dashboard body in operational order.  The original
        # hero is retained only while constructing its widgets above; all live
        # controls are moved into the fixed page header below.
        hero.pack_forget()
        details.pack_forget()
        ep.pack_forget()

        fixed = tk.Frame(head.actions, bg=c["bg"])
        fixed.pack()
        self._dot = StatusDot(fixed, c, size=16)
        self._dot.configure(bg=c["bg"])
        self._dot.pack(side="left")
        self._status_lbl = tk.Label(fixed, text=theme.track(t("Stopped")),
                                    bg=c["bg"], fg=c["text"],
                                    font=theme.mono(10, "bold"))
        self._status_lbl.pack(side="left", padx=(7, 0))
        self._uptime_lbl = tk.Label(fixed, text="", bg=c["bg"],
                                    fg=c["muted"], font=theme.mono(8))
        self._uptime_lbl.pack(side="left", padx=(7, 10))
        self._start_btn = PillButton(fixed, c, t("Start server"),
                                     kind="primary", size=9, padx=16,
                                     height=30, command=self._start)
        self._start_btn.pack(side="left")
        self._restart_btn = PillButton(fixed, c, t("Restart"), size=9,
                                       padx=14, height=30, command=self._restart)
        self._restart_btn.pack(side="left", padx=(6, 0))
        self._inventory_line = tk.Label(
            head.actions, text="", bg=c["bg"], fg=c["faint"],
            font=theme.mono(8), anchor="e")
        self._inventory_line.pack(anchor="e", pady=(3, 0))
        self._refresh_summary()

        # Resource use is fixed above; Logs itself owns its disclosure control.
        self._logpanel.pack(fill="x", padx=PAGE_PAD, pady=(14, 0))
        self._set_logs_open(bool(
            ctx.collapsible_states.get("dashboard.logs", False)))

        ep.pack(fill="x", padx=PAGE_PAD, pady=(14, 0))
        launch = CollapsibleCard(body, c, t("Launch command"),
                                 state_key="dashboard.launch",
                                 accent=c["panel_accent"])
        launch.pack(fill="x", padx=PAGE_PAD, pady=(14, 0))
        self._launch_card = launch
        self._launch_cmd = []
        cmd_box = tk.Frame(launch.content, bg=c["inset"])
        self._cmd_box = cmd_box
        cmd_box.pack(fill="x")
        self._cmd_copy = PillButton(cmd_box, c, t("Copy"), size=8, padx=9,
                                    height=24, command=self._copy_launch_cmd)
        self._cmd_copy.pack(side="right", padx=4, pady=4, anchor="n")
        self._cmd_copy.set_enabled(False)
        self._cmd = tk.Text(cmd_box, height=1, bg=c["inset"], fg=c["muted"],
                            bd=0, padx=10, pady=6, font=theme.mono(8),
                            wrap="word", state="disabled", takefocus=False,
                            highlightthickness=0)
        self._cmd.pack(side="left", fill="x", expand=True)

        # Inventory and First steps are always visible at the end of the
        # scrollable content, no longer controlled by separate disclosure
        # buttons.
        self._inventory_open = False
        self._steps_open = True
        self._guidance_row.pack(fill="x", padx=PAGE_PAD, pady=(14, PAGE_PAD))

        # Responsive row layout: side-by-side when wide, stacked when narrow.
        self._relayout_dash(self.winfo_width())
        self._scroll.bind("<Configure>",
                          lambda e: self._relayout_dash(e.width))

        self.subscribe("server_status", self.when_visible(self._on_status))
        self.subscribe("server_health",
                       self.when_visible(lambda d: self._tick_uptime()))
        self.subscribe("models_scanned",
                       self.when_visible(lambda d: self._refresh_summary()))
        self.subscribe("runtime_added",
                       self.when_visible(lambda d: self._refresh_summary()))
        self.subscribe("runtime_deleted",
                       self.when_visible(lambda d: self._refresh_summary()))
        self.subscribe("runtime_activated",
                       self.when_visible(lambda d: self._refresh_summary()))
        self.subscribe("gpu_stats", self.when_visible(self._on_gpu))
        self.subscribe("system_stats", self.when_visible(self._on_system))
        self.subscribe("log_line", self._on_log)
        self._refresh_cmd()
        server = self.ctx.services.get("server")
        if server is not None:
            self._on_status(server.get_status_dict())

    # ── Responsive ───────────────────────────────────────────────────────────

    def _relayout_dash(self, w: int) -> None:
        self._align_fixed_usage()
        if not getattr(self, "_guidance_row", None):
            return
        self._summary.grid_forget()
        self._steps_card_w.grid_forget()
        if not (self._inventory_open or self._steps_open):
            self._guidance_row.pack_forget()
        elif not self._guidance_row.winfo_ismapped():
            self._guidance_row.pack(fill="x", padx=PAGE_PAD, pady=(14, PAGE_PAD))
        if self._inventory_open and self._steps_open and w >= 720:
            self._summary.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
            self._steps_card_w.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        elif self._inventory_open and self._steps_open:
            self._summary.grid(row=0, column=0, columnspan=2, sticky="ew",
                               pady=(0, 12))
            self._steps_card_w.grid(row=1, column=0, columnspan=2, sticky="ew")
        elif self._inventory_open:
            self._summary.grid(row=0, column=0, columnspan=2, sticky="ew")
        else:
            self._steps_card_w.grid(row=0, column=0, columnspan=2, sticky="ew")

    def _align_fixed_usage(self) -> None:
        """Match the scroll body's right edge with or without its scrollbar."""
        gutter = 12 if self._scroll._vbar.winfo_ismapped() else 0
        self._fixed_usage.pack_configure(padx=(PAGE_PAD, PAGE_PAD + gutter))

    def _relayout_usage(self, event=None) -> None:
        """Keep CPU/RAM visible when the fixed resource strip narrows."""
        width = event.width if event is not None else self._usage_row.winfo_width()
        if width < 900:
            self._sys_card.grid_configure(row=0, column=0, columnspan=2,
                                          padx=0, pady=(0, 8))
            # Explicitly clear the side-by-side padding.  Monitor updates can
            # arrive before this layout pass and must not leave GPU indented.
            self._gpu_card.grid_configure(row=1, column=0, columnspan=2,
                                          padx=0, pady=0)
        else:
            self._sys_card.grid_configure(row=0, column=0, columnspan=1,
                                          padx=(0, 16), pady=0)
            self._gpu_card.grid_configure(row=0, column=1, columnspan=1,
                                          padx=0, pady=0)

    def _toggle_inventory(self) -> None:
        self._inventory_open = not self._inventory_open
        self._inventory_btn.set_text(
            ("▴ " if self._inventory_open else "▾ ") + t("Inventory"))
        self._relayout_dash(self._scroll.winfo_width())

    def _toggle_steps(self) -> None:
        self._steps_open = not self._steps_open
        self._steps_btn.set_text(
            ("▴ " if self._steps_open else "▾ ") + t("First steps"))
        self._relayout_dash(self._scroll.winfo_width())

    def _toggle_logs(self) -> None:
        self._set_logs_open(not self._logs_open)

    def _set_logs_open(self, open_: bool) -> None:
        self._logs_open = open_
        self.ctx.collapsible_states["dashboard.logs"] = open_
        self._logs_toggle.set_text("▾" if open_ else "▸")
        if open_:
            self._reload_logs()
            for child, options in self._log_actions:
                child.pack(**options)
            self._src.pack(side="left", padx=(12, 0))
            self._logscroll.pack(side="right", fill="y")
            self._log_text.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        else:
            for child, _options in self._log_actions:
                child.pack_forget()
            self._src.pack_forget()
            self._logscroll.pack_forget()
            self._log_text.pack_forget()
        self._refresh_dashboard_scroll()

    def _toggle_steps_card(self) -> None:
        self._steps_card_open = not self._steps_card_open
        self.ctx.collapsible_states["dashboard.first_steps"] = \
            self._steps_card_open
        self._steps_toggle.set_text("▾" if self._steps_card_open else "▸")
        if self._steps_card_open:
            self._steps_box.pack(fill="x", pady=(10, 0))
        else:
            self._steps_box.pack_forget()
        self._refresh_dashboard_scroll()

    def _refresh_dashboard_scroll(self) -> None:
        """Refresh a fill-height ScrollFrame after a child is collapsed."""
        self.after_idle(lambda: self._scroll._on_body(None))

    # ── Data ─────────────────────────────────────────────────────────────────

    def _endpoint_url(self) -> str:
        return self._base_url() + "/v1"

    def _base_url(self) -> str:
        cfg = self._config()
        host, port = "127.0.0.1", 8080
        server = self.ctx.services.get("server")
        if server:
            info = server.connection_info()
            host, port = info["host"], info["port"]
        elif cfg:
            host = cfg.server.effective_host()
            port = cfg.server.port
        host = _lan_address() if host == "0.0.0.0" else host
        return f"http://{host}:{port}"

    def _model_alias(self) -> str:
        profiles = self.ctx.services.get("profiles")
        if profiles:
            active = [p for p in profiles.list() if p.active and p.route_alias]
            if active:
                return active[0].route_alias
        return "your-model-alias"

    def _render_endpoints(self) -> None:
        for widget in self._endpoint_rows.winfo_children():
            widget.destroy()
        base = self._base_url()
        endpoints = (
            ("llama-server", base),
            ("OpenAI", base + "/v1"),
            ("Anthropic", base + "/v1/messages"),
        )
        for name, url in endpoints:
            row = tk.Frame(self._endpoint_rows, bg=self.c["surface"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=name, width=13, anchor="w",
                     bg=self.c["surface"], fg=self.c["muted"],
                     font=theme.ui(9, "bold")).pack(side="left")
            value_box = tk.Frame(row, bg=self.c["inset"])
            value_box.pack(side="left", fill="x", expand=True)
            tk.Label(value_box, text=url, anchor="w", bg=self.c["inset"],
                     fg=self.c["accent"], font=theme.mono(9), padx=8,
                     pady=5).pack(side="left", fill="x", expand=True)
            PillButton(value_box, self.c, t("Copy"), size=8, padx=9, height=24,
                       command=lambda value=url: self._copy_value(value)).pack(
                           side="right", padx=4, pady=3)
        cfg = self._config()
        api_key = cfg.server.api_key if cfg else ""
        key_row = tk.Frame(self._endpoint_rows, bg=self.c["surface"])
        key_row.pack(fill="x", pady=(7, 2))
        tk.Label(key_row, text=t("API key"), width=13, anchor="w",
                 bg=self.c["surface"], fg=self.c["muted"],
                 font=theme.ui(9, "bold")).pack(side="left")
        if api_key:
            key_text = api_key if cfg.show_api_details else t("Configured (hidden)")
            value_box = tk.Frame(key_row, bg=self.c["inset"])
            value_box.pack(side="left", fill="x", expand=True)
            tk.Label(value_box, text=key_text, anchor="w",
                     bg=self.c["inset"], fg=self.c["accent"],
                     font=theme.mono(9), padx=8, pady=5).pack(
                         side="left", fill="x", expand=True)
            PillButton(value_box, self.c, t("Copy"), size=8, padx=9, height=24,
                       command=lambda value=api_key: self._copy_value(value)).pack(
                           side="right", padx=4, pady=3)
        else:
            tk.Label(key_row, text=t("Not configured"), anchor="w",
                     bg=self.c["inset"], fg=self.c["faint"],
                     font=theme.mono(9), padx=8, pady=5).pack(
                         side="left", fill="x", expand=True)
        server = self.ctx.services.get("server")
        info = server.connection_info() if server else None
        is_lan = info["host"] == "0.0.0.0" if info else False
        note = (t("LAN access uses this computer's current address. Allow the port through the firewall.")
                if is_lan else
                t("Local access only. Choose Local network in Settings to connect from another device."))
        if info and info["pending_restart"]:
            note += "  " + t("Saved network changes apply after restarting the server.")
        tk.Label(self._endpoint_rows, text=note, bg=self.c["surface"],
                  fg=self.c["faint"], font=theme.ui(8), anchor="w",
                  justify="left", wraplength=650).pack(fill="x", pady=(7, 0))
        if self._examples_open:
            self._render_client_examples()

    def _toggle_client_examples(self) -> None:
        self._examples_open = not self._examples_open
        if self._examples_open:
            self._render_client_examples()
            self._client_examples_box.pack(fill="x", pady=(12, 0))
            self._examples_btn.set_text(t("Hide examples"))
        else:
            self._client_examples_box.pack_forget()
            self._examples_btn.set_text(t("Examples"))
        self._refresh_dashboard_scroll()

    def _render_client_examples(self) -> None:
        for widget in self._client_examples_box.winfo_children():
            widget.destroy()
        for label, example in self._client_examples().items():
            example_box = tk.Frame(self._client_examples_box, bg=self.c["surface"])
            example_box.pack(fill="x", pady=(0, 8))
            code_box = tk.Frame(example_box, bg=self.c["inset"])
            code_box.pack(fill="x")
            heading = tk.Frame(code_box, bg=self.c["inset"])
            heading.pack(fill="x", padx=(8, 4), pady=(3, 0))
            tk.Label(heading, text=label.upper(), bg=self.c["inset"],
                     fg=self.c["faint"], font=theme.mono(8, "bold")).pack(
                         side="left")
            PillButton(heading, self.c, t("Copy"), size=8, padx=9, height=24,
                        command=lambda value=example: self._copy_value(value)).pack(
                            side="right")
            # Shell examples are compact but wrap on a narrow card; SDK
            # examples need one row per source line so their tail never ends
            # up behind the Text widget's own viewport.
            code_rows = max(3, example.count("\n") + 1)
            code = tk.Text(code_box, height=code_rows, bg=self.c["inset"],
                           fg=self.c["text"], bd=0, padx=10, pady=8,
                           font=theme.mono(8), wrap="word",
                           insertbackground=self.c["text"])
            code.insert("1.0", example)
            self._highlight_code(code, example)
            code.configure(state="disabled")
            code.pack(fill="x", padx=1, pady=(0, 1))

    def _highlight_code(self, widget: tk.Text, text: str) -> None:
        """Apply a small, theme-aware syntax palette to dashboard snippets."""
        for name, color in (("keyword", self.c["accent"]),
                            ("function", self.c["ok"]),
                            ("string", self.c["request"]),
                            ("flag", self.c["accent"])):
            widget.tag_configure(name, foreground=color)
        patterns = (
            ("keyword", r"\b(?:from|import|const|new|await|return)\b"),
            ("function", r"\b(?:curl|OpenAI|print|console\.log|chat\.completions\.create)\b"),
            ("string", r'"[^"\n]*"|\'[^\'\n]*\''),
            ("flag", r"--[a-z0-9-]+"),
        )
        for tag, pattern in patterns:
            for match in re.finditer(pattern, text):
                widget.tag_add(tag, f"1.0+{match.start()}c",
                               f"1.0+{match.end()}c")

    def _client_examples(self) -> dict[str, str]:
        base, model = self._base_url(), self._model_alias()
        server = self.ctx.services.get("server")
        cfg = self._config()
        key_required = (server.connection_info()["api_key_required"] if server
                        else bool(cfg and cfg.server.api_key))
        key = (cfg.server.api_key if key_required and cfg.show_api_details
               else "YOUR_API_KEY" if key_required else "no-key")
        return {
            "cURL": (
                f'curl {base}/v1/chat/completions -H "Content-Type: application/json" '
                f'-H "Authorization: Bearer {key}" -d \'{{"model":"{model}",'
                '"messages":[{"role":"user","content":"Hello!"}]}\''),
            "Python": (
                "from openai import OpenAI\n\n"
                f'client = OpenAI(base_url="{base}/v1", api_key="{key}")\n'
                "reply = client.chat.completions.create(\n"
                f'    model="{model}",\n'
                '    messages=[{"role": "user", "content": "Hello!"}],\n'
                ")\nprint(reply.choices[0].message.content)"),
            "TypeScript": (
                "import OpenAI from \"openai\";\n\n"
                f'const client = new OpenAI({{ baseURL: "{base}/v1", apiKey: "{key}" }});\n'
                "const reply = await client.chat.completions.create({\n"
                f'  model: "{model}",\n'
                '  messages: [{ role: "user", content: "Hello!" }],\n'
                "});\nconsole.log(reply.choices[0].message.content);"),
            "Anthropic": (
                f'curl {base}/v1/messages -H "Content-Type: application/json" '
                f'-H "x-api-key: {key}" -d \'{{"model":"{model}","max_tokens":256,'
                '"messages":[{"role":"user","content":"Hello!"}]}\''),
        }

    def _show_client_guide(self) -> None:
        win = tk.Toplevel(self)
        win.title(t("Client connection guide"))
        win.configure(bg=self.c["bg"])
        win.geometry("760x520")
        win.minsize(620, 420)
        heading = tk.Frame(win, bg=self.c["bg"])
        heading.pack(fill="x", padx=20, pady=(18, 10))
        tk.Label(heading, text=t("Client connection guide"), bg=self.c["bg"],
                 fg=self.c["title"], font=theme.ui(15, "bold")).pack(anchor="w")
        tk.Label(heading, text=t("Use an active profile's route alias as the model name."),
                 bg=self.c["bg"], fg=self.c["muted"],
                 font=theme.ui(9)).pack(anchor="w", pady=(4, 0))
        tabs = ttk.Notebook(win)
        tabs.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        for label, example in self._client_examples().items():
            page = tk.Frame(tabs, bg=self.c["surface"])
            tabs.add(page, text=label)
            code = tk.Text(page, bg=self.c["inset"], fg=self.c["text"], bd=0,
                           padx=14, pady=14, font=theme.mono(9), wrap="word",
                           insertbackground=self.c["text"])
            code.insert("1.0", example)
            code.configure(state="disabled")
            code.pack(fill="both", expand=True, padx=10, pady=10)
            PillButton(page, self.c, t("Copy example"), size=9, padx=14,
                       command=lambda value=example: self._copy_value(value)).pack(
                           anchor="e", padx=10, pady=(0, 10))
        server = self.ctx.services.get("server")
        key_required = (server.connection_info()["api_key_required"] if server
                        else bool(self._config() and self._config().server.api_key))
        auth = (t("API key is enabled; replace YOUR_API_KEY with the configured key.")
                if key_required else
                t("No API key is configured. Set one before exposing the server to a network."))
        tk.Label(win, text=auth, bg=self.c["bg"], fg=self.c["warn"],
                 font=theme.ui(9), anchor="w").pack(fill="x", padx=20,
                                                   pady=(0, 16))

    def _copy_value(self, value: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(value)

    def _config(self):
        svc = self.ctx.services.get("config")
        return svc.get() if svc else None

    def _counts(self) -> tuple[str, str, str]:
        models = self.ctx.services.get("models")
        runtimes = self.ctx.services.get("runtimes")
        n_models = str(len(models.list())) if models else "—"
        if runtimes:
            active = runtimes.get_active()
            rt = active.version if active else t("none")
            n_rt = str(len(runtimes.list()))
        else:
            rt, n_rt = t("none"), "—"
        return n_models, n_rt, rt

    def _refresh_summary(self) -> None:
        c = self.c
        n_models, n_rt, active_rt = self._counts()
        if hasattr(self, "_inventory_line"):
            self._inventory_line.configure(
                text=f"{t('Registered models')}: {n_models}   "
                     f"{t('Installed runtimes')}: {n_rt}   "
                     f"{t('Active runtime')}: {active_rt}")
        for w in self._kv_rows.winfo_children():
            w.destroy()
        for k, v in ((t("Registered models"), n_models),
                     (t("Installed runtimes"), n_rt),
                     (t("Active runtime"), active_rt)):
            fg = c["faint"] if v in ("—", t("none")) else c["text"]
            key_value(self._kv_rows, c, k, v, value_fg=fg).pack(fill="x", pady=3)

    def _render_steps(self) -> None:
        c = self.c
        for w in self._steps_box.winfo_children():
            w.destroy()
        steps = [
            (t("Download a runtime"), t("pick a llama.cpp build in Runtime"), "runtime"),
            (t("Add your models"), t("scan a folder with GGUF files"), "models"),
            (t("Tune a profile"), t("context, GPU layers and route alias"), "profiles"),
            (t("Start the server"), t("then point your client at the endpoint"), "dashboard"),
        ]
        for i, (title, desc, page) in enumerate(steps, 1):
            row = tk.Frame(
                self._steps_box, bg=c["surface"], cursor="hand2",
                takefocus=True, highlightthickness=1,
                highlightbackground=c["border"],
                highlightcolor=c["accent_hi"])
            row._keyboard_nav = True
            row.pack(fill="x", pady=3, ipady=4)
            num = tk.Label(row, text=f"{i:02d}", bg=c["surface"], fg=c["accent"],
                           font=theme.mono(10, "bold"))
            num.pack(side="left")
            tt = tk.Label(row, text=title, bg=c["surface"], fg=c["text"],
                          font=theme.ui(10, "bold"))
            tt.pack(side="left", padx=(12, 8))
            d = tk.Label(row, text=desc, bg=c["surface"], fg=c["muted"],
                         font=theme.ui(9))
            d.pack(side="left")
            go = tk.Label(row, text="→", bg=c["surface"], fg=c["faint"],
                          font=theme.ui(10))
            go.pack(side="right")
            children = (row, num, tt, d, go)

            def _activate(_e=None, r=row, p=page):
                r.focus_set()
                self.ctx.navigate(p)
                return "break"

            for w in children:
                w.bind("<Button-1>", _activate)
            row.bind("<Key-space>", _activate)
            row.bind("<Key-Return>", _activate)

            def _enter(_e, ch=children):
                for w in ch:
                    w.configure(bg=c["surface_hi"])

            def _leave(_e, ch=children):
                focused = ch[0].focus_get() is ch[0]
                for w in ch:
                    w.configure(bg=c["surface_hi"] if focused else c["surface"])

            for w in children:
                w.bind("<Enter>", _enter)
                w.bind("<Leave>", _leave)
            row.bind("<FocusIn>", _enter)
            row.bind("<FocusOut>", _leave)

    # ── Events / actions ─────────────────────────────────────────────────────

    def _on_status(self, data: dict) -> None:
        status = (data or {}).get("status", "stopped")
        self._dot.set(status)
        self._status_lbl.configure(text=theme.track(status_label(status)))
        self._start_btn.set_enabled(status != "stopping")
        self._restart_btn.set_enabled(status == "running")
        self._start_btn.set_text(
            t("Stop") if status in ("running", "starting")
            else t("Start server"))
        if status == "running":
            self._hint.configure(text=t("Serving at {url}",
                                        url=self._endpoint_url()))
        elif status == "stopped":
            self._hint.configure(
                text=t("Install a runtime and add models before starting."))
        self._tick_uptime()
        self._render_endpoints()
        self._refresh_cmd()

    def _tick(self) -> None:
        self._tick_id = None
        if not self.winfo_exists() or not self._visible:
            return
        self._tick_uptime()
        self._tick_id = self.after(1000, self._tick)

    def _tick_uptime(self) -> None:
        server = self.ctx.services.get("server")
        if server is None:
            return
        d = server.get_status_dict()
        if d["uptime"]:
            routes = len(d["models"])
            txt = fmt_uptime(d["uptime"])
            if routes:
                txt += f"  ·  {routes} " + t("routes")
            self._uptime_lbl.configure(text=txt)
        else:
            self._uptime_lbl.configure(text="")

    def _meter_row(self, parent: tk.Widget, label: str):
        """A `LABEL  ▮▮▮▯▯  value` line; returns (bar, value_label)."""
        from llama_router.ui.widgets import SegmentBar
        c = self.c
        row = tk.Frame(parent, bg=c["surface"])
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=c["surface"], fg=c["text"],
                 font=theme.mono(9, "bold"), anchor="w", width=8).pack(side="left")
        bar = SegmentBar(row, c)
        bar.configure(bg=c["surface"])
        bar.pack(side="left", padx=(8, 12))
        val = tk.Label(row, text="", bg=c["surface"], fg=c["num"],
                       font=theme.mono(9, "bold"))
        val.pack(side="left")
        return bar, val

    def _on_system(self, d: dict) -> None:
        if not self._sys_card.winfo_ismapped():
            self._sys_card.grid(row=0, column=0, sticky="nsew")
            self._relayout_usage()
        self._cpu_bar.set(d["cpu"] / 100)
        self._cpu_val.configure(text=f"{d['cpu']:5.1f}%")
        total = max(1, d["mem_total"])
        self._ram_bar.set(d["mem_used"] / total)
        self._ram_val.configure(text=f"{d['mem_used'] / 1024:.1f}/"
                                     f"{total / 1024:.1f} GB")

    def _on_gpu(self, gpus: list[dict]) -> None:
        c = self.c
        if not self._gpu_card.winfo_ismapped():
            self._gpu_card.grid(row=0, column=1, sticky="nsew")
            self._relayout_usage()
        # (Re)build rows if GPU count changed
        if len(self._gpu_rows) != len(gpus):
            for w in self._gpu_box.winfo_children():
                w.destroy()
            self._gpu_rows = []
            from llama_router.ui.widgets import SegmentBar
            for _g in gpus:
                row = tk.Frame(self._gpu_box, bg=c["surface"])
                row.pack(fill="x", pady=3)
                name = tk.Label(row, text="", bg=c["surface"], fg=c["text"],
                                font=theme.mono(9, "bold"), anchor="w", width=18)
                name.pack(side="left")
                util = tk.Label(row, text="", bg=c["surface"], fg=c["num"],
                                font=theme.mono(9, "bold"), width=9)
                util.pack(side="left", padx=(8, 12))
                bar = SegmentBar(row, c)
                bar.configure(bg=c["surface"])
                bar.pack(side="left")
                mem = tk.Label(row, text="", bg=c["surface"], fg=c["num"],
                               font=theme.mono(9, "bold"))
                mem.pack(side="left", padx=(12, 0))
                self._gpu_rows.append((name, util, bar, mem))
        for (name, util, bar, mem), g in zip(self._gpu_rows, gpus):
            name.configure(text=g["name"].upper())
            util.configure(text=f"UTIL {g['util']:3d}%")
            bar.set(g["mem_used"] / max(1, g["mem_total"]))
            mem.configure(text=f"{g['mem_used'] / 1024:.1f}/"
                               f"{g['mem_total'] / 1024:.1f} GB")

    def _start(self) -> None:
        server = self.ctx.services.get("server")
        if server is None:
            self.ctx.navigate("runtime")
            return
        if server.is_running():
            server.stop_async()
            return
        result = server.start()
        if not result.get("ok"):
            target = {"no_runtime": "runtime", "no_models": "models",
                      "port_in_use": "settings"}.get(result.get("reason"))
            if result.get("reason") == "port_in_use":
                port = self.ctx.services["config"].get().server.port
                messagebox.showwarning(
                    t("Port unavailable"),
                    t("Port {port} is busy — stop the other process or change it in Settings.",
                      port=port),
                    parent=self)
            if target:
                self.ctx.navigate(target)
            else:
                self._show_reason(result)

    def _restart(self) -> None:
        server = self.ctx.services.get("server")
        if server is not None and server.is_running():
            server.restart_async()

    def _show_reason(self, result: dict) -> None:
        port = self.ctx.services["config"].get().server.port
        messages = {
            "no_runtime": t("No runtime installed — pick one on the Runtime page."),
            "no_models": t("No enabled model has an active profile — check Models and Profiles."),
            "port_in_use": t("Port {port} is busy — stop the other process or change it in Settings.", port=port),
            "busy": t("The server is already changing state — wait a moment."),
        }
        message = messages.get(result.get("reason"), result.get("error", ""))
        self._reason.configure(text="⚠  " + message)
        self._reason.pack(fill="x", pady=(8, 0))
        self.after(6000, self._reason.pack_forget)

    def _refresh_cmd(self) -> None:
        server = self.ctx.services.get("server")
        if server is None:
            return
        # Empty on a fresh install until the user selects a runtime.
        self._launch_cmd = server.build_cmd_preview()
        display_cmd = list(self._launch_cmd)
        if not self._config().show_api_details:
            for index, arg in enumerate(display_cmd):
                if arg == "--api-key" and index + 1 < len(display_cmd):
                    display_cmd[index + 1] = "••••••••"
                elif arg.startswith("--api-key="):
                    display_cmd[index] = "--api-key=••••••••"
        preview = (self._format_cmd(display_cmd) if display_cmd
                   else t("No runtime selected"))
        self._cmd.configure(state="normal", height=max(1, preview.count("\n") + 1))
        self._cmd.delete("1.0", "end")
        self._cmd.insert("1.0", preview)
        self._highlight_code(self._cmd, preview)
        self._cmd.configure(state="disabled")
        self._cmd_copy.set_enabled(bool(self._launch_cmd))

    @staticmethod
    def _format_cmd(cmd: list[str]) -> str:
        """Lay out a command preview as readable flag/value lines."""
        lines = [cmd[0]]
        index = 1
        while index < len(cmd):
            arg = cmd[index]
            if (arg.startswith("--") and index + 1 < len(cmd)
                    and not cmd[index + 1].startswith("--")):
                lines.append(f"  {arg} {cmd[index + 1]}")
                index += 2
            else:
                lines.append(f"  {arg}")
                index += 1
        return "\n".join(lines)

    def _copy_launch_cmd(self) -> None:
        if self._launch_cmd:
            self._copy_value(" ".join(self._launch_cmd))

    def _log_sources(self) -> list[str] | None:
        selected = self._src.get()
        return None if selected == t("All") else [selected]

    def _reload_logs(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        for entry in self.ctx.logs.get(limit=500, sources=self._log_sources()):
            self._append_log(entry)
        self._log_text.configure(state="disabled")
        self._log_text.see("end")

    def _on_log(self, entry: dict) -> None:
        if not self._visible:
            self._logs_dirty = True
            return
        sources = self._log_sources()
        if sources and entry["source"] not in sources:
            return
        self._log_text.configure(state="normal")
        self._append_log(entry)
        if float(self._log_text.index("end-1c").split(".")[0]) > 2000:
            self._log_text.delete("1.0", "200.0")
        self._log_text.configure(state="disabled")
        if self._follow.get():
            self._log_text.see("end")

    def _append_log(self, entry: dict) -> None:
        import time as _time
        timestamp = _time.strftime("%H:%M:%S", _time.localtime(entry["ts"]))
        self._log_text.insert("end", f"{timestamp} ", ("meta",))
        source = entry.get("source", "app")
        self._log_text.insert("end", f"[{source:<9}] ",
                              ("source:" + source,))
        self._log_text.insert("end", entry["message"] + "\n",
                              (entry["level"],))

    def _clear_logs(self) -> None:
        self.ctx.logs.clear()
        self._reload_logs()

    def _scroll_logs(self, event_or_steps) -> str:
        """Scroll the log and prevent the dashboard's global wheel binding."""
        if isinstance(event_or_steps, int):
            steps = event_or_steps
        else:
            # Windows normally sends ±120, while macOS may send much smaller
            # deltas.  Only the direction matters for one Tk scroll unit.
            steps = -1 if event_or_steps.delta > 0 else (
                1 if event_or_steps.delta < 0 else 0)
        if steps:
            self._log_text.yview_scroll(steps, "units")
        return "break"

    def _visible_logs(self) -> str:
        return self._log_text.get("1.0", "end-1c")

    def _copy_logs(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._visible_logs())

    def _export_logs(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title=t("Export logs"), defaultextension=".log",
            filetypes=[(t("Log files"), "*.log"),
                       (t("Text files"), "*.txt"),
                       (t("All files"), "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as out:
                out.write(self._visible_logs())
        except OSError as exc:
            self.ctx.logs.log("app", "error", f"Could not export logs: {exc}")

    def on_show(self) -> None:
        server = self.ctx.services.get("server")
        if server is not None:
            self._on_status(server.get_status_dict())
        self._refresh_summary()
        self._render_endpoints()
        self._refresh_cmd()
        if self._logs_dirty and self._logs_open:
            self._reload_logs()
        self._logs_dirty = False
        if self._tick_id is None:
            self._tick()

    def on_hide(self) -> None:
        if self._tick_id is not None:
            self.after_cancel(self._tick_id)
            self._tick_id = None
