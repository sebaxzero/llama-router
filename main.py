"""llama-router entry-point — stdlib only, launches the Tkinter UI."""
from __future__ import annotations

import logging
import sys
import atexit
from pathlib import Path

from llama_router.core.events import EventBus
from llama_router.core.logs import LogService
from llama_router.core.paths import (PathManager, needs_first_run_choice,
                                     asset_path, resolve_base,
                                     user_data_base, write_base_pointer)
from llama_router.core.storage import InstanceGuard, init_db
from llama_router.core.windows import configure_app_identity
from llama_router.i18n import set_language


def _debug_requested(argv: list[str] | None = None) -> bool:
    """Return whether the application was launched with ``--debug``."""
    return "--debug" in (sys.argv[1:] if argv is None else argv)


def _first_run_base():
    """Frozen first launch: ask where the app should keep its data.

    Returns the chosen folder, or None for "next to the exe" (portable —
    also the answer when the dialog is simply closed). Uses a throwaway
    Tk root; App builds its own afterwards. English only: the language
    setting lives in the folder we are about to choose.
    """
    import tkinter as tk
    from tkinter import filedialog

    result: dict = {"base": None}
    root = tk.Tk()
    root.title("Llama Router")
    root.resizable(False, False)
    try:
        icon = tk.PhotoImage(file=str(asset_path("app_icon.png")))
        root.iconphoto(True, icon)
    except Exception:
        icon = None
    tk.Label(root, justify="left", padx=20, pady=14,
             text="Where should Llama Router keep its data\n"
                  "(config, models, runtimes, logs)?").pack(anchor="w")
    row = tk.Frame(root, padx=20, pady=14)
    row.pack(fill="x")

    def portable() -> None:
        root.destroy()

    def choose() -> None:
        folder = filedialog.askdirectory(parent=root,
                                         title="Choose data folder")
        if folder:
            result["base"] = folder
            root.destroy()

    tk.Button(row, text="Use this folder (portable)", default="active",
              command=portable).pack(side="left")
    tk.Button(row, text="Choose folder…", command=choose).pack(
        side="left", padx=(12, 0))
    root.protocol("WM_DELETE_WINDOW", portable)
    root.eval("tk::PlaceWindow . center")
    root.mainloop()
    return result["base"]


def main() -> int:
    debug = _debug_requested()

    # Frozen POSIX builds re-enter this executable as the process supervisor;
    # source runs execute the helper script directly.
    if (sys.platform != "win32" and len(sys.argv) > 1
            and sys.argv[1] == "--posix-supervisor"):
        del sys.argv[1]
        from llama_router.services._posix_supervisor import main as supervise
        return supervise()

    if sys.version_info < (3, 10):
        print("llama-router requires Python 3.10+", file=sys.stderr)
        return 1

    try:
        import tkinter  # noqa: F401  — not bundled with some Linux pythons
    except ImportError:
        print("Tkinter is not available. On Debian/Ubuntu:"
              " sudo apt install python3-tk", file=sys.stderr)
        return 1

    # Must precede every UI surface so Windows attributes taskbar entries and
    # notifications to llama-router rather than the hosting python.exe.
    configure_app_identity(asset_path("app_icon.ico"))

    selected_base = None
    if needs_first_run_choice():
        from llama_router.ui import theme
        theme.set_dpi_aware()
        base = _first_run_base()
        if base is not None:
            selected_base = Path(base)
            try:
                write_base_pointer(selected_base)
            except OSError as e:
                print(f"Could not save the data-folder choice ({e}); "
                      "running portable.", file=sys.stderr)

    paths = PathManager(selected_base or resolve_base())
    try:
        paths.ensure_dirs()
    except OSError as e:
        if not getattr(sys, "frozen", False):
            raise
        fallback = user_data_base()
        logging.warning("Portable data folder is not writable (%s); using %s",
                        e, fallback)
        paths = PathManager(fallback)
        paths.ensure_dirs()
        try:
            write_base_pointer(fallback)
        except OSError:
            pass

    instance_guard = InstanceGuard(paths.config_dir / "llama-router.instance")
    if not instance_guard.acquire():
        print("Another Llama Router instance is already using this data folder.",
              file=sys.stderr)
        return 1
    atexit.register(instance_guard.release)
    init_db(paths.db_path)

    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    events = EventBus()
    logs = LogService(events)
    logs.install(paths.logs_dir)

    from llama_router.services.config_manager import ConfigManager
    from llama_router.services.models_manager import ModelsManager
    from llama_router.services.download_manager import DownloadManager
    from llama_router.services.runtime_manager import RuntimeManager
    from llama_router.services.preset_manager import PresetManager
    from llama_router.services.parameter_catalog import ParameterCatalog

    services: dict = {}
    services["config"] = config = ConfigManager(paths, events)
    config.load()
    set_language(config.get().language)

    services["models"] = models = ModelsManager(paths, config, events)
    models.load()
    services["downloads"] = downloads = DownloadManager(paths, config, events)
    services["runtimes"] = runtimes = RuntimeManager(paths, config, downloads, events)
    runtimes.load()
    downloads.load()

    services["preset"] = preset = PresetManager(
        paths, models, events, runtimes=runtimes)
    # The registry only refreshes diagnostics/suggestions.  It never writes
    # routes or parameters; models-preset.ini is the runtime source of truth.
    # Reparse it so status/context reflects newly missing or restored GGUF paths.
    for event in ("models_scanned", "model_removed"):
        events.subscribe(event, lambda _data, p=preset: p.load(
            force=True, origin="registry"))
    services["catalog"] = catalog = ParameterCatalog(paths, runtimes, events)
    preset.set_catalog(catalog)
    events.subscribe("parameter_catalog_changed", lambda _data, p=preset: p.load(
        force=True, origin="catalog"))
    events.subscribe("runtime_activated", lambda _data, c=catalog:
                     c.refresh_runtime(background=True))
    catalog.refresh_runtime(background=True)

    from llama_router.services.server_manager import ServerManager
    services["server"] = server = ServerManager(
        config, runtimes, preset, events, paths, logs)
    server.reap_orphan()

    from llama_router.services.playground import PlaygroundService
    services["playground"] = PlaygroundService(
        config, server, preset, events, paths)

    from llama_router.services.gpu_monitor import GpuMonitor
    services["gpu_monitor"] = gpu = GpuMonitor(events)
    gpu.start()

    from llama_router.services.system_monitor import SystemMonitor
    services["system_monitor"] = system = SystemMonitor(events)
    system.start()

    from llama_router.ui.app import App, AppContext
    ctx = AppContext(paths=paths, events=events, logs=logs,
                     colors={}, services=services)
    app = App(ctx)
    logging.getLogger(__name__).info("llama-router started (base: %s)", paths.base)

    if config.get().autostart_server:
        app.root.after(500, server.start)

    app.run()

    logs.close()
    instance_guard.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
