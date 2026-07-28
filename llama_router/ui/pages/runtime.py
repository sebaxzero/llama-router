"""Runtime — install llama.cpp builds from GitHub releases, import local
builds, pick the active runtime."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk

from llama_router.core.utils import fmt_bytes
from llama_router.i18n import t
from llama_router.ui import theme
from llama_router.ui.pages.base import PAGE_PAD, Page
from llama_router.ui.widgets import (CollapsibleCard, PillButton, ScrollFrame,
                                    enable_row_hover)


class RuntimePage(Page):
    def __init__(self, parent: tk.Widget, ctx) -> None:
        super().__init__(parent, ctx)
        c = self.c
        head = self.header(t("llama.cpp builds"), t("Runtime"),
                           t("Prebuilt binaries from ggerganov/llama.cpp releases"))
        PillButton(head.actions, c, t("Import local build"),
                   command=self._import_local).pack(side="left", padx=(0, 8))
        self._refresh_btn = PillButton(head.actions, c, t("Refresh releases"),
                                       command=self._fetch)
        self._refresh_btn.pack(side="left")

        scroll = ScrollFrame(self, c, fill_height=True)
        scroll.pack(fill="both", expand=True)
        content = scroll.body

        # ── Installed ────────────────────────────────────────────────────────
        inst_card = CollapsibleCard(content, c, t("Installed"), pad=14,
                                    state_key="runtime.installed",
                                    accent=c["panel_ok"])
        inst_card.pack(fill="x", padx=PAGE_PAD, pady=(0, 12))
        inst_panel = inst_card.content
        self._activate_btn = PillButton(inst_card.header, c, t("Use this runtime"),
                                        kind="primary", size=9, padx=12,
                                        height=28, command=self._activate)
        self._activate_btn.pack(side="right", padx=(0, 6))
        self._delete_btn = PillButton(inst_card.header, c, t("Delete"), size=9,
                                      padx=12, height=28, command=self._delete)
        self._delete_btn.pack(side="right", padx=(0, 8))

        cols = ("active", "name", "backend", "state")
        self._inst = ttk.Treeview(inst_panel, columns=cols, show="headings",
                                  height=4, selectmode="browse")
        for col, txt, w, anchor in (
                ("active", "", 40, "center"),
                ("name", t("Name"), 280, "w"),
                ("backend", t("Backend"), 100, "center"),
                ("state", t("State"), 100, "center")):
            self._inst.heading(col, text=txt)
            self._inst.column(col, width=w, minwidth=w, anchor=anchor,
                              stretch=(col == "name"))
        self._inst.pack(fill="both", expand=True, padx=1, pady=1)
        self._inst.tag_configure("active", foreground=c["ok"])
        self._inst.tag_configure("invalid", foreground=c["error"])
        enable_row_hover(self._inst, c)
        self._inst_empty = tk.Label(inst_panel,
                                    text=t("No runtimes yet — download one below."),
                                    bg=c["surface"], fg=c["muted"],
                                    font=theme.ui(9))

        # ── Releases ─────────────────────────────────────────────────────────
        rel_card = CollapsibleCard(content, c, t("Available releases"), pad=14,
                                   state_key="runtime.releases",
                                   accent=c["panel_request"])
        rel_card.pack(fill="x", padx=PAGE_PAD, pady=(0, 12))
        rel_panel = rel_card.content
        self._release_cb = ttk.Combobox(rel_card.header, state="readonly", width=28,
                                        font=theme.mono(9))
        self._release_cb.pack(side="left", padx=(14, 0))
        self._release_cb.bind("<<ComboboxSelected>>",
                              lambda e: self._show_assets())
        self._dl_btn = PillButton(rel_card.header, c, t("Download & install"),
                                  kind="primary", size=9, padx=12, height=28,
                                  command=self._install)
        self._dl_btn.pack(side="right")

        acols = ("name", "size")
        self._assets = ttk.Treeview(rel_panel, columns=acols, show="headings",
                                    selectmode="browse")
        self._assets.heading("name", text=t("Asset"))
        self._assets.heading("size", text=t("Size"))
        self._assets.column("name", width=360, minwidth=200, stretch=True)
        self._assets.column("size", width=90, anchor="e", stretch=False)
        self._assets.pack(fill="both", expand=True, padx=1, pady=1)
        enable_row_hover(self._assets, c)
        self._rel_status = tk.Label(rel_panel, text="", bg=c["surface"],
                                    fg=c["muted"], font=theme.ui(9))

        # ── Downloads strip ──────────────────────────────────────────────────
        self._dl_strip = tk.Frame(content, bg=c["bg"])
        self._dl_strip.pack(fill="x", padx=PAGE_PAD, pady=(0, PAGE_PAD))
        self._dl_rows: dict[str, tuple[tk.Frame, ttk.Progressbar, tk.Label]] = {}

        self._releases: list[dict] = []
        self._fetched = False
        self._pending_progress: dict[str, dict] = {}

        self.subscribe("runtime_added",
                       self.when_visible(lambda d: self._refresh_installed()))
        self.subscribe("runtime_deleted",
                       self.when_visible(lambda d: self._refresh_installed()))
        self.subscribe("runtime_activated",
                       self.when_visible(lambda d: self._refresh_installed()))
        self.subscribe("gh_releases", self._on_releases)
        self.subscribe("download_progress", self._on_progress)
        self._refresh_installed()

    # ── Services ─────────────────────────────────────────────────────────────

    @property
    def _rt(self):
        return self.ctx.services["runtimes"]

    # ── Installed list ───────────────────────────────────────────────────────

    def _refresh_installed(self) -> None:
        self._inst.delete(*self._inst.get_children())
        runtimes = self._rt.list()
        active = self._rt.get_active()
        for r in runtimes:
            mark = "●" if (active and r.id == active.id) else ""
            state = t("invalid") if r.state == "invalid" else t("ready")
            tags = (("invalid",) if r.state == "invalid" else
                    (("active",) if active and r.id == active.id else ()))
            self._inst.insert("", "end", iid=r.id,
                              values=(mark, r.name, r.backend, state),
                              tags=tags)
        if runtimes:
            self._inst_empty.pack_forget()
        else:
            self._inst_empty.pack(pady=(0, 10))

    def _activate(self) -> None:
        sel = self._inst.selection()
        if sel:
            self._rt.set_active(sel[0])

    def _delete(self) -> None:
        sel = self._inst.selection()
        if sel:
            self._rt.delete(sel[0])

    def _import_local(self) -> None:
        folder = filedialog.askdirectory(parent=self)
        if not folder:
            return
        name = simpledialog.askstring(
            t("Import local build"), t("Name for this runtime:"),
            parent=self) or "custom"
        try:
            self._rt.import_local(folder, name)
        except FileNotFoundError:
            self._rel_status.configure(
                text=t("llama-server executable not found in that folder"))
            self._rel_status.pack(pady=(0, 8))
            self.after(4000, self._rel_status.pack_forget)

    # ── Releases ─────────────────────────────────────────────────────────────

    def _fetch(self) -> None:
        self._refresh_btn.set_enabled(False)
        self._refresh_btn.set_text(t("Fetching…"))

        def work() -> None:
            releases = self._rt.fetch_releases()
            self.ctx.events.publish("gh_releases", releases)

        threading.Thread(target=work, daemon=True, name="gh-fetch").start()

    def _on_releases(self, releases: list[dict]) -> None:
        self._refresh_btn.set_enabled(True)
        self._refresh_btn.set_text(t("Refresh releases"))
        self._releases = releases or []
        if not self._releases:
            self._rel_status.configure(
                text=t("Could not reach GitHub — check your connection."))
            self._rel_status.pack(pady=(0, 8))
            return
        self._rel_status.pack_forget()
        self._release_cb.configure(
            values=[f"{r['tag']}  ·  {r['published'][:10]}"
                    for r in self._releases])
        self._release_cb.current(0)
        self._show_assets()

    def _show_assets(self) -> None:
        self._assets.delete(*self._assets.get_children())
        i = self._release_cb.current()
        if not (0 <= i < len(self._releases)):
            return
        for j, a in enumerate(self._releases[i]["assets"]):
            self._assets.insert("", "end", iid=str(j),
                                values=(a["name"], fmt_bytes(a["size"], "mb")))

    def _install(self) -> None:
        i = self._release_cb.current()
        sel = self._assets.selection()
        if not (0 <= i < len(self._releases)) or not sel:
            return
        rel = self._releases[i]
        asset = rel["assets"][int(sel[0])]
        self._rt.install_asset(rel["tag"], asset, all_assets=rel.get("all_assets", []))

    # ── Download progress strip ──────────────────────────────────────────────

    def _on_progress(self, d: dict) -> None:
        if not self._visible:
            self._pending_progress[d["id"]] = d
            return
        c = self.c
        dl_id = d["id"]
        state = d["state"]
        if state in ("completed", "cancelled"):
            row = self._dl_rows.pop(dl_id, None)
            if row:
                row[0].destroy()
            return
        if dl_id not in self._dl_rows:
            row = tk.Frame(self._dl_strip, bg=c["bg"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=d["name"], bg=c["bg"], fg=c["muted"],
                     font=theme.mono(8)).pack(side="left")
            info = tk.Label(row, text="", bg=c["bg"], fg=c["faint"],
                            font=theme.mono(8))
            info.pack(side="right")
            bar = ttk.Progressbar(row, length=220, mode="determinate")
            bar.pack(side="right", padx=10)
            self._dl_rows[dl_id] = (row, bar, info)
        _row, bar, info = self._dl_rows[dl_id]
        total = d.get("total_bytes") or 0
        done = d.get("downloaded_bytes") or 0
        if state == "failed":
            info.configure(text=t("failed — {err}", err=d.get("error", ""))[:80],
                           fg=c["error"])
            bar["value"] = 0
            return
        if total:
            bar["maximum"] = total
            bar["value"] = done
        speed = d.get("speed_bps") or 0
        info.configure(
            text=f'{fmt_bytes(done, "mb")} / {fmt_bytes(total, "mb")}'
                 f"  ·  {speed / (1024**2):.1f} MB/s",
            fg=c["faint"])

    def on_show(self) -> None:
        self._refresh_installed()
        pending, self._pending_progress = self._pending_progress, {}
        for progress in pending.values():
            self._on_progress(progress)
        if not self._fetched and self._auto_check():
            self._fetched = True
            self._fetch()

    def _auto_check(self) -> bool:
        return self.ctx.services["config"].get().auto_check_releases
