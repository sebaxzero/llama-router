"""Small structural UI regressions for keyboard, layout, and teardown."""
from __future__ import annotations

import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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

    def test_profiles_refresh_snapshots_once_and_hides_disabled_models(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            app._cancel_prewarm()
            models = app.ctx.services["models"]
            profiles = app.ctx.services["profiles"]
            models._models["enabled"] = ModelEntry(
                id="enabled", name="Enabled model", path="enabled.gguf")
            models._models["disabled"] = ModelEntry(
                id="disabled", name="Disabled model", path="disabled.gguf",
                enabled=False)
            enabled_profile = profiles.create(
                "enabled", "Default", {"n-gpu-layers": -1})
            profiles.create("disabled", "Hidden", {})
            try:
                app.show_page("profiles")
                app._cancel_prewarm()
                app.root.update_idletasks()
                page = app._pages["profiles"]
                with mock.patch.object(models, "list", wraps=models.list) as list_call, \
                        mock.patch.object(profiles, "by_model",
                                           wraps=profiles.by_model) as grouped_call, \
                        mock.patch.object(profiles, "ensure_defaults",
                                           wraps=profiles.ensure_defaults) as defaults_call:
                    page._refresh_tree()
                self.assertEqual(list_call.call_count, 1)
                self.assertEqual(grouped_call.call_count, 1)
                self.assertEqual(defaults_call.call_count, 0)
                roots = page._tree.get_children()
                self.assertIn("m:enabled", roots)
                self.assertNotIn("m:disabled", roots)
                self.assertTrue(page._tree.exists(f"p:{enabled_profile.id}"))
            finally:
                try:
                    if app.root.winfo_exists():
                        app._on_close()
                except tk.TclError:
                    pass
                logs.close()

    def test_profiles_defers_preset_until_open_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            app._cancel_prewarm()
            try:
                app.show_page("profiles")
                app._cancel_prewarm()
                page = app._pages["profiles"]
                self.assertIsNone(page._preset_view)

                page._preset_card.set_open(True)
                app.root.update_idletasks()
                preset = page._preset_view
                self.assertIsNotNone(preset)
                old_text = preset._text.get("1.0", "end-1c")

                page._preset_card.set_open(False)
                app.root.update_idletasks()
                new_text = "[*]\nmodel = fresh.gguf\n"
                app.ctx.paths.preset_ini.write_text(new_text, encoding="utf-8")
                preset._on_external_change()
                self.assertTrue(preset._external_dirty)
                self.assertEqual(preset._text.get("1.0", "end-1c"), old_text)

                page._preset_card.set_open(True)
                app.root.update_idletasks()
                self.assertFalse(preset._external_dirty)
                self.assertEqual(preset._text.get("1.0", "end-1c"), new_text)
            finally:
                try:
                    if app.root.winfo_exists():
                        app._on_close()
                except tk.TclError:
                    pass
                logs.close()

    def test_profiles_restores_an_open_preset_card(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            app.ctx.collapsible_states["preset-editor"] = True
            try:
                app.show_page("profiles")
                app._cancel_prewarm()
                app.root.update_idletasks()
                page = app._pages["profiles"]
                self.assertTrue(page._preset_card.is_open)
                self.assertIsNotNone(page._preset_view)
                self.assertTrue(page._preset_view.winfo_ismapped())
            finally:
                if app.root.winfo_exists():
                    app._on_close()
                logs.close()

    def test_profile_model_toggle_reads_live_profiles_after_autosave(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            models = app.ctx.services["models"]
            profiles = app.ctx.services["profiles"]
            models._models["live"] = ModelEntry(
                id="live", name="Live model", path="live.gguf")
            profile = profiles.create("live", "Default", {})
            try:
                app.show_page("profiles")
                app._cancel_prewarm()
                page = app._pages["profiles"]
                profiles.update(profile.id, {"active": True})
                self.assertFalse(page._profiles_snapshot["live"][0].active)
                with mock.patch.object(page._tree, "identify_column",
                                       return_value="#1"), \
                        mock.patch.object(page._tree, "identify_row",
                                           return_value="m:live"):
                    page._on_tree_click(SimpleNamespace(x=0, y=0))
                self.assertFalse(profiles.get(profile.id).active)
            finally:
                if app.root.winfo_exists():
                    app._on_close()
                logs.close()

    def test_dashboard_copy_reads_current_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            config = app.ctx.services["config"]
            try:
                page = app._pages["dashboard"]
                config.update({"server": {"api_key": "old-secret"}})
                page._render_endpoints()
                config.update({"server": {"api_key": "new-secret"}})
                page._copy_api_key()
                self.assertEqual(app.root.clipboard_get(), "new-secret")
            finally:
                if app.root.winfo_exists():
                    app._on_close()
                logs.close()

    def test_status_context_groups_profiles_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            profiles = app.ctx.services["profiles"]
            try:
                with mock.patch.object(profiles, "by_model",
                                       wraps=profiles.by_model) as grouped:
                    app._update_status_context()
                self.assertEqual(grouped.call_count, 1)
            finally:
                if app.root.winfo_exists():
                    app._on_close()
                logs.close()

    def test_runtime_assets_do_not_render_unknown_size(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            try:
                app.show_page("runtime")
                app._cancel_prewarm()
                page = app._pages["runtime"]
                page._releases = [{
                    "tag": "b1", "published": "2026-01-01",
                    "assets": [{"name": "llama.zip", "size": 0,
                                 "url": "https://example.invalid/llama.zip"}],
                }]
                page._release_cb.configure(values=["b1"])
                page._release_cb.current(0)
                page._show_assets()
                columns = page._assets.cget("columns")
                self.assertEqual(
                    columns if isinstance(columns, tuple)
                    else tuple(str(columns).split()), ("name",))
                self.assertEqual(page._assets.item("0", "values"),
                                 ("llama.zip",))
            finally:
                if app.root.winfo_exists():
                    app._on_close()
                logs.close()

    def test_dashboard_defers_closed_logs_until_open(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(LogService, "get", autospec=True,
                                   side_effect=LogService.get) as get:
                app, logs = self._build_app(Path(td))
                try:
                    page = app._pages["dashboard"]
                    self.assertFalse(page._logs_open)
                    self.assertIsNone(page._log_text)
                    self.assertEqual(get.call_count, 0)

                    logs.log("app", "info", "before opening")
                    app.ctx.events.drain()
                    self.assertEqual(page._visible_logs(), "")

                    page._set_logs_open(True)
                    self.assertIsNotNone(page._log_text)
                    self.assertEqual(get.call_count, 1)
                    self.assertIn("before opening", page._visible_logs())

                    page._set_logs_open(False)
                    previous = page._visible_logs()
                    logs.log("app", "info", "while closed")
                    app.ctx.events.drain()
                    self.assertEqual(page._visible_logs(), previous)

                    page._set_logs_open(True)
                    self.assertEqual(get.call_count, 2)
                    self.assertIn("while closed", page._visible_logs())
                finally:
                    if app.root.winfo_exists():
                        app._on_close()
                logs.close()

    def test_dashboard_defers_closed_optional_content(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            try:
                page = app._pages["dashboard"]
                page._ep.set_open(False)
                page._launch_card.set_open(False)
                page._endpoint_dirty = True
                page._cmd_dirty = True
                with mock.patch.object(page, "_render_endpoints") as endpoints, \
                        mock.patch.object(page, "_refresh_cmd") as command:
                    page._on_status({"status": "stopped"})
                endpoints.assert_not_called()
                command.assert_not_called()
                with mock.patch.object(page, "_render_endpoints") as endpoints, \
                        mock.patch.object(page, "_refresh_cmd") as command:
                    page._ep.set_open(True)
                    page._launch_card.set_open(True)
                endpoints.assert_called_once_with()
                command.assert_called_once_with()
            finally:
                if app.root.winfo_exists():
                    app._on_close()
                logs.close()

    def test_settings_defers_closed_server_form_and_serializes_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            app.ctx.collapsible_states["settings.server"] = False
            try:
                app.show_page("settings")
                app._cancel_prewarm()
                page = app._pages["settings"]
                self.assertFalse(page._server_card.is_open)
                self.assertFalse(page._server_built)
                self.assertEqual(page._serialize()["port"], "8080")
                page._server_card.set_open(True)
                self.assertTrue(page._server_built)
                self.assertEqual(page._port.get(), "8080")
            finally:
                if app.root.winfo_exists():
                    app._on_close()
                logs.close()

    def test_dashboard_skips_unchanged_endpoint_and_command_render(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            try:
                page = app._pages["dashboard"]
                page._endpoint_signature_rendered = None
                with mock.patch.object(page, "_base_url",
                                        wraps=page._base_url) as base_url:
                    page._render_endpoints()
                    first_calls = base_url.call_count
                    page._render_endpoints()
                    self.assertEqual(base_url.call_count, first_calls)

                page._last_cmd_preview = None
                with mock.patch.object(page, "_highlight_code",
                                        wraps=page._highlight_code) as highlight:
                    page._refresh_cmd()
                    first_calls = highlight.call_count
                    page._refresh_cmd()
                    self.assertEqual(highlight.call_count, first_calls)
            finally:
                if app.root.winfo_exists():
                    app._on_close()
                logs.close()

    def test_settings_coalesces_layout_and_builds_appearance_lazily(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            app, logs = self._build_app(Path(td))
            try:
                app.ctx.collapsible_states["settings.appearance"] = False
                app.show_page("settings")
                app._cancel_prewarm()
                app.root.update()
                page = app._pages["settings"]
                self.assertEqual(page._theme_btns, {})
                page._appearance_card.set_open(True)
                self.assertEqual(set(page._theme_btns), set(theme.theme_names()))

                page._cancel_layout()
                page._schedule_layout()
                first = page._layout_id
                page._schedule_layout()
                second = page._layout_id
                self.assertNotEqual(first, second)
                self.assertNotIn(first, page._after_ids)

                page._max_dl.delete(0, "end")
                page._max_dl.insert(0, "4")
                page._schedule_save(0)
                page.after_cancel(page._autosave_id)
                page._autosave_id = None
                with mock.patch.object(page, "_serialize",
                                        wraps=page._serialize) as serialize:
                    page._save()
                    self.assertEqual(serialize.call_count, 1)
            finally:
                if app.root.winfo_exists():
                    app._on_close()
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

    def test_navigation_idle_callback_ignores_stale_navigation(self) -> None:
        app = object.__new__(App)
        app._navigation_token = 2
        app._active = "settings"
        app._page_navigation_idle_ms = {}
        app._closing = False
        app._rebuilding = False

        app._record_navigation_idle("dashboard", time.perf_counter(), 1)
        self.assertEqual(app._page_navigation_idle_ms, {})
        app._record_navigation_idle("settings", time.perf_counter(), 2)
        self.assertIn("settings", app._page_navigation_idle_ms)


class TestBenchmarkHarness(unittest.TestCase):
    def test_startup_clock_starts_before_app_build(self) -> None:
        from tools import benchmark_ui

        order = []

        class FakeApp:
            def _cancel_prewarm(self):
                order.append("cancel")

        def clock():
            order.append("clock")
            return len(order)

        def build(_base, geometry=None):
            order.append("build")
            return FakeApp(), object()

        with mock.patch.object(benchmark_ui.time, "perf_counter",
                               side_effect=clock), \
                mock.patch.object(benchmark_ui.TestAppLayout, "_build_app",
                                   side_effect=build), \
                mock.patch.object(benchmark_ui, "_settle"):
            benchmark_ui._build_and_settle(Path("."), "960x640")
        self.assertEqual(order[:2], ["clock", "build"])

    def test_dashboard_first_load_uses_startup_without_navigation(self) -> None:
        from tools import benchmark_ui

        class FakeApp:
            _active = "dashboard"
            _pages = {"dashboard": object()}
            _page_construct_ms = {}
            _page_on_show_ms = {}
            _page_navigation_idle_ms = {}

            def show_page(self, _page):
                raise AssertionError("dashboard first load navigated")

        result = benchmark_ui._first_measurement(
            FakeApp(), "dashboard", {"startup_settled_ms": 12.0})
        self.assertEqual(result["startup_settled_ms"], 12.0)
        self.assertNotIn("show_call_ms", result)

    def test_non_dashboard_first_load_starts_before_target_exists(self) -> None:
        from tools import benchmark_ui

        class Root:
            def update_idletasks(self):
                pass

            def update(self):
                pass

        class FakeApp:
            root = Root()
            _active = "dashboard"
            _pages = {"dashboard": object()}
            _page_construct_ms = {"settings": 1.0}
            _page_on_show_ms = {"settings": 2.0}
            _page_navigation_idle_ms = {"settings": 3.0}

            def show_page(self, page):
                self.assert_not_present = page not in self._pages
                self._pages[page] = object()
                self._active = page

            def _cancel_prewarm(self):
                pass

        app = FakeApp()
        benchmark_ui._first_measurement(
            app, "settings", {"startup_settled_ms": 12.0})
        self.assertTrue(app.assert_not_present)

    def test_repeated_navigation_always_returns_through_other_page(self) -> None:
        from tools import benchmark_ui

        class Root:
            def update_idletasks(self):
                pass

            def update(self):
                pass

        class FakeApp:
            root = Root()
            _active = "settings"
            _pages = {"dashboard": object(), "settings": object()}
            _page_construct_ms = {}
            _page_on_show_ms = {}
            _page_navigation_idle_ms = {}

            def __init__(self):
                self.calls = []

            def show_page(self, page):
                self.calls.append(page)
                self._active = page

            def _cancel_prewarm(self):
                pass

        app = FakeApp()
        benchmark_ui._repeated_measurements(app, "settings", "dashboard", 2)
        self.assertEqual(app.calls,
                         ["dashboard", "settings", "dashboard", "settings",
                          "dashboard"])

    def test_settled_navigation_reports_two_tk_rounds(self) -> None:
        from tools import benchmark_ui

        class Root:
            def __init__(self):
                self.calls = []

            def update_idletasks(self):
                self.calls.append("idle")

            def update(self):
                self.calls.append("update")

        class FakeApp:
            def __init__(self):
                self.root = Root()
                self.page = None

            def show_page(self, page):
                self.page = page

            def _cancel_prewarm(self):
                pass

        app = FakeApp()
        timings = benchmark_ui._navigate_and_settle(app, "settings")
        self.assertEqual(app.root.calls, ["idle", "update"] * 2)
        self.assertEqual(app.page, "settings")
        self.assertEqual(set(timings), {"show_call_ms", "settled_ms"})

    def test_theme_font_families_are_enumerated_once(self) -> None:
        from llama_router.ui import theme

        previous = theme._MONO_FAMILY, theme._UI_FAMILY
        try:
            theme._MONO_FAMILY = None
            theme._UI_FAMILY = None
            with mock.patch.object(theme.tkfont, "families",
                                   return_value=set()) as families:
                theme.init_fonts(object())
                theme.init_fonts(object())
            self.assertEqual(families.call_count, 1)
        finally:
            theme._MONO_FAMILY, theme._UI_FAMILY = previous


if __name__ == "__main__":
    unittest.main()
