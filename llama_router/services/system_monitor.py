"""CPU + RAM stats with the standard library only (no psutil).

Publishes `system_stats` events every couple of seconds:
    {"cpu": float %, "mem_used": int, "mem_total": int}   (MiB)

Windows reads GetSystemTimes/GlobalMemoryStatusEx through ctypes, Linux reads
/proc, macOS shells out to sysctl/vm_stat/ps. Anywhere else the monitor never
starts and the UI hides its card.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import sys
import threading

from llama_router.core.events import EventBus

log = logging.getLogger(__name__)

_INTERVAL = 2.0
_MIB = 1024 * 1024


# ── Windows ──────────────────────────────────────────────────────────────────

class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _win_mem() -> tuple[int, int]:
    st = _MemoryStatusEx()
    st.dwLength = ctypes.sizeof(st)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    total = st.ullTotalPhys
    return (total - st.ullAvailPhys) // _MIB, total // _MIB


def _win_cpu_times() -> tuple[int, int]:
    """Returns (idle, total) in 100ns ticks."""
    idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
    ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    # kernel time already includes idle time
    return idle.value, kernel.value + user.value


# ── Linux ────────────────────────────────────────────────────────────────────

def _linux_mem() -> tuple[int, int]:
    info = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            k, _, rest = line.partition(":")
            info[k] = int(rest.split()[0])  # kB
    total = info["MemTotal"]
    avail = info.get("MemAvailable", info["MemFree"])
    return (total - avail) // 1024, total // 1024


def _linux_cpu_times() -> tuple[int, int]:
    with open("/proc/stat") as fh:
        parts = [int(v) for v in fh.readline().split()[1:]]
    return parts[3], sum(parts)


# ── macOS ────────────────────────────────────────────────────────────────────

def _sysctl(name: str) -> str:
    return subprocess.run(["sysctl", "-n", name], capture_output=True,
                          text=True, timeout=10).stdout.strip()


def _darwin_mem() -> tuple[int, int]:
    total = int(_sysctl("hw.memsize"))
    out = subprocess.run(["vm_stat"], capture_output=True, text=True,
                         timeout=10).stdout
    m = re.search(r"page size of (\d+)", out)
    page = int(m.group(1)) if m else 4096
    pages = {}
    for line in out.splitlines():
        k, _, v = line.partition(":")
        v = v.strip().rstrip(".")
        if v.isdigit():
            pages[k.strip()] = int(v)
    free = (pages.get("Pages free", 0) + pages.get("Pages inactive", 0)
            + pages.get("Pages speculative", 0)) * page
    return (total - free) // _MIB, total // _MIB


def _darwin_cpu_percent() -> float:
    out = subprocess.run(["ps", "-A", "-o", "%cpu="], capture_output=True,
                         text=True, timeout=10).stdout
    busy = sum(float(v) for v in out.split())
    return busy / (os.cpu_count() or 1)


class SystemMonitor:
    def __init__(self, events: EventBus) -> None:
        self._events = events
        self._active = False
        self._cv = threading.Condition()
        self._cpu_percent = None    # direct % source (macOS has no tick API)
        if sys.platform == "win32":
            self._mem, self._cpu_times = _win_mem, _win_cpu_times
        elif sys.platform.startswith("linux"):
            self._mem, self._cpu_times = _linux_mem, _linux_cpu_times
        elif sys.platform == "darwin":
            self._mem, self._cpu_times = _darwin_mem, None
            self._cpu_percent = _darwin_cpu_percent
        else:
            self._mem = self._cpu_times = None

    def set_active(self, active: bool) -> None:
        with self._cv:
            self._active = active
            self._cv.notify_all()

    def start(self) -> None:
        if self._mem is None:
            log.info("no stdlib CPU/RAM source on %s — system stats disabled",
                     sys.platform)
            return
        threading.Thread(target=self._loop, daemon=True,
                         name="system-monitor").start()

    def _loop(self) -> None:
        prev = self._cpu_times() if self._cpu_times else None
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._active)
                paused = self._cv.wait_for(lambda: not self._active,
                                           timeout=_INTERVAL)
            if paused:
                continue
            try:
                if self._cpu_times:
                    idle, total = self._cpu_times()
                    d_idle, d_total = idle - prev[0], total - prev[1]
                    prev = (idle, total)
                    cpu = 100.0 * (1 - d_idle / d_total) if d_total > 0 else 0.0
                else:
                    cpu = self._cpu_percent()
                used, mem_total = self._mem()
            except (OSError, ValueError, KeyError, IndexError,
                    subprocess.SubprocessError):
                log.warning("system stats unavailable — monitor stopped",
                            exc_info=True)
                return
            self._events.publish("system_stats", {
                "cpu": max(0.0, min(100.0, cpu)),
                "mem_used": used,
                "mem_total": mem_total,
            })
