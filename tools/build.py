"""Build a one-file llama-router executable with PyInstaller (dev-only).

    py -3 tools/build.py            # windowed build for this OS
    py -3 tools/build.py --debug    # console build, for diagnostics
    py -3 tools/build.py --keep-work  # preserve build/ and the .spec file

PyInstaller does not cross-compile — run this on each target OS. Output
lands in dist/. By default the temporary build/ and llama-router.spec are
removed after every attempt; --keep-work preserves them for diagnostics. The
app creates its data folders at runtime; only the packaged icon assets are
added explicitly. PyInstaller bundles tkinter automatically. On macOS the
windowed build produces llama-router.app plus
the plain unix binary.

Like everything in tools/, this is dev-only: PyInstaller must never be
imported from llama_router/.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT / "build"
SPEC_FILE = ROOT / "llama-router.spec"
ASSETS_DIR = ROOT / "llama_router" / "assets"


def _cleanup_scratch() -> None:
    """Remove only this tool's known PyInstaller scratch paths."""
    root = ROOT.resolve()
    work = WORK_DIR.resolve()
    spec = SPEC_FILE.resolve()
    if work.parent != root or work.name != "build":
        raise RuntimeError(f"Refusing to remove unexpected work path: {work}")
    if spec.parent != root or spec.name != "llama-router.spec":
        raise RuntimeError(f"Refusing to remove unexpected spec path: {spec}")
    if work.exists():
        shutil.rmtree(work)
    spec.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build a one-file llama-router executable.")
    ap.add_argument("--debug", action="store_true",
                    help="keep the console window for diagnostics")
    ap.add_argument("--keep-work", action="store_true",
                    help="preserve build/ and llama-router.spec")
    args = ap.parse_args()

    if importlib.util.find_spec("PyInstaller") is None:
        print("PyInstaller is not installed:  pip install pyinstaller",
              file=sys.stderr)
        return 1

    # -m keeps this working when Scripts/bin is not on PATH.
    cmd = [sys.executable, "-m", "PyInstaller",
           "--onefile", "--name", "llama-router",
           "--clean", "--noconfirm",
           "--icon", str(ASSETS_DIR / "app_icon.ico"),
           "--add-data", f"{ASSETS_DIR}{os.pathsep}llama_router/assets",
           "--console" if args.debug else "--noconsole",
           "--distpath", str(ROOT / "dist"),
           "--workpath", str(WORK_DIR),
           "--specpath", str(ROOT),
           str(ROOT / "main.py")]
    print(" ".join(cmd))
    try:
        rc = subprocess.run(cmd, cwd=ROOT).returncode
    finally:
        if not args.keep_work:
            _cleanup_scratch()
            print("cleaned build/ and llama-router.spec")
    if rc == 0:
        exe = "llama-router" + (".exe" if sys.platform == "win32" else "")
        print(f"\nbuild ok -> dist/{exe}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
