from __future__ import annotations

import sys
import os
from pathlib import Path


class PathManager:
    """Resolves all application paths relative to the base directory.

    The base directory is the folder containing the running script (or executable
    when frozen), making the app fully portable — no home-dir pollution.
    """

    def __init__(self, base: Path) -> None:
        self.base = base.resolve()

        self.config_dir = self.base / "config"
        self.models_dir = self.base / "models"
        self.runtime_dir = self.base / "runtime"
        self.logs_dir = self.base / "logs"

        # Database
        self.db_path = self.config_dir / "llama_router.db"

        # Generated files
        self.preset_ini = self.config_dir / "models-preset.ini"

    def ensure_dirs(self) -> None:
        for d in (self.config_dir, self.models_dir, self.runtime_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


# One-line text file next to the exe holding the absolute path of the data
# folder. Written by the frozen build's first-run dialog when the user picks a
# custom folder; readable and deletable by hand to reset.
_POINTER_NAME = "llama-router.base"


def asset_path(name: str) -> Path:
    """Return a bundled asset in script mode or a PyInstaller one-file app."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        root = Path(sys._MEIPASS)
    else:
        root = Path(__file__).parent.parent.parent
    return root / "llama_router" / "assets" / name


def _anchor() -> Path:
    """Folder of the executable (frozen) or the repo root (script mode)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent


def resolve_base(anchor: Path | None = None) -> Path:
    """Return the directory that should be treated as the app root.

    The pointer file's target wins when it names an existing directory;
    otherwise the app is portable and lives next to the exe / repo root.
    """
    anchor = anchor if anchor is not None else _anchor()
    try:
        raw = (anchor / _POINTER_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        raw = ""
    if raw:
        target = Path(raw)
        if target.is_dir():
            return target
    return anchor


def needs_first_run_choice(anchor: Path | None = None) -> bool:
    """True on a frozen build's very first launch.

    A pointer file *or* an existing ``config/`` next to the exe means the
    install is already initialised (the latter keeps pre-pointer portable
    setups working without ever seeing the dialog).
    """
    if not getattr(sys, "frozen", False):
        return False
    anchor = anchor if anchor is not None else _anchor()
    return not (anchor / _POINTER_NAME).exists() \
        and not (anchor / "config").exists()


def write_base_pointer(target: Path, anchor: Path | None = None) -> None:
    anchor = anchor if anchor is not None else _anchor()
    (anchor / _POINTER_NAME).write_text(str(Path(target).resolve()),
                                        encoding="utf-8")


def user_data_base() -> Path:
    """Return a writable per-user data location for the current platform."""
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return root / "llama-router"
