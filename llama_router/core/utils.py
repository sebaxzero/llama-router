"""Shared utility functions."""
from __future__ import annotations

import os
import re
import socket
import stat
import tarfile
import uuid
import zipfile
from pathlib import Path


def uid(prefix: str) -> str:
    """Short unique id for a registry entry, e.g. ``model_1a2b3c4d``."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def fmt_bytes(nbytes: int, unit: str = "auto") -> str:
    """Human byte size. unit='mb' forces MB; 'auto' picks GiB/MiB."""
    if nbytes <= 0:
        return "—"
    if unit == "mb":
        return f"{nbytes / (1024 ** 2):.0f} MB"
    gib = nbytes / (1024 ** 3)
    return f"{gib:.1f} GiB" if gib >= 1 else f"{nbytes / (1024 ** 2):.0f} MiB"


# ANSI escape sequences llama.cpp emits on its log lines (SGR colours, cursor
# moves, OSC title sets). Strip them before storing/displaying so the UI shows
# clean text instead of raw "←[32m" garbage.
_ANSI_RE = re.compile(
    r"\x1B\[[0-?]*[ -/]*[@-~]"          # CSI  … final byte (colours, cursor)
    r"|\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)"  # OSC  … terminated by BEL or ST
    r"|\x1B[@-Z\\-_]"                    # other single-char escape sequences
)


def strip_ansi(text: str) -> str:
    """Remove ANSI/VT100 escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


def detect_backend(name: str) -> str:
    """Detect GPU backend from a filename string."""
    nl = name.lower()
    if "cuda" in nl or "cu1" in nl:
        return "cuda"
    if "vulkan" in nl:
        return "vulkan"
    if "metal" in nl:
        return "metal"
    return "cpu"


def sanitise(s: str) -> str:
    """Sanitise a string for use as an INI section name or route alias."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s).strip("_")[:24]


def cuda_major_ver(name: str) -> str | None:
    """Return CUDA major version string from an asset filename.

    Handles all llama.cpp naming conventions:
      cuda-12.4  →  '12'   (dash-separated)
      cuda12.4   →  '12'   (no separator)
      cu12.4     →  '12'   (cu-prefix, old format)
    Returns None if no CUDA version is found.
    """
    nl = name.lower()
    m = re.search(r"cuda[_-]?(\d+)", nl)
    if m:
        return m.group(1)
    m = re.search(r"\bcu(\d+)", nl)
    if m:
        return m.group(1)
    return None


def port_in_use(host: str, port: int) -> bool:
    """True if something already accepts connections on host:port.

    Guards against a silent double-bind: on Windows two processes can bind the
    same port without SO_EXCLUSIVEADDRUSE, so llama-server starts 'fine' while
    a foreign server keeps answering — and the health check then reports the
    wrong server as ours.
    """
    check_host = "127.0.0.1" if host == "0.0.0.0" else host
    try:
        with socket.create_connection((check_host, port), timeout=0.5):
            return True
    except OSError:
        return False


def extract_archive(archive: Path, target: Path) -> None:
    """Extract a zip or tar.gz archive, fix executable bits, then delete the archive."""
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()

    def safe_path(name: str) -> None:
        candidate = (root / name).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as e:
            raise ValueError(f"Unsafe archive member path: {name}") from e

    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                safe_path(member.filename)
                # Unix symlinks can be stored in ZIP external attributes.
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError(
                        f"Archive links are not supported: {member.filename}")
            zf.extractall(target)
    elif name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                safe_path(member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise ValueError(
                        f"Archive links/devices are not supported: {member.name}")
            tf.extractall(target)
    else:
        raise ValueError(f"Unsupported archive format: {archive.name}")
    archive.unlink(missing_ok=True)
    _fix_exec_bits(target)


def _fix_exec_bits(folder: Path) -> None:
    if os.name == "nt":
        return
    for p in folder.rglob("llama-server*"):
        if p.is_file() and p.name in ("llama-server", "llama-server.exe"):
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
