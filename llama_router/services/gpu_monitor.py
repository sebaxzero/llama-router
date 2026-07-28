"""GPU stats via nvidia-smi (LlamaForge's approach — no dependencies).

Publishes `gpu_stats` events every couple of seconds:
    [{"name": str, "util": int, "mem_used": int, "mem_total": int}, …]  (MiB)

If nvidia-smi isn't on PATH the monitor never starts and the UI simply hides
its GPU card. AMD/Intel: future work.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading

from llama_router.core.events import EventBus

log = logging.getLogger(__name__)

_INTERVAL = 2.0
_QUERY = ("--query-gpu=name,utilization.gpu,memory.used,memory.total",
          "--format=csv,noheader,nounits")


class GpuMonitor:
    def __init__(self, events: EventBus) -> None:
        self._events = events
        self._smi = shutil.which("nvidia-smi")
        self._active = False
        self._cv = threading.Condition()

    def set_active(self, active: bool) -> None:
        with self._cv:
            self._active = active
            self._cv.notify_all()

    def start(self) -> None:
        if self._smi is None:
            log.info("nvidia-smi not found — GPU stats disabled")
            return
        threading.Thread(target=self._loop, daemon=True,
                         name="gpu-monitor").start()

    def _loop(self) -> None:
        extra = ({"creationflags": subprocess.CREATE_NO_WINDOW}
                 if sys.platform == "win32" else {})
        failures = 0
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._active)
            try:
                out = subprocess.run(
                    [self._smi, *_QUERY], capture_output=True, text=True,
                    timeout=5, **extra).stdout
                gpus = []
                for line in out.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 4:
                        gpus.append({
                            "name": parts[0],
                            "util": int(float(parts[1])),
                            "mem_used": int(float(parts[2])),
                            "mem_total": int(float(parts[3])),
                        })
                if gpus:
                    self._events.publish("gpu_stats", gpus)
                    failures = 0
            except (subprocess.SubprocessError, ValueError, OSError):
                failures += 1
                if failures >= 3:
                    log.warning("nvidia-smi failing repeatedly — "
                                "GPU stats disabled")
                    return
            with self._cv:
                self._cv.wait_for(lambda: not self._active,
                                  timeout=_INTERVAL)
