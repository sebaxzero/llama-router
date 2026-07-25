"""POSIX child supervisor for llama-server.

The app owns the write end of a pipe and this process owns the read end. File
descriptors are closed by the kernel even when the app dies via SIGKILL, so
EOF is a reliable parent-death notification on both Linux and macOS.
"""
from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[1] != "--fd":
        return 2
    life_fd = int(sys.argv[2])
    pidfile = Path(sys.argv[3])
    cmd = sys.argv[4:]
    if not cmd:
        return 2

    child = subprocess.Popen(cmd, start_new_session=True)
    pidfile.write_text(str(child.pid))
    stopping = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, request_stop)

    try:
        while child.poll() is None:
            if stopping:
                break
            readable, _, _ = select.select([life_fd], [], [], 0.25)
            if readable and not os.read(life_fd, 1):
                break

        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            except ProcessLookupError:
                pass
        return child.returncode if child.returncode is not None else 1
    finally:
        try:
            os.close(life_fd)
        except OSError:
            pass
        try:
            pidfile.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
