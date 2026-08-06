"""The single model configuration surface: models-preset.ini."""
from __future__ import annotations

import tkinter as tk
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from llama_router.core.utils import fmt_bytes, sanitise
from llama_router.i18n import t
from llama_router.preset import (PresetDocument, TextEdit, apply_edits,
                                 normalize_editor_text, normalize_model_path)
from llama_router.services.preset_manager import PresetValidationError
from llama_router.ui import theme
from llama_router.ui.pages.base import PAGE_PAD, Page
from llama_router.ui.widgets import PillButton


class ModelPresetPage(Page):
    """Lossless text editor with small, disposable helpers around it."""

    def __init__(self, parent: tk.Widget, ctx) -> None:
        super().__init__(parent, ctx)
        self._ignore_modified = False
        self._validate_id: str | None = None
        self._base_snapshot = None
        self._base_view = ""
        self._conflict = False
        self._build()
        self._load_saved()
        self.subscribe("preset_changed", self._on_preset_changed)
        self.subscribe("models_scanned", lambda _d: self._validate_now())
        self.subscribe("parameter_catalog_changed", lambda _d: self._validate_now())
        self.subscribe("runtime_activated", self._on_runtime_activated)

    @property
    def _preset(self):
        return self.ctx.services["preset"]

    @property
    def _models(self):
        return self.ctx.services["models"]

    @property
    def _catalog(self):
        return self.ctx.services.get("catalog")

    def _build(self) -> None:
        c = self.c
        head = self.header(t("configuration"), t("Model Preset"),
                           t("The saved INI is the source of truth for model routes."))
        actions = head.actions
        self._buttons: dict[str, PillButton] = {}
        for key, label, kind in (
                ("save", "Save", "primary"),
                ("reload", "Reload", "ghost"),
                ("validate", "Validate", "ghost"),
                ("add_model", "Add model", "accent"),
                ("add_param", "Add parameter", "accent")):
            button = PillButton(actions, c, t(label), kind=kind, size=8,
                                padx=10, height=28,
                                command=getattr(self, f"_{key}"))
            button.pack(side="left", padx=(0, 5), pady=(10, 0))
            self._buttons[key] = button
        more = tk.Menubutton(actions, text="⋯", bg=c["surface"], fg=c["text"],
                             activebackground=c["surface_hi"],
                             activeforeground=c["accent"], relief="flat",
                             font=theme.mono(11, "bold"), padx=7, pady=3)
        menu = tk.Menu(more, tearoff=False, bg=c["surface"], fg=c["text"],
                       activebackground=c["accent"], activeforeground=c["on_accent"])
        menu.add_command(label=t("Scan models"), command=self._scan)
        menu.add_command(label=t("Add folder"), command=self._add_folder)
        menu.add_command(label=t("Restore last backup"), command=self._restore_backup)
        menu.add_command(label=t("Open file location"), command=self._open_location)
        more.configure(menu=menu)
        more.pack(side="left", padx=(2, 8), pady=(10, 0))
        self._apply_btn = PillButton(actions, c, t("Apply to server"),
                                     kind="primary", size=8, padx=10, height=28,
                                     command=self._apply_server)
        self._apply_btn.pack_forget()

        body = tk.Frame(self, bg=c["bg"])
        body.pack(fill="both", expand=True, padx=PAGE_PAD, pady=(0, PAGE_PAD))
        self._status = tk.Label(body, text="", bg=c["bg"], fg=c["faint"],
                                anchor="w", font=theme.mono(8))
        self._status.pack(fill="x", pady=(0, 6))

        editor = tk.Frame(body, bg=c["surface"], highlightbackground=c["border"],
                          highlightthickness=1)
        editor.pack(fill="both", expand=True)
        self._text = tk.Text(editor, undo=True, wrap="none", bg=c["inset"],
                             fg=c["text"], insertbackground=c["accent"],
                             selectbackground=c["accent"],
                             selectforeground=c["on_accent"], bd=0, padx=12,
                             pady=10, font=theme.mono(9),
                             highlightthickness=0)
        ybar = ttk.Scrollbar(editor, orient="vertical", command=self._text.yview)
        xbar = ttk.Scrollbar(editor, orient="horizontal", command=self._text.xview)
        self._text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        ybar.pack(side="right", fill="y")
        xbar.pack(side="bottom", fill="x")
        self._text.pack(fill="both", expand=True)
        self._text.bind("<<Modified>>", self._on_modified)
        self._text.bind("<Control-s>", lambda _e: self._save() or "break")
        self._text.bind("<Control-Shift-R>", lambda _e: self._reload() or "break")
        self._text.bind("<F8>", lambda _e: self._validate_now() or "break")
        self._text.bind("<Control-Shift-M>", lambda _e: self._add_model() or "break")
        self._text.bind("<Control-k>", lambda _e: self._add_param() or "break")
        self._text.bind("<KeyRelease>", self._update_cursor_status, add="+")

        self._diag = tk.Listbox(body, height=5, bg=c["surface"], fg=c["text"],
                                selectbackground=c["accent"],
                                selectforeground=c["on_accent"], bd=0,
                                highlightthickness=1,
                                highlightbackground=c["border"],
                                font=theme.mono(8))
        self._diag.pack(fill="x", pady=(8, 0))
        self._diag.bind("<Double-Button-1>", self._jump_diagnostic)

    def _load_saved(self) -> None:
        snap = self._preset.load(force=True, origin="ui")
        self._base_snapshot = snap
        self._base_view = snap.text
        self._conflict = False
        self._replace_text(snap.text)
        self._show_diagnostics(snap.document)
        self._update_state()

    def _replace_text(self, text: str) -> None:
        self._ignore_modified = True
        try:
            self._text.delete("1.0", "end")
            self._text.insert("1.0", text)
            self._text.edit_modified(False)
        finally:
            self._ignore_modified = False

    def _draft(self) -> str:
        return self._text.get("1.0", "end-1c")

    def _document(self) -> PresetDocument:
        runtime = self._runtime_cwd()
        return PresetDocument.parse(self._draft(), runtime_cwd=runtime,
                                    catalog=self._catalog,
                                    registry=self._models.list())

    def _runtime_cwd(self) -> Path | None:
        runtimes = self.ctx.services.get("runtimes")
        if runtimes:
            exe = runtimes.get_executable()
            return exe.parent if exe else None
        return None

    def _on_modified(self, _event=None) -> None:
        if self._ignore_modified:
            return
        self._text.edit_modified(False)
        if self._validate_id is not None:
            self.after_cancel(self._validate_id)
        self._validate_id = self.after(280, self._validate_now)
        self._update_state()

    def _validate_now(self) -> None:
        self._validate_id = None
        self._show_diagnostics(self._document())
        self._update_state()

    def _validate(self) -> None:
        """Toolbar callback kept separate from the debounced validator."""
        self._validate_now()

    def _show_diagnostics(self, document: PresetDocument) -> None:
        self._diag.delete(0, "end")
        for diagnostic in document.diagnostics:
            prefix = diagnostic.severity.upper()
            self._diag.insert("end", f"{prefix}  {diagnostic.message}")
        if not document.diagnostics:
            self._diag.insert("end", t("No validation issues"))

    def _update_cursor_status(self, _event=None) -> None:
        try:
            line, col = self._text.index("insert").split(".")
            self._status.configure(text=f"Ln {line}, Col {int(col) + 1}  ·  " + self._status.cget("text").split("  ·  ")[-1])
        except (tk.TclError, ValueError):
            pass

    def _update_state(self) -> None:
        if self._base_snapshot is None:
            return
        draft = self._draft()
        dirty = normalize_editor_text(draft) != self._base_view
        doc = self._document()
        self._buttons["save"].set_enabled(bool(dirty and doc.can_save and not self._conflict))
        if self._conflict:
            state = t("External change detected — reload or overwrite explicitly")
        elif dirty:
            state = t("Unsaved")
        elif not self._base_snapshot.readable:
            state = t("Unreadable")
        else:
            state = t("Saved")
        text = (f"{state}  ·  {self._base_snapshot.path}  ·  "
                f"{len(doc.usable_routes)} {t('routes')} / "
                f"{doc.unique_sources} {t('models')}  ·  "
                f"{len(doc.errors)} {t('errors')} / "
                f"{len(doc.warnings)} {t('warnings')}")
        catalog = self._catalog
        if catalog is not None:
            catalog_snapshot = catalog.snapshot
            text += f"  ·  catalog: {catalog_snapshot.source}"
            if catalog_snapshot.version:
                text += f" {catalog_snapshot.version}"
        self._status.configure(text=text)
        server = self.ctx.services.get("server")
        # Applying always reloads the saved file.  Keep the action hidden while
        # the editor contains a draft so the user cannot mistake an explicit
        # server reload for applying text that has not been saved yet.
        if (server and server.is_running() and not dirty
                and self._base_snapshot.fingerprint != server.applied_preset_fingerprint):
            self._apply_btn.pack(side="left", padx=(0, 8), pady=(10, 0))
        else:
            self._apply_btn.pack_forget()

    def _on_preset_changed(self, data) -> None:
        if not self._visible:
            return
        if (data or {}).get("origin") == "external":
            dirty = normalize_editor_text(self._draft()) != self._base_view
            if dirty:
                self._conflict = True
                self._update_state()
            else:
                self._load_saved()

    def _on_runtime_activated(self, _data=None) -> None:
        # Runtime selection changes diagnostics/catalog context but must not
        # erase text the user is currently editing.
        if self.has_unsaved_changes():
            self._validate_now()
        else:
            self._load_saved()

    def on_show(self) -> None:
        self._visible = True
        self._preset.poll_external(force=True)
        self._update_state()

    def on_hide(self) -> None:
        self._visible = False

    def _save(self):
        if self._base_snapshot is None:
            return
        snap = None
        try:
            snap = self._preset.save(
                self._draft(), self._base_snapshot.fingerprint,
                base_text=self._base_view, overwrite_external=False)
        except Exception as exc:
            if "changed outside" in str(exc):
                self._conflict = True
                self._update_state()
                if messagebox.askyesno(
                        t("External change"),
                        t("The file changed outside this editor. Overwrite the external version with your draft?"),
                        parent=self):
                    try:
                        snap = self._preset.save(
                            self._draft(), self._base_snapshot.fingerprint,
                            base_text=self._base_view,
                            overwrite_external=True)
                    except Exception as overwrite_exc:
                        messagebox.showerror(t("Save failed"),
                                             str(overwrite_exc), parent=self)
                        return
                else:
                    return
            elif isinstance(exc, PresetValidationError):
                self._show_diagnostics(exc.document)
                messagebox.showerror(t("Invalid preset"), str(exc), parent=self)
            else:
                messagebox.showerror(t("Save failed"), str(exc), parent=self)
            if snap is None:
                return
        if snap is None:
            return
        self._base_snapshot = snap
        self._base_view = snap.text
        self._conflict = False
        self._replace_text(snap.text)
        self._show_diagnostics(snap.document)
        self._update_state()

    def _reload(self):
        dirty = normalize_editor_text(self._draft()) != self._base_view
        if dirty and not messagebox.askyesno(
                t("Reload"), t("Discard unsaved changes and reload from disk?"), parent=self):
            return
        self._load_saved()

    def _add_model(self):
        self._model_picker()

    def _model_picker(self):
        c = self.c
        win = tk.Toplevel(self)
        win.title(t("Add model"))
        win.transient(self.winfo_toplevel())
        win.grab_set()
        win.geometry("760x480")
        search = ttk.Entry(win, font=theme.mono(10))
        search.pack(fill="x", padx=12, pady=12)
        tree = ttk.Treeview(win, columns=("state", "name", "path", "meta"),
                            show="headings", selectmode="browse")
        for key, title, width in (("state", "State", 100), ("name", "Name", 190),
                                  ("path", "Path", 340), ("meta", "GGUF", 160)):
            tree.heading(key, text=t(title))
            tree.column(key, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=12)
        footer = tk.Frame(win, bg=c["surface"])
        footer.pack(fill="x", padx=12, pady=12)
        info = tk.Label(footer, text="", bg=c["surface"], fg=c["muted"],
                        font=theme.ui(8), anchor="w")
        info.pack(side="left", fill="x", expand=True)
        def refresh(_event=None):
            q = search.get().casefold().strip()
            tree.delete(*tree.get_children())
            doc = self._document()
            for i, model in enumerate(self._models.list()):
                meta = model.meta or {}
                hay = " ".join([model.name, model.path,
                                str(meta.get("arch", "")),
                                str(meta.get("quant", "")),
                                str(meta.get("params", "")),
                                str(model.size)]).casefold()
                if q and q not in hay:
                    continue
                normalized = normalize_model_path(model.path, base=self._runtime_cwd())
                matches = [r for r in doc.routes if r.normalized_source == normalized]
                state = (f"{len(matches)} {t('route(s)')}" if matches else
                         (t("Missing") if getattr(model.state, "value", model.state) == "missing"
                          else t("Not in preset")))
                meta_values = [str(meta.get(k, ""))
                               for k in ("arch", "quant", "params")
                               if meta.get(k)]
                if model.size:
                    meta_values.append(fmt_bytes(model.size))
                meta_text = " · ".join(meta_values)
                tree.insert("", "end", iid=str(i), values=(state, model.name,
                             model.path, meta_text))
            info.configure(text=t("Select a model to add or jump to its routes"))

        def choose(_event=None):
            sel = tree.selection()
            if not sel:
                return
            model = self._models.list()[int(sel[0])]
            doc = self._document()
            normalized = normalize_model_path(model.path, base=self._runtime_cwd())
            matches = [r for r in doc.routes if r.normalized_source == normalized]
            if matches:
                add_another = messagebox.askyesno(
                    t("Model already present"),
                    t("This model already has route(s). Add another route explicitly?"),
                    parent=win)
                if not add_another:
                    if len(matches) > 1:
                        section = simpledialog.askstring(
                            t("Jump to route"),
                            t("Routes") + ": " + ", ".join(r.section for r in matches),
                            initialvalue=matches[0].section, parent=win)
                        target = next((r for r in matches if r.section == section), matches[0])
                    else:
                        target = matches[0]
                    self._text.mark_set("insert", f"1.0+{target.start}c")
                    self._text.see(f"1.0+{target.start}c")
                    win.destroy()
                    return
            if getattr(model.state, "value", model.state) == "missing":
                messagebox.showwarning(t("Model missing"), model.path, parent=win)
                return
            base = sanitise(Path(model.name or model.path).stem) or "model"
            existing = {s.name.casefold() for s in doc.sections}
            name = base
            n = 2
            while name.casefold() in existing:
                suffix = f"_{n}"
                name = base[:max(1, 24 - len(suffix))] + suffix
                n += 1
            if not messagebox.askyesno(
                    t("Add model"), f"[{name}]\nmodel = {model.path}\n\n" + t("Add this route?"),
                    parent=win):
                return
            companions: dict[str, str] = {}
            # Companion files are deliberately suggestions, never implicit
            # routes.  The user must opt in for each detected MMProj/draft.
            mmproj = self._models.detect_mmproj(model.id)
            if mmproj.get("path") and messagebox.askyesno(
                    t("MMProj companion"),
                    t("A single MMProj companion was detected. Add it to this route?"),
                    parent=win):
                companions["mmproj"] = mmproj["path"]
            draft = self._models.detect_draft(model.id)
            if draft.get("path") and messagebox.askyesno(
                    t("Draft companion"),
                    t("A single draft companion was detected. Add it to this route?"),
                    parent=win):
                companions["model-draft"] = draft["path"]
            edit = doc.add_model(name, str(Path(model.path).resolve(strict=False)),
                                 companions)
            self._apply_text_edit(edit)
            win.destroy()

        def remove_selected(_event=None):
            sel = tree.selection()
            if not sel:
                return
            model = self._models.list()[int(sel[0])]
            doc = self._document()
            matches = [r for r in doc.routes
                       if r.normalized_source == normalize_model_path(
                           model.path, base=self._runtime_cwd())]
            if not matches:
                messagebox.showinfo(t("Remove route"),
                                    t("This model is not present in the preset."),
                                    parent=win)
                return
            target = matches[0]
            if len(matches) > 1:
                chosen = simpledialog.askstring(
                    t("Remove route"),
                    t("Choose one section to remove") + ": " +
                    ", ".join(r.section for r in matches),
                    initialvalue=target.section, parent=win)
                target = next((r for r in matches if r.section == chosen), None)
                if target is None:
                    return
            if not messagebox.askyesno(
                    t("Remove route"),
                    t("Remove section [{section}] from the preset?",
                      section=target.section), parent=win):
                return
            edits = doc.remove_sections([target.section])
            if edits:
                self._apply_text_edits(edits)
            win.destroy()

        search.bind("<KeyRelease>", refresh)
        tree.bind("<Double-Button-1>", choose)
        ttk.Button(footer, text=t("Remove route"), command=remove_selected).pack(side="right")
        ttk.Button(footer, text=t("Add / Jump"), command=choose).pack(side="right", padx=(0, 8))
        ttk.Button(footer, text=t("Cancel"), command=win.destroy).pack(side="right", padx=(0, 8))
        search.focus_set()
        refresh()

    def _add_param(self):
        catalog = self._catalog
        if catalog is None:
            return
        win = tk.Toplevel(self)
        win.title(t("Add parameter"))
        win.transient(self.winfo_toplevel())
        win.grab_set()
        win.geometry("720x450")
        search = ttk.Entry(win, font=theme.mono(10))
        search.pack(fill="x", padx=12, pady=12)
        tree = ttk.Treeview(win, columns=("name", "type", "default", "source", "description"),
                            show="headings", selectmode="browse")
        for key, title, width in (("name", "Name", 150), ("type", "Type", 90),
                                  ("default", "Default", 90), ("source", "Compatibility", 110),
                                  ("description", "Description", 260)):
            tree.heading(key, text=t(title)); tree.column(key, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=12)
        specs: list = []

        def refresh(_event=None):
            nonlocal specs
            specs = catalog.search(search.get())
            tree.delete(*tree.get_children())
            for i, spec in enumerate(specs):
                tree.insert("", "end", iid=str(i), values=(
                    spec.name, spec.value_type, "" if spec.default is None else spec.default,
                    spec.source, spec.description))

        def choose(_event=None):
            sel = tree.selection()
            if not sel:
                return
            spec = specs[int(sel[0])]
            offset = int(self._text.count("1.0", "insert", "chars")[0])
            doc = self._document()
            section = doc.section_at_offset(offset)
            if section is None or section.name == "":
                messagebox.showinfo(t("Add parameter"),
                                    t("Place the cursor inside [*] or a model section."), parent=win)
                return
            if spec.router_controlled:
                messagebox.showinfo(t("Managed parameter"),
                                    spec.blocked_reason or t("Controlled by Settings"), parent=win)
                return
            scope = "global" if section.name == "*" else "model"
            if scope not in spec.scopes:
                messagebox.showinfo(
                    t("Parameter scope"),
                    t("This parameter is not available in the current section scope."),
                    parent=win)
                return
            value = spec.default
            if spec.allowed_values:
                value = spec.allowed_values[0]
            if value is None:
                value = "true" if spec.value_type == "boolean" else ""
            value = simpledialog.askstring(t("Parameter value"), spec.name,
                                           initialvalue=str(value), parent=win)
            if value is None:
                return
            edit = doc.add_parameter(section.name, spec.name, value)
            if edit is None:
                existing = next((e for e in doc.entries
                                 if e.section == section.name and
                                 (catalog.canonical_name(e.key) or e.key) == spec.name), None)
                if existing:
                    self._text.mark_set("insert", f"1.0+{existing.value_start}c")
                    self._text.see(f"1.0+{existing.value_start}c")
                win.destroy()
                return
            self._apply_text_edit(edit)
            # Leave the insertion point inside the value so the proposed
            # default/placeholder can be replaced immediately.
            key_offset = edit.replacement.find(spec.name)
            value_offset = (edit.start + max(0, key_offset)
                            + len(spec.name) + 3)  # ``key = ``
            self._text.mark_set("insert", f"1.0+{value_offset}c")
            self._text.see("insert")
            win.destroy()

        search.bind("<KeyRelease>", refresh)
        tree.bind("<Double-Button-1>", choose)
        footer = tk.Frame(win, bg=self.c["bg"]); footer.pack(fill="x", padx=12, pady=12)
        info = tk.Label(footer, text="", bg=self.c["bg"], fg=self.c["muted"],
                        anchor="w", justify="left", font=theme.mono(8))
        info.pack(side="left", fill="x", expand=True)
        ttk.Button(footer, text=t("Insert"), command=choose).pack(side="right")
        ttk.Button(footer, text=t("Cancel"), command=win.destroy).pack(side="right", padx=(0, 8))

        def show_details(_event=None):
            selection = tree.selection()
            if not selection:
                info.configure(text="")
                return
            spec = specs[int(selection[0])]
            aliases = ", ".join(spec.aliases + spec.negated_aliases) or "—"
            allowed = ", ".join(spec.allowed_values) or "—"
            scopes = ", ".join(spec.scopes) or "—"
            info.configure(text=(
                f"{spec.description}  ·  {t('Aliases')}: {aliases}  ·  "
                f"{t('Allowed')}: {allowed}  ·  {t('Scope')}: {scopes}  ·  "
                f"{t('Source')}: {spec.source}"))

        tree.bind("<<TreeviewSelect>>", show_details, add="+")
        search.focus_set(); refresh()

    def _apply_text_edit(self, edit: TextEdit):
        self._apply_text_edits([edit])
        self._text.mark_set("insert", f"1.0+{edit.start + len(edit.replacement)}c")
        self._text.see("insert")

    def _apply_text_edits(self, edits: list[TextEdit]):
        current = self._draft()
        updated = apply_edits(current, edits)
        self._replace_text(updated)
        self._validate_now()

    def _scan(self):
        # GGUF header reads can be expensive; keep Tk responsive and let the
        # existing ``models_scanned`` event trigger validation when complete.
        def work() -> None:
            try:
                self._models.scan()
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror(
                    t("Scan failed"), str(exc), parent=self))
        threading.Thread(target=work, daemon=True, name="model-scan").start()

    def _add_folder(self):
        folder = filedialog.askdirectory(parent=self, title=t("Add model folder"))
        if folder:
            self._models.add_folder(folder)

    def _restore_backup(self):
        if not messagebox.askyesno(t("Restore backup"),
                                    t("Replace the current preset with its last backup?"),
                                    parent=self):
            return
        try:
            self._preset.restore_backup()
            self._load_saved()
        except Exception as exc:
            messagebox.showerror(t("Restore failed"), str(exc), parent=self)

    def _open_location(self):
        # Opening a file manager is platform-specific and optional; reveal the
        # path in a copy-friendly dialog instead of shelling out from Tk.
        messagebox.showinfo(t("Preset path"), str(self._preset.path), parent=self)

    def _apply_server(self):
        server = self.ctx.services.get("server")
        if not server:
            return
        if not messagebox.askyesno(
                t("Apply to server"),
                t("Reloading may unload or restart changed models. Continue?"),
                parent=self):
            return
        result = server.reload_models()
        if not result.get("ok"):
            messagebox.showerror(t("Apply failed"), result.get("error", result.get("reason", "")), parent=self)
        self._update_state()

    def _jump_diagnostic(self, _event=None):
        index = self._diag.curselection()
        if not index:
            return
        document = self._document()
        if index[0] >= len(document.diagnostics):
            return
        diagnostic = document.diagnostics[index[0]]
        self._text.mark_set("insert", f"1.0+{diagnostic.start}c")
        self._text.see("insert")

    def _serialize(self) -> dict:
        return {
            "text": self._draft(),
            "base_view": self._base_view,
            "fingerprint": self._base_snapshot.fingerprint if self._base_snapshot else "",
            "conflict": self._conflict,
            "insert": self._text.index("insert"),
            "top": self._text.index("@0,0"),
        }

    def _restore(self, state: dict) -> None:
        if not state:
            return
        self._replace_text(state.get("text", ""))
        self._base_view = state.get("base_view", self._base_view)
        self._conflict = bool(state.get("conflict", False))
        try:
            self._text.mark_set("insert", state.get("insert", "1.0"))
            self._text.see(state.get("top", "1.0"))
        except tk.TclError:
            pass
        self._validate_now()

    def has_unsaved_changes(self) -> bool:
        return self._base_snapshot is not None and normalize_editor_text(self._draft()) != self._base_view
