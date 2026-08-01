"""Capture screenshots or a short demo video of the running Tkinter UI.

Two jobs in one tool:

  * **Static reference set** (no arguments) — walks every page in the default
    theme and re-shoots Settings in Midnight and Light.
  * **Ad-hoc captures** (`--pages` / `--themes`) — shoot any page/theme
    combination into any directory, for reviewing a change or handing images
    to a human.
  * **README demo** (`--video`) — records a polished MP4 and GIF with a
    synthetic cursor, smooth camera moves, clicks, and the full app tour.

Usage (from the repository root):

    py -3 tools/screenshots.py                                  # README set
    py -3 tools/screenshots.py --pages profiles settings        # midnight only
    py -3 tools/screenshots.py --pages settings --themes light arctic
    py -3 tools/screenshots.py --pages dashboard --sizes 960x640 1280x860
    py -3 tools/screenshots.py --pages dashboard --out _scratch --keep-open
    tools/.venv/Scripts/python tools/screenshots.py --video --out _capture

Install the dev-only capture dependencies in the tool-local virtual
environment (never import them from `llama_router/`):

    py -3 -m venv tools/.venv
    tools/.venv/Scripts/python -m pip install -r tools/requirements.txt

Screenshots only need Pillow. Video uses imageio-ffmpeg's bundled FFmpeg when
there is no `ffmpeg` executable on PATH. The MP4 is the high-quality source;
the smaller looping GIF is the version embedded by `README.md`.

The fixture locks the real data folder, copies only its database and generated
preset into a temporary base, and keeps every write there. Registered model
and runtime paths remain absolute, so a populated registry still gives better
screenshots without copying large files.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(os.environ.get(
    "LLAMA_ROUTER_ROOT", Path(__file__).resolve().parent.parent))
if getattr(sys, "frozen", False) and "LLAMA_ROUTER_ROOT" not in os.environ:
    ROOT = next((parent for parent in Path(sys.executable).resolve().parents
                 if (parent / "tools" / "screenshots.py").is_file()), ROOT)
sys.path.insert(0, str(ROOT))

try:
    from PIL import Image, ImageColor, ImageDraw, ImageGrab
except ImportError:
    print("Pillow is required for screenshots: pip install pillow",
          file=sys.stderr)
    raise SystemExit(1)

# Static page set used by the no-argument capture mode.
README_PAGES = ("dashboard", "playground", "profiles",
                "runtime", "settings")
README_THEME_SHOTS = ("midnight", "light")
DEFAULT_THEME = "midnight"
DEFAULT_SIZE = (1280, 860)
VIDEO_FILE = "app_demo.mp4"
DEMO_GIF_FILE = "app_demo.gif"
VIDEO_THEME = "light"
VIDEO_FPS = 15
GIF_FPS = 10
VIDEO_SIZE = (960, 504)
GIF_SIZE = (800, 420)
VIDEO_PROMPT = "hello world"


def build_app(*, base: Path, demo: bool = False):
    """Construct the capture fixture against an explicit isolated base."""
    from llama_router.core.events import EventBus
    from llama_router.core.logs import LogService
    from llama_router.core.paths import PathManager
    from llama_router.core.storage import init_db
    from llama_router.i18n import set_language
    from llama_router.preset import write_preset
    from llama_router.services.config_manager import ConfigManager
    from llama_router.services.download_manager import DownloadManager
    from llama_router.services.models_manager import ModelsManager
    from llama_router.services.playground import PlaygroundService
    from llama_router.services.profile_manager import ProfileManager
    from llama_router.services.runtime_manager import RuntimeManager
    from llama_router.services.server_manager import ServerManager
    from llama_router.ui.app import App, AppContext

    paths = PathManager(base)
    paths.ensure_dirs()
    init_db(paths.db_path)

    events = EventBus()
    logs = LogService(events)
    logs.install(paths.logs_dir)

    services: dict = {}
    services["config"] = config = ConfigManager(paths, events)
    config.load()
    # Captures are documentation artifacts, not a reflection of the user's
    # persisted language preference.
    set_language("en")

    services["models"] = models = ModelsManager(paths, config, events)
    models.load()
    services["profiles"] = profiles = ProfileManager(paths, models, events)
    profiles.load()
    write_preset(paths.preset_ini, models.list(), profiles.by_model(),
                 config.get().global_params)

    services["downloads"] = downloads = DownloadManager(paths, config, events)
    services["runtimes"] = runtimes = RuntimeManager(
        paths, config, downloads, events)
    runtimes.load()

    services["server"] = server = ServerManager(
        config, runtimes, models, profiles, events, paths, logs)
    # Static captures are observational and must not touch shared instance
    # lockfiles. The demo owns the app lock and starts a real server, so it can
    # perform the same stale-process cleanup as main.py.
    if demo:
        server.reap_orphan()
    services["playground"] = PlaygroundService(
        config, server, profiles, events, paths)

    if demo:
        from llama_router.services.gpu_monitor import GpuMonitor
        from llama_router.services.system_monitor import SystemMonitor
        services["gpu_monitor"] = gpu = GpuMonitor(events)
        services["system_monitor"] = system = SystemMonitor(events)
        gpu.start()
        system.start()
    else:
        _ensure_sample_model(models, profiles)

    ctx = AppContext(paths=paths, events=events, logs=logs,
                     colors={}, services=services, enable_tray=False)
    return App(ctx)


def _ensure_sample_model(models, profiles) -> None:
    """Inject a placeholder; fixture persistence stays in the temp base.

    Written straight into the registry dict and deliberately NOT persisted —
    running this tool must never mutate the user's real model list.
    """
    if models.list():
        return
    from llama_router.schemas import ModelEntry, ModelState

    entry = ModelEntry(
        id="model_sample01",
        name="Sample-Model-8B-Q4_K_M",
        path=str(ROOT / "models" / "Sample-Model-8B-Q4_K_M.gguf"),
        size=4_920_000_000,
        state=ModelState.VALID,
        enabled=True,
        added_at="2026-01-01T00:00:00",
        meta={"arch": "llama", "quant": "Q4_K_M",
              "params": "8B", "ctx": 131072},
    )
    models._models[entry.id] = entry
    profiles.ensure_defaults(entry.id)
    print("note: model registry empty — injected an in-memory placeholder")


@contextmanager
def isolated_capture_base(real_base: Path | None = None):
    """Yield a temporary copy of capture state while locking *real_base*.

    Only the database and generated preset are copied. Registered model and
    runtime paths remain absolute references, so no large user files need to
    be duplicated and every write made by the fixture is disposable.
    """
    from llama_router.core.paths import PathManager, resolve_base
    from llama_router.core.storage import InstanceGuard

    source = PathManager((real_base or resolve_base()).resolve())
    guard = InstanceGuard(source.config_dir / "llama-router.instance")
    if not guard.acquire():
        raise RuntimeError("close Llama Router before capturing screenshots")
    try:
        with tempfile.TemporaryDirectory(prefix="llama-router-capture-") as td:
            target_paths = PathManager(Path(td))
            target_paths.ensure_dirs()
            for source_path, target_path in (
                    (source.db_path, target_paths.db_path),
                    (source.preset_ini, target_paths.preset_ini)):
                if source_path.is_file():
                    shutil.copy2(source_path, target_path)
            yield target_paths.base
    finally:
        guard.release()


def _place_in_work_area(app, width: int, height: int) -> None:
    """Place the outer window wholly above the Windows taskbar."""
    app.root.geometry(f"{width}x{height}+0+0")
    app.root.update_idletasks()
    app.root.update()

    if sys.platform != "win32":
        return

    import ctypes
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                   ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    work = RECT()
    rect = RECT()
    user32 = ctypes.windll.user32
    if not user32.SystemParametersInfoW(48, 0, ctypes.byref(work), 0):
        return
    hwnd = app.root.winfo_id()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return
    outer_w, outer_h = rect.right - rect.left, rect.bottom - rect.top
    target_x = work.left + max(0, (work.right - work.left - outer_w) // 2)
    target_y = work.top + max(0, (work.bottom - work.top - outer_h) // 2)
    user32.SetWindowPos(hwnd, None, target_x, target_y, 0, 0,
                        0x0001 | 0x0004 | 0x0010)  # NOSIZE|NOZORDER|NOACTIVATE
    app.root.update_idletasks()
    app.root.update()


def _raise_window(app, settle: float = 0.3) -> None:
    try:
        app.root.attributes("-topmost", True)
        app.root.deiconify()
        app.root.lift()
        app.root.focus_force()
        app.root.update_idletasks()
        app.root.update()
        app.root.attributes("-topmost", False)
        if settle:
            time.sleep(settle)
    except Exception:
        pass


def _maximize_window(app) -> None:
    app.root.deiconify()
    try:
        if sys.platform == "win32":
            app.root.state("zoomed")
        else:
            app.root.attributes("-zoomed", True)
    except Exception:
        app.root.geometry(
            f"{app.root.winfo_screenwidth()}x{app.root.winfo_screenheight()}+0+0")
    app.root.update_idletasks()
    app.root.update()
    _raise_window(app)


def _client_box(app, *, even: bool = False) -> tuple[int, int, int, int]:
    """Return a visible client-area box, optionally cropped to even dimensions."""
    app.root.update_idletasks()
    x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
    width, height = app.root.winfo_width(), app.root.winfo_height()
    screen_w = app.root.winfo_screenwidth()
    screen_h = app.root.winfo_screenheight()
    x = max(0, min(x, screen_w - 1))
    y = max(0, min(y, screen_h - 1))
    width = min(width, screen_w - x)
    height = min(height, screen_h - y)
    if even:
        width -= width % 2
        height -= height % 2
    return x, y, x + width, y + height


def _grab_client(app, box=None):
    return ImageGrab.grab(bbox=box or _client_box(app)).convert("RGB")


def _ease(value: float) -> float:
    """Cubic ease-in/out for camera and cursor motion."""
    value = max(0.0, min(1.0, value))
    return 4 * value ** 3 if value < 0.5 \
        else 1 - ((-2 * value + 2) ** 3) / 2


def _camera_box(source_size: tuple[int, int], output_size: tuple[int, int],
                center: tuple[float, float], zoom: float
                ) -> tuple[int, int, int, int]:
    """Return an aspect-correct, bounds-clamped crop for a camera view."""
    source_w, source_h = source_size
    output_w, output_h = output_size
    aspect = output_w / output_h
    base_w = float(source_w)
    base_h = base_w / aspect
    if base_h > source_h:
        base_h = float(source_h)
        base_w = base_h * aspect

    zoom = max(1.0, zoom)
    crop_w = max(2, round(base_w / zoom))
    crop_h = max(2, round(crop_w / aspect))
    if crop_h > source_h:
        crop_h = source_h
        crop_w = round(crop_h * aspect)

    center_x = max(crop_w / 2, min(source_w - crop_w / 2, center[0]))
    center_y = max(crop_h / 2, min(source_h - crop_h / 2, center[1]))
    left = round(center_x - crop_w / 2)
    top = round(center_y - crop_h / 2)
    return left, top, left + crop_w, top + crop_h


def _render_demo_frame(source, output_size: tuple[int, int],
                       cursor: tuple[float, float],
                       camera: tuple[float, float], zoom: float,
                       accent: str, click: float | None = None):
    """Crop/scale one frame and paint a consistent demo cursor over it."""
    crop = _camera_box(source.size, output_size, camera, zoom)
    frame = source.crop(crop).resize(output_size, Image.Resampling.LANCZOS)
    scale_x = output_size[0] / (crop[2] - crop[0])
    scale_y = output_size[1] / (crop[3] - crop[1])
    x = round((cursor[0] - crop[0]) * scale_x)
    y = round((cursor[1] - crop[1]) * scale_y)

    overlay = Image.new("RGBA", output_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if click is not None and 0 <= x < output_size[0] and 0 <= y < output_size[1]:
        progress = max(0.0, min(1.0, click))
        radius = round(8 + 22 * progress)
        alpha = round(220 * (1 - progress))
        color = (*ImageColor.getrgb(accent), alpha)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                     outline=color, width=3)

    # Familiar Windows/macOS-style pointer, kept crisp at README scale.
    pointer = ((0, 0), (0, 24), (6, 18), (12, 30),
               (17, 27), (10, 16), (21, 15))
    shadow = [(x + dx + 2, y + dy + 2) for dx, dy in pointer]
    points = [(x + dx, y + dy) for dx, dy in pointer]
    draw.polygon(shadow, fill=(0, 0, 0, 150))
    draw.polygon(points, fill=(250, 252, 255, 255),
                 outline=(8, 12, 18, 255), width=2)
    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def make_shooter(app, out_dir: Path):
    def shot(filename: str, size: tuple[int, int] = DEFAULT_SIZE) -> None:
        _place_in_work_area(app, *size)
        time.sleep(0.2)
        _raise_window(app)
        _grab_client(app).save(str(out_dir / filename))
        print(f"saved {out_dir.name}/{filename}")

    return shot


def _ffmpeg_executable() -> str | None:
    """Prefer a system FFmpeg, then the tool-venv bundled executable."""
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    return imageio_ffmpeg.get_ffmpeg_exe()


def _collapsible_cards(widget) -> list:
    from llama_router.ui.widgets import CollapsibleCard

    cards = []

    def visit(parent) -> None:
        for child in parent.winfo_children():
            if isinstance(child, CollapsibleCard):
                cards.append(child)
            visit(child)

    visit(widget)
    return cards


def _collapse_page(page) -> None:
    """Put every disclosure on a page in the deterministic closed state."""
    for card in _collapsible_cards(page):
        card.set_open(False)
    if hasattr(page, "_set_logs_open"):
        page._set_logs_open(False)
    if getattr(page, "_steps_card_open", False):
        page._toggle_steps_card()


def _show_collapsed_page(app, key: str):
    # Runtime checks GitHub on first show. Pre-create it and mark that optional
    # refresh handled so a no-interaction tour remains deterministic/offline.
    if key == "runtime" and key not in app._pages:
        app._create_page(key)
        app._pages[key]._fetched = True
    app.show_page(key)
    page = app._pages[key]
    _collapse_page(page)
    app.root.update_idletasks()
    app.root.update()
    return page


def _demo_problem(app) -> str | None:
    runtimes = app.ctx.services["runtimes"]
    if not runtimes.get_executable():
        return "no valid active runtime is configured"
    models = app.ctx.services["models"].list()
    profiles = app.ctx.services["profiles"].by_model()
    if not any(model.enabled and model.state == "valid"
               and any(profile.active for profile in profiles.get(model.id, ()))
               for model in models):
        return "no enabled model has an active profile"
    return None


class _DemoRecorder:
    """Small deterministic camera layered over the real Tkinter window."""

    def __init__(self, app, output: Path, ffmpeg: str,
                 fps: int = VIDEO_FPS) -> None:
        self.app = app
        self.box = _client_box(app, even=True)
        self.source_size = (self.box[2] - self.box[0],
                            self.box[3] - self.box[1])
        self.output_size = VIDEO_SIZE
        self.fps = fps
        self.cursor = (self.source_size[0] * 0.08,
                       self.source_size[1] * 0.08)
        self.camera = (self.source_size[0] / 2,
                       self.source_size[1] / 2)
        self.zoom = 1.0
        self._hovered = None
        self._next_frame = time.perf_counter()

        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-video_size", f"{VIDEO_SIZE[0]}x{VIDEO_SIZE[1]}",
            "-framerate", str(fps), "-i", "-", "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.encoder = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, creationflags=flags)
        assert self.encoder.stdin is not None

    @property
    def center(self) -> tuple[float, float]:
        return self.source_size[0] / 2, self.source_size[1] / 2

    def point(self, widget) -> tuple[float, float]:
        self.app.root.update_idletasks()
        return (widget.winfo_rootx() - self.box[0] + widget.winfo_width() / 2,
                widget.winfo_rooty() - self.box[1] + widget.winfo_height() / 2)

    def _frame(self, click: float | None = None) -> None:
        self.app.root.update_idletasks()
        self.app.root.update()
        source = _grab_client(self.app, self.box)
        rendered = _render_demo_frame(
            source, self.output_size, self.cursor, self.camera, self.zoom,
            self.app.ctx.colors["accent"], click)
        self.encoder.stdin.write(rendered.tobytes())
        self._next_frame += 1 / self.fps
        delay = self._next_frame - time.perf_counter()
        if delay > 0:
            time.sleep(delay)

    def hold(self, seconds: float) -> None:
        for _ in range(max(1, round(seconds * self.fps))):
            self._frame()

    def animate(self, seconds: float, *, cursor=None, camera=None,
                zoom: float | None = None) -> None:
        start_cursor, start_camera, start_zoom = self.cursor, self.camera, self.zoom
        target_cursor = cursor or start_cursor
        target_camera = camera or start_camera
        target_zoom = zoom if zoom is not None else start_zoom
        count = max(1, round(seconds * self.fps))
        for index in range(1, count + 1):
            amount = _ease(index / count)
            self.cursor = tuple(a + (b - a) * amount
                                for a, b in zip(start_cursor, target_cursor))
            self.camera = tuple(a + (b - a) * amount
                                for a, b in zip(start_camera, target_camera))
            self.zoom = start_zoom + (target_zoom - start_zoom) * amount
            self._frame()

    def _hover(self, widget) -> None:
        if self._hovered is widget:
            return
        if self._hovered is not None:
            try:
                self._hovered.event_generate("<Leave>")
            except Exception:
                pass
        try:
            widget.event_generate("<Enter>")
        except Exception:
            pass
        self._hovered = widget

    def move_to(self, widget, *, seconds: float = 0.45,
                zoom: float = 1.14, camera=None) -> None:
        target = self.point(widget)
        self.animate(seconds, cursor=target, camera=camera or target, zoom=zoom)
        self._hover(widget)

    def click(self, widget, action, *, zoom: float = 1.14,
              camera=None) -> None:
        self.move_to(widget, zoom=zoom, camera=camera)
        count = max(4, round(0.3 * self.fps))
        midpoint = count // 2
        for index in range(count):
            if index == midpoint:
                action()
                self.app.root.update_idletasks()
                self.app.root.update()
            self._frame(index / (count - 1))

    def wait_for(self, predicate, timeout: float) -> bool:
        for _ in range(round(timeout * self.fps)):
            self._frame()
            if predicate():
                return True
        return bool(predicate())

    def finish(self) -> None:
        self.encoder.stdin.close()
        error = self.encoder.stderr.read().decode("utf-8", "replace").strip()
        if self.encoder.wait() != 0:
            raise RuntimeError(error or "FFmpeg failed to encode the video")

    def abort(self) -> None:
        if self.encoder.poll() is None:
            self.encoder.kill()
            self.encoder.wait()


def _make_demo_gif(ffmpeg: str, video: Path, output: Path) -> None:
    """Create a palette-optimized, looping README GIF from the MP4."""
    filters = (
        f"fps={GIF_FPS},scale={GIF_SIZE[0]}:{GIF_SIZE[1]}:flags=lanczos,"
        "split[frames][palette_source];"
        "[palette_source]palettegen=max_colors=128:stats_mode=diff[palette];"
        "[frames][palette]paletteuse=dither=bayer:bayer_scale=4:"
        "diff_mode=rectangle"
    )
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(video),
         "-filter_complex", filters, "-loop", "0", str(output)],
        capture_output=True, text=True, creationflags=flags)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "FFmpeg failed to make GIF")


def record_demo(app, output: Path, ffmpeg: str,
                fps: int = VIDEO_FPS) -> None:
    """Record the polished README tour requested for the real app."""
    app.ctx.apply_theme(DEFAULT_THEME)
    dashboard = _show_collapsed_page(app, "dashboard")
    dashboard._scroll.scroll_to_start()
    _maximize_window(app)
    recorder = _DemoRecorder(app, output, ffmpeg, fps)
    recorder.cursor = recorder.point(app._head_mark)

    try:
        recorder.hold(0.7)
        print("video: starting server")
        server = app.ctx.services["server"]
        start_result = {}

        def start_server() -> None:
            start_result.update(server.start())

        recorder.click(dashboard._start_btn, start_server, zoom=1.18)
        if not start_result.get("ok"):
            raise RuntimeError(start_result.get("error") or "server did not start")
        if not recorder.wait_for(
                lambda: getattr(server.status, "value", server.status)
                == "running", 90):
            status = getattr(server.status, "value", server.status)
            raise RuntimeError(f"server did not become ready (status: {status})")
        recorder.animate(0.35, camera=recorder.center, zoom=1.0)
        recorder.hold(0.45)

        print("video: opening Connect your client")
        def open_client() -> None:
            dashboard._ep.set_open(True)
            dashboard._scroll.see(dashboard._ep)

        recorder.click(
            dashboard._ep._toggle, open_client, zoom=1.03)
        client_center = recorder.point(dashboard._ep)
        recorder.animate(
            0.4,
            cursor=(recorder.source_size[0] * 0.78, client_center[1]),
            camera=(recorder.source_size[0] * 0.45, client_center[1]),
            zoom=1.15)
        recorder.hold(0.75)

        print("video: sending hello world in Playground")
        recorder.click(
            app._nav["playground"],
            lambda: _show_collapsed_page(app, "playground"), zoom=1.08,
            camera=(recorder.center[0], recorder.source_size[1] * 0.12))
        playground = app._pages["playground"]
        recorder.animate(0.35, camera=recorder.center, zoom=1.0)
        recorder.hold(0.35)
        recorder.click(
            playground._input, playground._input.focus_set, zoom=1.2,
            camera=recorder.point(playground._input))
        input_center = recorder.point(playground._input)
        input_text = (
            input_center[0] - playground._input.winfo_width() / 2 + 110,
            input_center[1],
        )
        recorder.animate(0.3, cursor=input_text, camera=input_text, zoom=2.35)
        for character in VIDEO_PROMPT:
            playground._input.insert("end", character)
            recorder.hold(0.12)
        recorder.hold(0.15)
        playground._send()
        recorder.animate(
            0.28,
            cursor=(recorder.source_size[0] * 0.84,
                    recorder.source_size[1] * 0.9),
            camera=(recorder.source_size[0] * 0.47, recorder.center[1]),
            zoom=1.08)
        recorder.hold(2.0)

        print("video: showing Models, Runtime, and Settings")
        for key in ("profiles", "runtime", "settings"):
            recorder.click(
                app._nav[key], lambda page=key: _show_collapsed_page(app, page),
                zoom=1.08,
                camera=(recorder.center[0], recorder.source_size[1] * 0.12))
            recorder.animate(0.3, camera=recorder.center, zoom=1.0)
            recorder.hold(0.75)
        settings = app._pages["settings"]

        appearance = next(
            card for card in _collapsible_cards(settings)
            if card._state_key == "settings.appearance")
        recorder.click(
            appearance._toggle, lambda: appearance.set_open(True), zoom=1.03)
        recorder.hold(0.55)
        theme_button = settings._theme_btns[VIDEO_THEME]
        recorder.click(
            theme_button, lambda: settings._pick_theme(VIDEO_THEME), zoom=1.28,
            camera=recorder.point(theme_button))
        recorder.animate(0.55, camera=recorder.center, zoom=1.02)
        recorder.hold(1.1)
    except Exception:
        recorder.abort()
        raise
    else:
        recorder.finish()
        gif = output.with_name(DEMO_GIF_FILE)
        _make_demo_gif(ffmpeg, output, gif)
    print(f"saved {output}")
    print(f"saved {gif}")


def _close_app(app, timeout: float = 20) -> None:
    """Drive Tk while App performs its asynchronous server shutdown."""
    try:
        if not app.root.winfo_exists():
            return
    except Exception:
        return
    try:
        app._on_close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if not app.root.winfo_exists():
                    return
                app.root.update()
            except Exception:
                return
            time.sleep(0.05)
    finally:
        server = app.ctx.services.get("server")
        if server is not None and server.is_running():
            server.stop(timeout=1)
        try:
            if app.root.winfo_exists():
                app._finish_close()
        except Exception:
            pass


@contextmanager
def capture_app(*, demo: bool = False, real_base: Path | None = None):
    """Build and close an App while its isolated base is still available."""
    with isolated_capture_base(real_base) as capture_base:
        app = build_app(base=capture_base, demo=demo)
        try:
            yield app
        finally:
            playground = app.ctx.services.get("playground")
            if playground is not None:
                playground.cancel()
            _close_app(app)
            for service in app.ctx.services.values():
                close = getattr(service, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            app.ctx.logs.close()

def main() -> int:
    from llama_router.ui import theme

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pages", nargs="+", metavar="PAGE",
                    help=f"pages to capture (default: the README set). "
                         f"Choices: {', '.join(README_PAGES)}")
    ap.add_argument("--themes", nargs="+", metavar="THEME",
                    help=f"themes to capture each page in (default: "
                         f"{DEFAULT_THEME}). Choices: "
                         f"{', '.join(theme.theme_names())}")
    ap.add_argument("--sizes", nargs="+", metavar="WxH",
                    help="window sizes for ad-hoc captures "
                         "(default: 1280x860)")
    ap.add_argument("--out", default="llama_router/assets/screenshots",
                    metavar="DIR", help="output directory "
                    "(default: llama_router/assets/screenshots)")
    ap.add_argument("--keep-open", action="store_true",
                    help="leave the window open for manual review")
    ap.add_argument("--video", action="store_true",
                    help=f"record the polished app tour as {VIDEO_FILE} and "
                         f"{DEMO_GIF_FILE}")
    args = ap.parse_args()

    if args.video and (args.pages or args.themes or args.sizes):
        print("--video cannot be combined with --pages, --themes, or --sizes",
              file=sys.stderr)
        return 2

    bad_pages = [p for p in (args.pages or []) if p not in README_PAGES]
    bad_themes = [t for t in (args.themes or []) if t not in theme.THEMES]
    if bad_pages or bad_themes:
        if bad_pages:
            print(f"unknown page(s): {', '.join(bad_pages)}", file=sys.stderr)
        if bad_themes:
            print(f"unknown theme(s): {', '.join(bad_themes)}", file=sys.stderr)
        return 2

    sizes = [DEFAULT_SIZE]
    if args.sizes:
        sizes = []
        for value in args.sizes:
            try:
                parts = value.lower().split("x")
                if len(parts) != 2:
                    raise ValueError
                width, height = (int(part) for part in parts)
                if width <= 0 or height <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                print(f"invalid size: {value} (expected WxH)",
                      file=sys.stderr)
                return 2
            sizes.append((width, height))

    out_dir = (ROOT / args.out) if not Path(args.out).is_absolute() \
        else Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg = _ffmpeg_executable() if args.video else None
    if args.video and ffmpeg is None:
        print("video capture needs tools/.venv: install tools/requirements.txt",
              file=sys.stderr)
        return 1

    try:
        with capture_app(demo=args.video) as app:
            if args.video:
                problem = _demo_problem(app)
                if problem:
                    raise RuntimeError(f"cannot record demo: {problem}")
                record_demo(app, out_dir / VIDEO_FILE, ffmpeg)
                print(f"\nDone — MP4 and GIF in {out_dir}")
            else:
                shot = make_shooter(app, out_dir)
                default_mode = (not args.pages and not args.themes
                                and not args.sizes)
                if default_mode:
                    app.ctx.apply_theme(DEFAULT_THEME)
                    for page in README_PAGES:
                        app.show_page(page)
                        shot(f"page_{page}.png")
                    for name in README_THEME_SHOTS:
                        app.ctx.apply_theme(name)
                        app.show_page("settings")
                        shot(f"settings_{name}.png")
                    app.ctx.apply_theme(DEFAULT_THEME)
                    app.show_page("dashboard")
                    shot("final_dashboard.png")
                else:
                    for name in (args.themes or [DEFAULT_THEME]):
                        app.ctx.apply_theme(name)
                        for page in (args.pages or README_PAGES):
                            for size in sizes:
                                app.show_page(page)
                                suffix = (f"_{size[0]}x{size[1]}"
                                          if args.sizes else "")
                                shot(f"{page}_{name}{suffix}.png", size)
                    app.ctx.apply_theme(DEFAULT_THEME)
                print(f"\nDone — images in {out_dir}")

            if args.keep_open:
                print("Window left open for review; close it when finished.")
                app.run()
            return 0
    except Exception as error:
        print(f"capture failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
