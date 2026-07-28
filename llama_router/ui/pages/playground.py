"""Playground — chat with the managed llama-server without leaving the app.

The transcript is a read-only `tk.Text`. Each message's characters carry a
`m{index}` tag, which is how the right-click menu maps a click back to a
message, and how a re-render can be a plain wipe-and-rebuild. Streaming
appends raw deltas; the finished message is re-rendered once so fenced code
blocks get their inset styling and a COPY button.
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

from llama_router.i18n import t
from llama_router.ui import theme
from llama_router.ui.pages.base import PAGE_PAD, Page
from llama_router.ui.widgets import (AutoScrollbar, Card, CollapsibleCard, PillButton,
                                    ScrollFrame, section_label)

_MAX_ATTACH = 200 * 1024        # per file, bytes
_FENCE = re.compile(r"```[^\n]*\n(.*?)(?:```|\Z)", re.S)
_ROLE_LABELS = {"user": "You", "assistant": "Assistant", "system": "System"}


class PlaygroundPage(Page):
    def __init__(self, parent: tk.Widget, ctx) -> None:
        super().__init__(parent, ctx)
        self._attachments: list[dict] = []   # {"name", "content", "chars"}
        self._streaming = False
        self._stream_id: int | None = None
        self._stream_dirty = False
        self._embedded: list[tk.Widget] = []  # widgets inside the transcript
        self._sidebar_open = False
        self._build()
        self._session = self._svc.new_session() if self._svc else {"messages": []}
        self._render()
        self._refresh_models()
        self._sync_server_state()

        self.subscribe("server_status",
                       self.when_visible(lambda _d: self._sync_server_state()))
        self.subscribe("server_health",
                       self.when_visible(lambda _d: self._refresh_models()))
        self.subscribe("pg_token", self._on_token)
        self.subscribe("pg_done", self._on_done)
        self.subscribe("pg_error", self._on_error)

    # ── Services ─────────────────────────────────────────────────────────────

    @property
    def _svc(self):
        return self.ctx.services.get("playground")

    @property
    def _server(self):
        return self.ctx.services.get("server")

    @property
    def _messages(self) -> list[dict]:
        return self._session["messages"]

    # ── Build ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        c = self.c
        head = self.header(t("chat"), t("Playground"))

        model_wrap = tk.Frame(head.actions, bg=c["bg"])
        model_wrap.pack(side="left", padx=(0, 8))
        tk.Label(model_wrap, text=t("Model").upper(), bg=c["bg"],
                 fg=c["panel_accent"], font=theme.mono(7, "bold")).pack(anchor="w")
        self._model = ttk.Combobox(model_wrap, state="readonly", width=22,
                                   font=theme.mono(9))
        self._model.pack()
        self._temp = self._param(head.actions, t("temp"), "0.8")
        self._max_tok = self._param(head.actions, t("Max tokens"), "1024")
        PillButton(head.actions, c, t("New session"), size=9, padx=12,
                   height=28, command=self._new_session).pack(
                       side="left", padx=(8, 6), pady=(12, 0))
        PillButton(head.actions, c, t("Sessions"), size=9, padx=12, height=28,
                   command=self._toggle_sidebar).pack(side="left", pady=(12, 0))

        # Keep the composer outside the page scroller: it must remain usable
        # while a long transcript, banner, or system prompt is being viewed.
        self._composer = composer = tk.Frame(self, bg=c["bg"])
        composer.pack(side="bottom", fill="x")

        # The history and auxiliary controls may scroll in a short window.
        scroll = ScrollFrame(self, c, fill_height=True)
        scroll.pack(fill="both", expand=True)
        content = scroll.body

        # ── Offline banner ───────────────────────────────────────────────────
        self._banner = Card(content, c, pad=12, border=c["panel_warn"])
        brow = tk.Frame(self._banner.body, bg=c["surface"])
        brow.pack(fill="x")
        tk.Label(brow, text=t("The server is not running — start it to chat."),
                 bg=c["surface"], fg=c["warn"], font=theme.ui(9)).pack(side="left")
        PillButton(brow, c, t("Server"), kind="accent", size=8, padx=12,
                   height=26,
                   command=lambda: self.ctx.navigate("dashboard")).pack(side="right")

        # ── System prompt (collapsible) ──────────────────────────────────────
        self._sys_open = False
        self._sys_card = CollapsibleCard(
            content, c, t("System prompt"), expanded=False, pad=12,
            on_toggle=lambda open_: setattr(self, "_sys_open", open_),
            state_key="playground.system_prompt", accent=c["panel_num"])
        self._sys_open = self._sys_card.is_open
        self._sys_card.pack(fill="x", padx=PAGE_PAD)
        self._sysrow = self._sys_card
        self._sys = tk.Text(self._sys_card.content, height=3, bg=c["inset"],
                            fg=c["text"], insertbackground=c["accent"], bd=0,
                            padx=10, pady=7, font=theme.mono(9), wrap="word",
                            highlightthickness=1, highlightbackground=c["border"])
        self._sys.pack(fill="x")

        # ── Body: optional session sidebar + transcript ──────────────────────
        body = tk.Frame(content, bg=c["bg"])
        body.pack(fill="both", expand=True, padx=PAGE_PAD, pady=(10, 0))

        self._sidebar = tk.Frame(body, bg=c["surface"],
                                 highlightbackground=c["panel_request"],
                                 highlightthickness=1, width=210)
        self._sidebar.pack_propagate(False)
        section_label(self._sidebar, c, t("Saved sessions"),
                      c["panel_request"]).pack(
            anchor="w", padx=10, pady=(9, 6))
        self._sess_list = tk.Frame(self._sidebar, bg=c["surface"])
        self._sess_list.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        panel = tk.Frame(body, bg=c["surface"],
                         highlightbackground=c["panel_accent"],
                         highlightthickness=1)
        panel.pack(side="right", fill="both", expand=True)
        self._text = tk.Text(panel, bg=c["inset"], fg=c["text"], bd=0,
                             padx=12, pady=10, font=theme.ui(10), wrap="word",
                             state="disabled", highlightthickness=0,
                             spacing1=2, spacing3=2)
        vbar = AutoScrollbar(panel, orient="vertical",
                             command=self._text.yview)
        self._text.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        self._text.pack(fill="both", expand=True, padx=1, pady=1)
        self._text.tag_configure("user", foreground=c["accent"])
        self._text.tag_configure("assistant", foreground=c["text"])
        self._text.tag_configure("meta", foreground=c["faint"],
                                 font=theme.mono(8, "bold"), spacing1=8)
        self._text.tag_configure("error", foreground=c["error"],
                                 font=theme.mono(9))
        self._text.tag_configure("code", background=c["surface"],
                                 foreground=c["text"], font=theme.mono(9),
                                 lmargin1=10, lmargin2=10, rmargin=10)
        self._text.bind("<Button-3>", self._on_right_click)

        # ── Attachment chips ─────────────────────────────────────────────────
        self._chips = tk.Frame(composer, bg=c["bg"])

        # ── Input ────────────────────────────────────────────────────────────
        self._inputrow = row = tk.Frame(composer, bg=c["bg"])
        row.pack(fill="x", padx=PAGE_PAD, pady=(10, PAGE_PAD))
        self._input = tk.Text(row, height=3, bg=c["inset"], fg=c["text"],
                              insertbackground=c["accent"], bd=0, padx=10,
                              pady=7, font=theme.ui(10), wrap="word",
                              highlightthickness=1,
                              highlightbackground=c["panel_accent"])
        self._input.pack(side="left", fill="both", expand=True)
        self._input.bind("<Return>", self._on_return)
        btns = tk.Frame(row, bg=c["bg"])
        btns.pack(side="right", padx=(10, 0))
        PillButton(btns, c, t("Attach"), size=9, padx=12, height=28,
                   command=self._attach).pack(fill="x")
        self._send_btn = PillButton(btns, c, t("Send"), kind="primary", size=9,
                                    padx=12, height=30, command=self._send)
        self._send_btn.pack(fill="x", pady=(6, 0))

    def _param(self, parent: tk.Widget, label: str, value: str) -> ttk.Entry:
        wrap = tk.Frame(parent, bg=self.c["bg"])
        wrap.pack(side="left", padx=(0, 8))
        tk.Label(wrap, text=label.upper(), bg=self.c["bg"], fg=self.c["faint"],
                 font=theme.mono(7, "bold")).pack(anchor="w")
        e = ttk.Entry(wrap, width=6, font=theme.mono(9))
        e.insert(0, value)
        e.pack()
        return e

    # ── Server / model state ─────────────────────────────────────────────────

    def _sync_server_state(self) -> None:
        srv = self._server
        running = bool(srv and getattr(srv.status, "value", srv.status) == "running")
        if running:
            self._banner.pack_forget()
        else:
            self._banner.pack(fill="x", padx=PAGE_PAD, pady=(0, 10),
                              before=self._sysrow)
        self._send_btn.set_enabled(running or self._streaming)

    def _refresh_models(self) -> None:
        if self._svc is None:
            return
        names = self._svc.models()
        current = self._model.get()
        self._model["values"] = names
        if current not in names:
            self._model.set(names[0] if names else "")

    # ── Rendering ────────────────────────────────────────────────────────────

    def _clear_embedded(self) -> None:
        for w in self._embedded:
            try:
                w.destroy()
            except Exception:
                pass
        self._embedded.clear()

    def _render(self) -> None:
        self._clear_embedded()
        txt = self._text
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        for i, m in enumerate(self._messages):
            self._insert_message(i, m)
        txt.configure(state="disabled")
        txt.see("end")

    def _insert_message(self, i: int, m: dict) -> None:
        txt, tag = self._text, f"m{i}"
        role = m.get("role", "assistant")
        head = t(_ROLE_LABELS.get(role, role)).upper()
        stats = m.get("stats") or {}
        if stats.get("tokens"):
            head += f"   {stats['tokens']} tok · {stats.get('tps', 0):.1f} t/s"
        txt.insert("end", head + "\n", ("meta", tag))
        for a in m.get("attachments") or ():
            txt.insert("end", f"  📎 {a['name']}  ({a.get('chars', 0)} chars)\n",
                       ("meta", tag))

        content = m.get("text", m.get("content", ""))
        pos = 0
        for match in _FENCE.finditer(content):
            plain = content[pos:match.start()]
            if plain.strip():
                txt.insert("end", plain.strip() + "\n", (role, tag))
            self._insert_code(match.group(1).rstrip(), tag)
            pos = match.end()
        rest = content[pos:]
        if rest.strip():
            txt.insert("end", rest.strip() + "\n", (role, tag))

    def _insert_code(self, code: str, tag: str) -> None:
        txt = self._text
        btn = PillButton(txt, self.c, t("Copy"), size=7, padx=8, height=20,
                         command=lambda s=code: self._to_clipboard(s))
        self._embedded.append(btn)
        txt.window_create("end", window=btn)
        txt.insert("end", "\n", (tag,))
        txt.insert("end", code + "\n", ("code", tag))

    def _to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)

    # ── Sending ──────────────────────────────────────────────────────────────

    def _on_return(self, event) -> str | None:
        if event.state & 0x0001:      # Shift held → newline
            return None
        self._send()
        return "break"

    def _params(self) -> dict:
        out: dict = {}
        try:
            out["temperature"] = float(self._temp.get().strip())
        except ValueError:
            pass
        try:
            out["max_tokens"] = int(self._max_tok.get().strip())
        except ValueError:
            pass
        return out

    def _send(self) -> None:
        if self._streaming:
            self._stop()
            return
        typed = self._input.get("1.0", "end-1c").strip()
        if not typed and not self._attachments:
            return
        content = typed
        for a in self._attachments:
            content += f"\n\n{a['name']}:\n```\n{a['content']}\n```"
        self._messages.append({
            "role": "user", "content": content, "text": typed,
            "attachments": [{"name": a["name"], "chars": a["chars"]}
                            for a in self._attachments],
        })
        self._input.delete("1.0", "end")
        self._attachments = []
        self._render_chips()
        self._request()

    def _request(self) -> None:
        svc = self._svc
        if svc is None:
            return
        wire = []
        system = self._sys.get("1.0", "end-1c").strip()
        if system:
            wire.append({"role": "system", "content": system})
        wire += [{"role": m["role"], "content": m["content"]}
                 for m in self._messages]

        self._messages.append({"role": "assistant", "content": ""})
        self._render()
        self._streaming = True
        self._send_btn.set_kind("accent")
        self._send_btn.set_text(t("Stop"))
        self._send_btn.set_enabled(True)
        self._stream_id = svc.send(wire, self._model.get(), self._params())

    def _stop(self) -> None:
        if self._svc is not None:
            self._svc.cancel()
        self._finish()

    def _finish(self) -> None:
        self._streaming = False
        self._send_btn.set_kind("primary")
        self._send_btn.set_text(t("Send"))
        self._sync_server_state()

    # ── Stream events ────────────────────────────────────────────────────────

    def _on_token(self, data: dict) -> None:
        if not self._streaming or data.get("session") != self._stream_id:
            return
        self._messages[-1]["content"] += data["text"]
        if not self._visible:
            self._stream_dirty = True
            return
        self._text.configure(state="normal")
        self._text.insert("end", data["text"],
                          ("assistant", f"m{len(self._messages) - 1}"))
        self._text.configure(state="disabled")
        self._text.see("end")

    def _on_done(self, data: dict) -> None:
        if data.get("session") != getattr(self, "_stream_id", None):
            return
        self._messages[-1]["content"] = data.get("text", "")
        self._messages[-1]["stats"] = data.get("stats") or {}
        if not self._visible:
            self._streaming = False
            self._stream_dirty = True
            self._autosave()
            return
        self._finish()
        self._render()
        self._autosave()

    def _on_error(self, data: dict) -> None:
        if data.get("session") != getattr(self, "_stream_id", None):
            return
        if self._messages and self._messages[-1]["role"] == "assistant" \
                and not self._messages[-1]["content"]:
            self._messages.pop()
        self._finish()
        self._render()
        self._text.configure(state="normal")
        self._text.insert("end", "\n⚠  " + str(data.get("error", "")) + "\n",
                          ("error",))
        self._text.configure(state="disabled")
        self._text.see("end")

    # ── Message context menu ─────────────────────────────────────────────────

    def _on_right_click(self, event) -> None:
        idx = self._text.index(f"@{event.x},{event.y}")
        hit = next((n for n in self._text.tag_names(idx)
                    if re.fullmatch(r"m\d+", n)), None)
        c = self.c
        menu = tk.Menu(self, tearoff=0, bg=c["surface"], fg=c["text"],
                       activebackground=c["accent"],
                       activeforeground=c["on_accent"], bd=0,
                       font=theme.ui(9))
        if hit is not None and not self._streaming:
            i = int(hit[1:])
            menu.add_command(label=t("Copy message"),
                             command=lambda: self._to_clipboard(
                                 self._messages[i].get("content", "")))
            menu.add_command(label=t("Edit"), command=lambda: self._edit(i))
            menu.add_command(label=t("Regenerate"),
                             command=lambda: self._regenerate(i))
            menu.add_command(label=t("Delete message"),
                             command=lambda: self._delete_message(i))
            menu.add_separator()
        menu.add_command(label=t("Clear chat"), command=self._clear_chat)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _edit(self, i: int) -> None:
        m = self._messages[i]
        self._input.delete("1.0", "end")
        self._input.insert("1.0", m.get("text", m.get("content", "")))
        del self._messages[i:]
        self._render()

    def _regenerate(self, i: int) -> None:
        # Drop the clicked message (and everything after) when it is a reply;
        # for a user turn, keep it and re-ask from there.
        cut = i if self._messages[i]["role"] == "assistant" else i + 1
        del self._messages[cut:]
        if self._messages:
            self._request()

    def _delete_message(self, i: int) -> None:
        del self._messages[i]
        self._render()
        self._autosave()

    def _clear_chat(self) -> None:
        self._messages.clear()
        self._render()
        self._autosave()

    # ── Attachments ──────────────────────────────────────────────────────────

    def _attach(self) -> None:
        names = filedialog.askopenfilenames(
            parent=self, title=t("Attach text files"))
        for name in names or ():
            path = Path(name)
            try:
                if path.stat().st_size > _MAX_ATTACH:
                    raise ValueError(t("file is larger than 200 KB"))
                content = path.read_text(encoding="utf-8")
            except (OSError, ValueError, UnicodeDecodeError) as e:
                messagebox.showwarning(
                    t("Attachment skipped"), f"{path.name}\n{e}", parent=self)
                continue
            self._attachments.append({"name": path.name, "content": content,
                                      "chars": len(content)})
        self._render_chips()

    def _render_chips(self) -> None:
        for w in self._chips.winfo_children():
            w.destroy()
        if not self._attachments:
            self._chips.pack_forget()
            return
        c = self.c
        self._chips.pack(fill="x", padx=PAGE_PAD, pady=(8, 0),
                         before=self._inputrow)
        for a in list(self._attachments):
            chip = tk.Frame(self._chips, bg=c["surface"],
                            highlightbackground=c["border"], highlightthickness=1)
            chip.pack(side="left", padx=(0, 6))
            tk.Label(chip, text=f"📎 {a['name']}", bg=c["surface"],
                     fg=c["muted"], font=theme.mono(8)).pack(side="left",
                                                             padx=(8, 4), pady=3)
            x = tk.Label(chip, text="✕", bg=c["surface"], fg=c["faint"],
                         font=theme.mono(8), cursor="hand2")
            x.pack(side="left", padx=(0, 7))
            x.bind("<Button-1>", lambda _e, item=a: self._drop_chip(item))

    def _drop_chip(self, item: dict) -> None:
        if item in self._attachments:
            self._attachments.remove(item)
        self._render_chips()

    # ── Sessions ─────────────────────────────────────────────────────────────

    def _autosave(self) -> None:
        if self._svc is None or not self._messages:
            return
        self._session["model"] = self._model.get()
        self._session["params"] = self._params()
        self._session["system_prompt"] = self._sys.get("1.0", "end-1c").strip()
        self._svc.save_session(self._session)
        if self._sidebar_open:
            self._render_sessions()

    def _new_session(self) -> None:
        if self._svc is None:
            return
        self._autosave()
        self._session = self._svc.new_session(model=self._model.get())
        self._sys.delete("1.0", "end")
        self._render()
        if self._sidebar_open:
            self._render_sessions()

    def _load_session(self, data: dict) -> None:
        self._autosave()
        self._session = data
        self._sys.delete("1.0", "end")
        self._sys.insert("1.0", data.get("system_prompt", ""))
        if data.get("model"):
            self._model.set(data["model"])
        self._render()

    def _toggle_sidebar(self) -> None:
        self._sidebar_open = not self._sidebar_open
        if self._sidebar_open:
            self._sidebar.pack(side="left", fill="y", padx=(0, 10))
            self._render_sessions()
        else:
            self._sidebar.pack_forget()

    def _render_sessions(self) -> None:
        for w in self._sess_list.winfo_children():
            w.destroy()
        c = self.c
        rows = self._svc.sessions() if self._svc else []
        if not rows:
            tk.Label(self._sess_list, text=t("No saved sessions yet."),
                     bg=c["surface"], fg=c["faint"], font=theme.ui(9),
                     wraplength=180, justify="left").pack(anchor="w", padx=6)
            return
        for s in rows:
            selected = s["id"] == self._session.get("id")
            row = tk.Frame(
                self._sess_list, bg=c["surface"], cursor="hand2",
                highlightbackground=c["request"] if selected else c["border"],
                highlightthickness=1)
            row.pack(fill="x", pady=1)
            name = tk.Label(row, text=s.get("name") or s["id"], bg=c["surface"],
                            fg=c["request"] if selected else c["text"],
                            font=theme.ui(9), anchor="w")
            name.pack(fill="x", padx=8, pady=(4, 0))
            meta = tk.Label(row, text=f"{len(s.get('messages') or [])} msg  ·  "
                                      f"{s.get('updated_at', '')}",
                            bg=c["surface"], fg=c["faint"], font=theme.mono(7),
                            anchor="w")
            meta.pack(fill="x", padx=8, pady=(0, 4))
            for w in (row, name, meta):
                w.bind("<Button-1>", lambda _e, d=s: self._load_session(d))
                w.bind("<Button-3>", lambda e, d=s: self._session_menu(e, d))
                w.bind("<Enter>", lambda _e, r=row: self._hover_row(r, True))
                w.bind("<Leave>", lambda _e, r=row: self._hover_row(r, False))

    def _hover_row(self, row: tk.Frame, on: bool) -> None:
        bg = self.c["surface_hi"] if on else self.c["surface"]
        row.configure(bg=bg)
        for w in row.winfo_children():
            w.configure(bg=bg)

    def _session_menu(self, event, data: dict) -> None:
        c = self.c
        menu = tk.Menu(self, tearoff=0, bg=c["surface"], fg=c["text"],
                       activebackground=c["accent"],
                       activeforeground=c["on_accent"], bd=0, font=theme.ui(9))
        menu.add_command(label=t("Rename"), command=lambda: self._rename(data))
        menu.add_command(label=t("Export…"), command=lambda: self._export(data))
        menu.add_command(label=t("Delete"),
                         command=lambda: self._delete_session(data))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _rename(self, data: dict) -> None:
        new = simpledialog.askstring(t("Rename"), t("Session name"),
                                     initialvalue=data.get("name", ""),
                                     parent=self)
        if not new:
            return
        data["name"] = new
        self._svc.save_session(data)
        self._render_sessions()

    def _delete_session(self, data: dict) -> None:
        self._svc.delete_session(data["id"])
        if data["id"] == self._session.get("id"):
            self._session = self._svc.new_session(model=self._model.get())
            self._render()
        self._render_sessions()

    def _export(self, data: dict) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title=t("Export session"), defaultextension=".md",
            initialfile=(data.get("name") or data["id"]).replace(" ", "_"),
            filetypes=[("Markdown", "*.md"), ("JSON", "*.json")])
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                import json
                text = json.dumps(data, ensure_ascii=False, indent=2)
            else:
                text = _to_markdown(data)
            Path(path).write_text(text, encoding="utf-8")
        except OSError as e:
            messagebox.showwarning(t("Export failed"), str(e), parent=self)

    # ── System prompt ────────────────────────────────────────────────────────

    def _toggle_system(self) -> None:
        self._sys_card.toggle()

    # ── Theme-flip state ─────────────────────────────────────────────────────

    def _serialize(self) -> dict:
        return {"draft": self._input.get("1.0", "end-1c"),
                "system": self._sys.get("1.0", "end-1c"),
                "system_open": self._sys_open,
                "sidebar": self._sidebar_open,
                "model": self._model.get(),
                "temp": self._temp.get(), "max": self._max_tok.get(),
                "session": self._session,
                "attachments": self._attachments}

    def _restore(self, d: dict) -> None:
        self._session = d.get("session") or self._session
        self._attachments = d.get("attachments") or []
        self._input.delete("1.0", "end")
        self._input.insert("1.0", d.get("draft", ""))
        self._sys.delete("1.0", "end")
        self._sys.insert("1.0", d.get("system", ""))
        self._sys_card.set_open(bool(d.get("system_open")))
        if d.get("sidebar"):
            self._toggle_sidebar()
        if d.get("model"):
            self._model.set(d["model"])
        for entry, key in ((self._temp, "temp"), (self._max_tok, "max")):
            entry.delete(0, "end")
            entry.insert(0, d.get(key, ""))
        self._render_chips()
        self._render()

    def on_show(self) -> None:
        if self._stream_dirty:
            self._render()
            self._stream_dirty = False
        self._refresh_models()
        self._sync_server_state()


def _to_markdown(data: dict) -> str:
    lines = [f"# {data.get('name') or data.get('id', 'session')}", ""]
    if data.get("system_prompt"):
        lines += ["> " + data["system_prompt"], ""]
    for m in data.get("messages") or ():
        lines += [f"## {_ROLE_LABELS.get(m.get('role'), m.get('role', ''))}",
                  "", m.get("content", ""), ""]
    return "\n".join(lines)
