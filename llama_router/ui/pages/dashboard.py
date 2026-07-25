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
import socket
import tkinter as tk
from tkinter import ttk

from llama_router.i18n import t
from llama_router.ui import theme
from llama_router.ui.pages.base import PAGE_PAD, Page
from llama_router.ui.widgets import (Card, PillButton, ScrollFrame, StatusDot,
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
        self.header(t("panel"), t("Dashboard"), t("Router status and first steps"))

        # Scroll container: keeps the entire dashboard reachable on short
        # windows instead of clipping the lower cards.
        self._scroll = ScrollFrame(self, c)
        self._scroll.pack(fill="both", expand=True)
        body = self._scroll.body

        # ── Hero: server state ───────────────────────────────────────────────
        hero = Card(body, c, pad=22)
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
        tk.Label(hero.body, text=t("Launch command").upper(), bg=c["surface"],
                 fg=c["faint"], font=theme.mono(8, "bold")).pack(
                     anchor="w", pady=(12, 4))
        self._cmd = tk.Label(hero.body, text="—", bg=c["inset"], fg=c["muted"],
                             font=theme.mono(8), anchor="w", justify="left",
                             padx=10, pady=6)
        self._cmd.pack(fill="x")

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

        # ── Row: endpoint + inventory (restacks vertically when narrow) ──────
        self._row = tk.Frame(body, bg=c["bg"])
        self._row.pack(fill="x", padx=PAGE_PAD, pady=(14, 0))
        self._row.columnconfigure(0, weight=1, uniform="dashboard-cards")
        self._row.columnconfigure(1, weight=1, uniform="dashboard-cards")

        ep = Card(self._row, c)
        ep_row = tk.Frame(ep.body, bg=c["surface"])
        ep_row.pack(fill="x")
        tk.Label(ep_row, text=t("Connect your client").upper(),
                 bg=c["surface"], fg=c["faint"],
                 font=theme.mono(8, "bold")).pack(side="left", padx=(0, 12))
        PillButton(ep_row, c, t("Examples"), command=self._show_client_guide,
                   size=9, padx=14, height=30).pack(side="right")
        self._endpoint_rows = tk.Frame(ep.body, bg=c["surface"])
        self._endpoint_rows.pack(fill="x", pady=(10, 0))
        self._render_endpoints()

        summary = Card(self._row, c)
        tk.Label(summary.body, text=t("Inventory").upper(), bg=c["surface"],
                 fg=c["faint"], font=theme.mono(8, "bold")).pack(anchor="w")
        self._kv_rows = tk.Frame(summary.body, bg=c["surface"])
        self._kv_rows.pack(fill="x", pady=(8, 0))

        self._ep, self._summary = ep, summary
        self._refresh_summary()

        # ── System (CPU / RAM) ───────────────────────────────────────────────
        self._usage_row = tk.Frame(body, bg=c["bg"])
        self._usage_row.pack(fill="x", padx=PAGE_PAD, pady=(14, 0))
        self._usage_row.columnconfigure(0, weight=1, uniform="usage")
        self._usage_row.columnconfigure(1, weight=1, uniform="usage")
        self._sys_card = Card(self._usage_row, c)
        tk.Label(self._sys_card.body, text=t("System").upper(), bg=c["surface"],
                 fg=c["faint"], font=theme.mono(8, "bold")).pack(anchor="w")
        sys_box = tk.Frame(self._sys_card.body, bg=c["surface"])
        sys_box.pack(fill="x", pady=(8, 0))
        self._cpu_bar, self._cpu_val = self._meter_row(sys_box, "CPU")
        self._ram_bar, self._ram_val = self._meter_row(sys_box, "RAM")

        # ── GPU ──────────────────────────────────────────────────────────────
        self._gpu_card = Card(self._usage_row, c)
        tk.Label(self._gpu_card.body, text="GPU", bg=c["surface"],
                 fg=c["faint"], font=theme.mono(8, "bold")).pack(anchor="w")
        self._gpu_box = tk.Frame(self._gpu_card.body, bg=c["surface"])
        self._gpu_box.pack(fill="x", pady=(8, 0))
        self._gpu_rows: list[tuple] = []

        # ── Getting started ──────────────────────────────────────────────────
        steps = Card(body, c)
        self._steps_card_w = steps
        tk.Label(steps.body, text=t("First steps").upper(), bg=c["surface"],
                 fg=c["faint"], font=theme.mono(8, "bold")).pack(anchor="w")
        self._steps_box = tk.Frame(steps.body, bg=c["surface"])
        self._steps_box.pack(fill="x", pady=(10, 0))
        self._render_steps()
        # Secondary guidance is collapsed until requested.

        self._logpanel = tk.Frame(body, bg=c["surface"],
                                  highlightbackground=c["border"],
                                  highlightthickness=1)
        logbar = tk.Frame(self._logpanel, bg=c["surface"])
        logbar.pack(fill="x", padx=12, pady=(8, 4))
        section_label(logbar, c, t("Logs")).pack(side="left")
        self._src = ttk.Combobox(
            logbar, state="readonly", width=12, font=theme.ui(9),
            values=[t("All"), "server", "app", "downloads"])
        self._src.current(0)
        self._src.pack(side="left", padx=(12, 0))
        self._src.bind("<<ComboboxSelected>>", lambda _e: self._reload_logs())
        PillButton(logbar, c, t("Clear"), size=9, padx=12, height=26,
                   command=self._clear_logs).pack(side="right")
        self._follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(logbar, text=t("Follow"), variable=self._follow,
                        takefocus=False).pack(side="right", padx=(0, 10))
        self._log_text = tk.Text(
            self._logpanel, height=10, bg=c["inset"], fg=c["text"], bd=0,
            padx=10, pady=8, font=theme.mono(8), wrap="none", state="disabled",
            highlightthickness=0)
        logscroll = ttk.Scrollbar(self._logpanel, orient="vertical",
                                  command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=logscroll.set)
        logscroll.pack(side="right", fill="y")
        self._log_text.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        for level, token in _LEVEL_COLORS.items():
            self._log_text.tag_configure(level, foreground=c[token])
        self._log_text.tag_configure("meta", foreground=c["faint"])
        for source, token in _SOURCE_COLORS.items():
            self._log_text.tag_configure("source:" + source,
                                         foreground=c[token])
        self._reload_logs()

        # Responsive row layout: side-by-side when wide, stacked when narrow.
        self._relayout_dash(self.winfo_width())
        self._scroll.bind("<Configure>",
                          lambda e: self._relayout_dash(e.width))

        self.subscribe("server_status", self._on_status)
        self.subscribe("server_health", lambda d: self._tick_uptime())
        self.subscribe("models_scanned", lambda d: self._refresh_summary())
        self.subscribe("runtime_added", lambda d: self._refresh_summary())
        self.subscribe("runtime_deleted", lambda d: self._refresh_summary())
        self.subscribe("runtime_activated", lambda d: self._refresh_summary())
        self.subscribe("gpu_stats", self._on_gpu)
        self.subscribe("system_stats", self._on_system)
        self.subscribe("log_line", self._on_log)
        self._refresh_cmd()
        server = self.ctx.services.get("server")
        if server is not None:
            self._on_status(server.get_status_dict())
        self._tick()

    # ── Responsive ───────────────────────────────────────────────────────────

    def _relayout_dash(self, w: int) -> None:
        if not getattr(self, "_row", None):
            return
        self._ep.grid_forget()
        self._summary.grid_forget()
        if not self._inventory_open:
            self._ep.grid(row=0, column=0, columnspan=2, sticky="ew")
        elif w < 720:
            self._ep.grid(row=0, column=0, columnspan=2, sticky="ew",
                          pady=(0, 12))
            self._summary.grid(row=1, column=0, columnspan=2, sticky="ew")
        else:
            self._ep.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
            self._summary.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

    def _toggle_inventory(self) -> None:
        self._inventory_open = not self._inventory_open
        self._inventory_btn.set_text(
            ("▴ " if self._inventory_open else "▾ ") + t("Inventory"))
        self._relayout_dash(self._scroll.winfo_width())

    def _toggle_steps(self) -> None:
        self._steps_open = not self._steps_open
        self._steps_btn.set_text(
            ("▴ " if self._steps_open else "▾ ") + t("First steps"))
        if self._steps_open:
            self._steps_card_w.pack(fill="x", padx=PAGE_PAD,
                                    pady=(14, PAGE_PAD))
        else:
            self._steps_card_w.pack_forget()

    def _toggle_logs(self) -> None:
        self._logs_open = not self._logs_open
        self._logs_btn.set_text(
            ("▴ " if self._logs_open else "▾ ") + t("Logs"))
        if self._logs_open:
            self._reload_logs()
            self._logpanel.pack(fill="both", expand=True, padx=PAGE_PAD,
                                pady=(14, PAGE_PAD))
        else:
            self._logpanel.pack_forget()

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
            tk.Label(row, text=url, anchor="w", bg=self.c["inset"],
                     fg=self.c["accent"], font=theme.mono(9), padx=8,
                     pady=5).pack(side="left", fill="x", expand=True)
            PillButton(row, self.c, t("Copy"), size=8, padx=9, height=26,
                       command=lambda value=url: self._copy_value(value)).pack(
                           side="left", padx=(6, 0))
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

    def _client_examples(self) -> dict[str, str]:
        base, model = self._base_url(), self._model_alias()
        server = self.ctx.services.get("server")
        cfg = self._config()
        key_required = (server.connection_info()["api_key_required"] if server
                        else bool(cfg and cfg.server.api_key))
        key = "YOUR_API_KEY" if key_required else "no-key"
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
            "JavaScript": (
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
        for w in self._kv_rows.winfo_children():
            w.destroy()
        n_models, n_rt, active_rt = self._counts()
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
            row = tk.Frame(self._steps_box, bg=c["surface"], cursor="hand2")
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
            for w in children:
                w.bind("<Button-1>", lambda e, p=page: self.ctx.navigate(p))

            def _enter(_e, ch=children):
                for w in ch:
                    w.configure(bg=c["surface_hi"])

            def _leave(_e, ch=children):
                for w in ch:
                    w.configure(bg=c["surface"])

            for w in children:
                w.bind("<Enter>", _enter)
                w.bind("<Leave>", _leave)

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
        if not self.winfo_exists():
            return
        self._tick_uptime()
        self.after(1000, self._tick)

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
            self._sys_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self._cpu_bar.set(d["cpu"] / 100)
        self._cpu_val.configure(text=f"{d['cpu']:5.1f}%")
        total = max(1, d["mem_total"])
        self._ram_bar.set(d["mem_used"] / total)
        self._ram_val.configure(text=f"{d['mem_used'] / 1024:.1f}/"
                                     f"{total / 1024:.1f} GB")

    def _on_gpu(self, gpus: list[dict]) -> None:
        c = self.c
        if not self._gpu_card.winfo_ismapped():
            self._gpu_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
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
        cmd = server.build_cmd_preview()
        self._cmd.configure(text=" ".join(cmd) if cmd
                            else t("No runtime selected"))

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

    def on_show(self) -> None:
        self._refresh_summary()
        self._render_endpoints()
        self._refresh_cmd()
