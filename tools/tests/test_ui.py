"""Small structural UI regressions for keyboard, layout, and teardown."""
from __future__ import annotations

import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from llama_router.core.events import EventBus
from llama_router.core.logs import LogService
from llama_router.core.paths import PathManager
from llama_router.core.storage import init_db
from llama_router.i18n import set_language
from llama_router.services.config_manager import ConfigManager
from llama_router.services.download_manager import DownloadManager
from llama_router.services.models_manager import ModelsManager
from llama_router.services.playground import PlaygroundService
from llama_router.services.profile_manager import ProfileManager
from llama_router.services.runtime_manager import RuntimeManager
from llama_router.services.server_manager import ServerManager
from llama_router.schemas import ModelEntry
from llama_router.ui import theme
from llama_router.ui.app import App, AppContext, PAGES
from llama_router.ui.pages.settings import SettingsPage
from llama_router.ui.widgets import NavItem, PageHeader, PillButton, ScrollFrame


class TestPrewarm(unittest.TestCase):
    def test_prewarm_continues_from_a_non_dashboard_page(self) -> None:
        scheduled = []

        class Root:
            def after(self, delay, callback):
                scheduled.append((delay, callback))
                return "after#1"

            def after_cancel(self, _aid):
                pass

        app = App.__new__(App)
        app.root = Root()
        app._active = "settings"
        app._pages = {"settings": object()}
        app._prewarm_id = None
        app._prewarm_queue = []
        app._closing = False
        app._rebuilding = False
        app._page_construct_ms = {}

        app._schedule_prewarm(321)
        self.assertEqual(
            app._prewarm_queue, ["dashboard", "playground", "runtime"])
        self.assertEqual(scheduled[0][0], 321)

        built = []
        app._prewarm_queue = ["dashboard"]
        app._create_page = lambda key: built.append(key)
        app._prewarm_next()
        self.assertEqual(built, ["dashboard"])


class _TkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            root = tk.Tk()
            root.withdraw()
            root.destroy()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}") from exc


class TestWidgetContracts(_TkTest):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.colors = theme.apply(self.root, "midnight")

    def tearDown(self) -> None:
        self.root.destroy()

    def test_nav_header_and_global_binding_lifecycle(self) -> None:
        called = []
        item = NavItem(self.root, self.colors, "Models",
                       command=lambda: called.append(True))
        self.assertTrue(item._keyboard_nav)
        self.assertNotIn(str(item.cget("takefocus")).lower(), ("", "0", "false"))
        self.assertTrue(item.bind("<Key-space>"))
        item._activate(object())
        self.assertEqual(called, [True])

        header = PageHeader(self.root, self.colors, "area", "Title")
        PillButton(header.actions, self.colors, "Action", command=lambda: None).pack()
        header._on_resize(SimpleNamespace(width=700))
        self.assertEqual(int(header.actions.grid_info()["row"]), 1)
        header._on_resize(SimpleNamespace(width=1000))
        self.assertEqual(int(header.actions.grid_info()["row"]), 0)

        scroll = ScrollFrame(self.root, self.colors)
        owned = list(scroll._global_bindings)
        for sequence, funcid in owned:
            self.assertIn(funcid, self.root.bind_all(sequence))
        scroll.destroy()
        self.root.update_idletasks()
        for sequence, funcid in owned:
            self.assertNotIn(funcid, self.root.bind_all(sequence) or "")
            self.assertNotIn(funcid, self.root._tclCommands or ())

    def test_scrollbar_visibility_does_not_resize_content(self) -> None:
        self.root.geometry("400x200+0+0")
        scroll = ScrollFrame(self.root, self.colors)
        scroll.pack(fill="both", expand=True)
        short = tk.Frame(scroll.body, bg=self.colors["bg"], height=20)
        short.pack(fill="x")
        self.root.update()
        width = scroll._canvas.winfo_width()
        self.assertFalse(scroll._vbar.winfo_ismapped())

        tall = tk.Frame(scroll.body, bg=self.colors["bg"], height=600)
        tall.pack(fill="x")
        self.root.update()
        self.assertTrue(scroll._vbar.winfo_ismapped())
        self.assertEqual(scroll._canvas.winfo_width(), width)

        tall.destroy()
        self.root.update()
        self.assertFalse(scroll._vbar.winfo_ismapped())
        self.assertEqual(scroll._canvas.winfo_width(), width)


class TestAppLayout(_TkTest):
    @staticmethod
    def _build_app(base: Path, geometry: str | None = None) -> tuple[App, LogService]:
        paths = PathManager(base)
        paths.ensure_dirs()
        init_db(paths.db_path)
        events = EventBus()
        logs = LogService(events)

        services: dict = {}
        services["config"] = config = ConfigManager(paths, events)
        config.load()
        config.update({"auto_check_releases": False, "language": "en"})
        set_language("en")
        services["models"] = models = ModelsManager(paths, config, events)
        models.load()
        services["profiles"] = profiles = ProfileManager(paths, models, events)
        profiles.load()
        services["downloads"] = downloads = DownloadManager(paths, config, events)
        services["runtimes"] = runtimes = RuntimeManager(
            paths, config, downloads, events)
        runtimes.load()
        downloads.load()
        services["server"] = server = ServerManager(
            config, runtimes, models, profiles, events, paths, logs)
        services["playground"] = PlaygroundService(
            config, server, profiles, events, paths)
        ctx = AppContext(paths=paths, events=events, logs=logs,
                         colors={}, services=services, enable_tray=False,
                         initial_geometry=geometry)
        return App(ctx), logs

    def test_page_viewports_focus_and_theme_teardown(self) -> None:
        errors = []
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            app.root.report_callback_exception = lambda *error: errors.append(error)
            try:
                app.root.deiconify()
                app.root.focus_force()
                for index in range(1, len(PAGES) + 1):
                    self.assertTrue(app.root.bind(f"<Control-Key-{index}>"))

                for width, height in ((760, 500), (960, 640), (1280, 860)):
                    app.root.geometry(f"{width}x{height}+0+0")
                    app.root.update_idletasks()
                    for key in PAGES:
                        app.show_page(key)
                        app._cancel_prewarm()
                        app.root.update_idletasks()
                        app.root.update()
                        page = app._pages[key]
                        header = next(child for child in page.winfo_children()
                                      if isinstance(child, PageHeader))
                        header_left = header.winfo_rootx()
                        header_right = header_left + header.winfo_width()
                        actions_left = header.actions.winfo_rootx()
                        actions_right = actions_left + header.actions.winfo_width()
                        self.assertGreaterEqual(actions_left, header_left)
                        self.assertLessEqual(actions_right, header_right + 1)

                        if key == "settings":
                            page._layout_server_grid()
                            page._layout_api_actions()
                            self.assertEqual(page._server_compact, width == 760)
                            if width == 760:
                                self.assertTrue(page._api_actions_compact)
                        elif key == "profiles":
                            self.assertNotIsInstance(page._editor_body, ScrollFrame)

                        app._focus_first_action(key)
                        app.root.update_idletasks()
                        focused = app.root.focus_get()
                        self.assertIsNotNone(focused, (width, height, key))
                        self.assertTrue(app._is_visible_in_viewport(focused),
                                        (width, height, key, focused))

                app.show_page("playground")
                app._cancel_prewarm()
                playground = app._pages["playground"]
                session = playground._svc.new_session(name="Keyboard session")
                playground._svc.save_session(session)
                playground._toggle_sidebar()
                app.root.update_idletasks()
                session_row = playground._sess_list.winfo_children()[0]
                self.assertTrue(session_row._keyboard_nav)
                self.assertTrue(session_row.bind("<Shift-F10>"))
                self.assertTrue(playground._text.bind("<Shift-F10>"))
                self.assertTrue(playground._text._focus_when_disabled)
                self.assertNotIn(
                    str(playground._text.cget("takefocus")).lower(),
                    ("", "0", "false"))

                app.apply_theme("carbon")
                app._cancel_prewarm()
                app.root.update_idletasks()
                first_count = len((app.root.bind_all("<MouseWheel>") or "").splitlines())
                app.apply_theme("midnight")
                app._cancel_prewarm()
                app.root.update_idletasks()
                second_count = len((app.root.bind_all("<MouseWheel>") or "").splitlines())
                self.assertEqual(first_count, second_count)

                app._after_idle(app._scroll_to_nav, "playground")
                app._on_close()
                self.assertFalse(app._idle_ids)
                self.assertIsNone(app._pump_id)
                self.assertFalse(errors)
            finally:
                try:
                    if app.root.winfo_exists():
                        app._on_close()
                except tk.TclError:
                    pass
                logs.close()

    def test_profile_resets_restore_builtin_template_values(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            app._cancel_prewarm()
            models = app.ctx.services["models"]
            profiles = app.ctx.services["profiles"]
            models._models["m1"] = ModelEntry(
                id="m1", name="Model One", path="model.gguf")
            profile = profiles.create("m1", "Agent", {
                "n-gpu-layers": 7,
                "jinja": "false",
                "reasoning": "off",
                "chat-template-kwargs": "{}",
                "custom-flag": "kept",
            })
            try:
                app.show_page("profiles")
                app._cancel_prewarm()
                app.root.update()
                page = app._pages["profiles"]
                iid = f"p:{profile.id}"
                page._tree.selection_set(iid)
                page._tree.focus(iid)
                page._on_select()
                app.root.update()
                self.assertFalse(page._advanced_built)

                page._reset_params({
                    "jinja", "reasoning", "chat-template-kwargs"})
                app.root.update()
                self.assertEqual(profiles.get(profile.id).params, {
                    "n-gpu-layers": 7,
                    "jinja": "true",
                    "reasoning": "on",
                    "chat-template-kwargs":
                        '{"preserve_thinking": true}',
                    "custom-flag": "kept",
                })

                page._reset_params()
                app.root.update()
                self.assertEqual(
                    profiles.get(profile.id).params,
                    profiles.template_params("Agent"))

                page._toggle_params()
                app.root.update()
                self.assertTrue(page._advanced_built)
                self.assertEqual(page._collect_params(),
                                 profiles.template_params("Agent"))
            finally:
                try:
                    if app.root.winfo_exists():
                        app._on_close()
                except tk.TclError:
                    pass
                logs.close()


class TestNavigationRanking(unittest.TestCase):
    def test_nearest_section_wins_before_alignment(self) -> None:
        app = object.__new__(App)
        focused, near, aligned_far = object(), object(), object()
        app.root = SimpleNamespace(focus_get=lambda: focused)
        app._focus_widgets = lambda: [focused, near, aligned_far]
        rects = {
            focused: (0, 0, 20, 20),
            near: (30, 30, 50, 50),
            aligned_far: (0, 200, 20, 220),
        }
        app._widget_rect = rects.__getitem__
        selected = []
        app._focus_widget = selected.append

        self.assertEqual(App._focus_direction(app, 0, 1), "break")
        self.assertEqual(selected, [near])

    def test_horizontal_arrow_does_not_move_to_a_control_below(self) -> None:
        app = object.__new__(App)
        focused, below = object(), object()
        app.root = SimpleNamespace(focus_get=lambda: focused)
        app._focus_widgets = lambda: [focused, below]
        rects = {
            focused: (20, 20, 220, 120),
            below: (20, 130, 80, 150),
        }
        app._widget_rect = rects.__getitem__
        selected = []
        app._focus_widget = selected.append

        self.assertEqual(App._focus_direction(app, -1, 0), "break")
        self.assertEqual(selected, [])

    def test_status_ellipsis_keeps_label(self) -> None:
        font = SimpleNamespace(measure=len)
        self.assertEqual(App._ellipsize_status_part("model · abcdef", 11, font),
                         "model · ab…")


if __name__ == "__main__":
    unittest.main()
