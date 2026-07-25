"""Server — llama-server process control and unified log stream."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from llama_router.i18n import t
from llama_router.ui import theme
from llama_router.ui.pages.base import PAGE_PAD, Page
from llama_router.ui.widgets import (Card, PillButton, StatusDot, fmt_uptime,
                                    section_label, status_label)

_LEVEL_COLORS = {"error": "error", "warning": "warn", "info": "muted",
                 "request": "request", "debug": "faint"}
_SOURCE_COLORS = {"server": "accent", "app": "ok", "downloads": "request"}


class ServerPage(Page):
    def __init__(self, parent: tk.Widget, ctx) -> None:
        super().__init__(parent, ctx)
        c = self.c
        self.header(t("llama-server process"), t("Server"))

        # ── Status card ──────────────────────────────────────────────────────
        card = Card(self, c, pad=18)
        card.pack(fill="x", padx=PAGE_PAD)
        row = tk.Frame(card.body, bg=c["surface"])
        row.pack(fill="x")
        self._dot = StatusDot(row, c, size=16)
        self._dot.configure(bg=c["surface"])
        self._dot.pack(side="left")
        self._status_lbl = tk.Label(row, text=t("Stopped"), bg=c["surface"],
                                    fg=c["text"], font=theme.mono(12, "bold"))
        self._status_lbl.pack(side="left", padx=(8, 0))
        self._meta = tk.Label(row, text="", bg=c["surface"], fg=c["muted"],
                              font=theme.mono(9))
        self._meta.pack(side="left", padx=(12, 0), pady=(2, 0))

        controls = tk.Frame(card.body, bg=c["surface"])
        controls.pack(pady=(14, 2))
        self._toggle_btn = PillButton(controls, c, t("Start server"),
                                      kind="primary", size=10, padx=26,
                                      height=36, command=self._toggle)
        self._toggle_btn.pack(side="left", padx=(0, 8))
        self._restart_btn = PillButton(controls, c, t("Restart"), size=10,
                                       padx=22, height=36,
                                       command=self._restart)
        self._restart_btn.pack(side="left")

        self._reason = tk.Label(card.body, text="", bg=c["surface"],
                                fg=c["warn"], font=theme.ui(9), anchor="w",
                                justify="left")

        tk.Label(card.body, text=t("Launch command").upper(), bg=c["surface"],
                 fg=c["faint"], font=theme.mono(8, "bold")).pack(
            anchor="w", pady=(12, 4))
        self._cmd = tk.Label(card.body, text="—", bg=c["inset"], fg=c["muted"],
                             font=theme.mono(8), anchor="w", justify="left",
                             padx=10, pady=7)
        self._cmd.pack(fill="x")

        # ── Logs ─────────────────────────────────────────────────────────────
        logpanel = tk.Frame(self, bg=c["surface"],
                            highlightbackground=c["border"],
                            highlightthickness=1)
        logpanel.pack(fill="both", expand=True, padx=PAGE_PAD,
                      pady=(12, PAGE_PAD))
        bar = tk.Frame(logpanel, bg=c["surface"])
        bar.pack(fill="x", padx=12, pady=(8, 4))
        section_label(bar, c, t("Logs")).pack(side="left")

        self._src = ttk.Combobox(bar, state="readonly", width=12,
                                 font=theme.ui(9),
                                 values=[t("All"), "server", "app", "downloads"])
        self._src.current(0)
        self._src.pack(side="left", padx=(12, 0))
        self._src.bind("<<ComboboxSelected>>", lambda e: self._reload_logs())

        PillButton(bar, c, t("Clear"), size=9, padx=12, height=26,
                   command=self._clear_logs).pack(side="right")
        self._follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=t("Follow"), variable=self._follow,
                        style="TCheckbutton",
                        takefocus=False).pack(side="right", padx=(0, 10))

        self._text = tk.Text(logpanel, bg=c["inset"], fg=c["text"], bd=0,
                             padx=10, pady=8, font=theme.mono(8),
                             wrap="none", state="disabled",
                             highlightthickness=0)
        vbar = ttk.Scrollbar(logpanel, orient="vertical",
                             command=self._text.yview)
        self._text.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        self._text.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        for level, token in _LEVEL_COLORS.items():
            self._text.tag_configure(level, foreground=c[token])
        self._text.tag_configure("meta", foreground=c["faint"])
        for source, token in _SOURCE_COLORS.items():
            self._text.tag_configure("source:" + source,
                                     foreground=c[token])

        self.subscribe("server_status", self._on_status)
        self.subscribe("server_health", lambda d: self._tick_meta())
        self.subscribe("log_line", self._on_log)
        self._reload_logs()
        self._refresh_cmd()
        self._tick()

    # ── Services ─────────────────────────────────────────────────────────────

    @property
    def _server(self):
        return self.ctx.services.get("server")

    # ── Actions ──────────────────────────────────────────────────────────────

    def _toggle(self) -> None:
        srv = self._server
        if srv is None:
            return
        if srv.is_running():
            self._toggle_btn.set_enabled(False)
            srv.stop_async()
        else:
            result = srv.start()
            if not result.get("ok"):
                self._show_reason(result)

    def _restart(self) -> None:
        srv = self._server
        if srv is not None and srv.is_running():
            srv.restart_async()

    def _show_reason(self, result: dict) -> None:
        reason = result.get("reason", "")
        port = self.ctx.services["config"].get().server.port
        msgs = {
            "no_runtime": t("No runtime installed — pick one on the Runtime page."),
            "no_models": t("No enabled model has an active profile — check Models and Profiles."),
            "port_in_use": t("Port {port} is busy — stop the other process or change it in Settings.", port=port),
            "busy": t("The server is already changing state — wait a moment."),
        }
        text = msgs.get(reason, result.get("error", ""))
        self._reason.configure(text="⚠  " + text)
        self._reason.pack(fill="x", pady=(8, 0))
        self.after(6000, self._reason.pack_forget)

    # ── Status rendering ─────────────────────────────────────────────────────

    def _on_status(self, data: dict) -> None:
        status = (data or {}).get("status", "stopped")
        self._dot.set(status)
        self._status_lbl.configure(text=status_label(status))
        self._toggle_btn.set_enabled(status not in ("stopping",))
        self._toggle_btn.set_text(
            t("Stop") if status in ("running", "starting") else t("Start server"))
        err = (data or {}).get("error")
        if err and status == "error":
            self._reason.configure(text="⚠  " + str(err))
            self._reason.pack(fill="x", pady=(8, 0))
        self._tick_meta()
        self._refresh_cmd()

    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        self._tick_meta()
        self.after(1000, self._tick)

    def _tick_meta(self) -> None:
        srv = self._server
        if srv is None:
            return
        d = srv.get_status_dict()
        parts = []
        if d["pid"]:
            parts.append(f"pid {d['pid']}")
        if d["uptime"]:
            parts.append("up " + fmt_uptime(d["uptime"]))
        if d["models"]:
            parts.append(f"{len(d['models'])} " + t("routes"))
        self._meta.configure(text="  ·  ".join(parts))

    def _refresh_cmd(self) -> None:
        srv = self._server
        if srv is None:
            return
        cmd = srv.build_cmd_preview()
        self._cmd.configure(
            text=" ".join(cmd) if cmd else t("No runtime selected"))

    # ── Logs ─────────────────────────────────────────────────────────────────

    def _sources(self) -> list[str] | None:
        sel = self._src.get()
        return None if sel == t("All") else [sel]

    def _reload_logs(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        for e in self.ctx.logs.get(limit=500, sources=self._sources()):
            self._append(e)
        self._text.configure(state="disabled")
        self._text.see("end")

    def _on_log(self, e: dict) -> None:
        src = self._sources()
        if src and e["source"] not in src:
            return
        self._text.configure(state="normal")
        self._append(e)
        # Cap the widget at ~2000 lines so long sessions stay snappy
        if float(self._text.index("end-1c").split(".")[0]) > 2000:
            self._text.delete("1.0", "200.0")
        self._text.configure(state="disabled")
        if self._follow.get():
            self._text.see("end")

    def _append(self, e: dict) -> None:
        import time as _t
        ts = _t.strftime("%H:%M:%S", _t.localtime(e["ts"]))
        self._text.insert("end", f"{ts} ", ("meta",))
        source = e.get("source", "app")
        self._text.insert("end", f"[{source:<9}] ",
                          ("source:" + source,))
        self._text.insert("end", e["message"] + "\n", (e["level"],))

    def _clear_logs(self) -> None:
        self.ctx.logs.clear()
        self._reload_logs()

    def on_show(self) -> None:
        self._refresh_cmd()
        self._tick_meta()
