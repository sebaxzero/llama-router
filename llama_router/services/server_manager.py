"""llama-server process lifecycle — the ★ llama-server-manager.

Thread port of pi-test's asyncio ServerManager: Popen + a log-reader thread +
a health-check thread. Orphan reaping works without psutil: the recorded pid
is only killed after its image name is confirmed via tasklist (Windows) or
/proc (POSIX) — a reused pid is never killed blindly.

Each app instance is identified by its Python process PID (``os.getpid()``).
Each instance writes a lockfile ``config/instance_{pid}.lock`` that records
its llama-server pid. On startup ``reap_orphan()`` scans all lockfiles:
if a peer's lockfile points to an *inactive* pid it is reaped, otherwise
(a peer is alive) it is left alone — so two concurrent app instances never
step on each other's server.

On Windows, llama-server is placed in a Job Object with ``LIMIT_KILL_ON_JOB_CLOSE``
so a force-kill of the Python process also terminates the server.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from llama_router.core.events import EventBus
from llama_router.core.logs import LogService, parse_binary_level
from llama_router.core.paths import PathManager
from llama_router.core.utils import port_in_use, strip_ansi
from llama_router.preset import write_preset
from llama_router.schemas import ServerStatus
from llama_router.services.config_manager import ConfigManager
from llama_router.services.models_manager import ModelsManager
from llama_router.services.profile_manager import ProfileManager
from llama_router.services.runtime_manager import RuntimeManager

log = logging.getLogger(__name__)

# Per-instance lockfile: each Python process writes ``config/instance_{pid}.lock``
# containing its llama-server pid. reap_orphan() scans these lockfiles to
# distinguish "peer alive" (skip) from "peer dead" (reap).
_LOCK_PREFIX = "instance_"
_LOCK_SUFFIX = ".lock"

_HEALTH_INTERVAL = 5.0
_HEALTH_TIMEOUT = 3.0
_HEALTH_MAX_FAILURES = 3

# Image name of the managed server binary on this platform, used to confirm
# a recorded pid still belongs to llama-server before reaping it.
_SERVER_IMAGE = "llama-server.exe" if sys.platform == "win32" else "llama-server"

# Windows Job Object constants (kernel32.dll)
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_INFORMATION_CLASS_EXTENDED_LIMIT_INFORMATION = 9
_CREATE_SUSPENDED = 0x00000004
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


class _WindowsJob:
    """Wraps a Windows Job Object so all child processes die when Python dies.

    Used only on Windows. On other platforms this is a no-op.
    """

    __slots__ = ("_handle", "_closed")

    def __init__(self) -> None:
        self._handle: int = 0
        self._closed = False
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32

            # ctypes assumes a 32-bit int return value unless told otherwise.
            # HANDLE is pointer-sized, so leaving the default truncates Job
            # handles in 64-bit Python and makes every later call fail.
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p,
                                                   wintypes.LPCWSTR]
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE

            # CreateJobObject(hAttributeSecurityDescriptor, lpName)
            self._handle = kernel32.CreateJobObjectW(None, None)
            if not self._handle:
                self._handle = 0
                return

            # SetInformationJobObject requires the complete structure for the
            # selected information class. Passing only LimitFlags is rejected
            # with ERROR_BAD_LENGTH, which previously left llama-server alive
            # when the console hosting Python was closed.
            class _BASIC_LIMITS(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class _IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64),
                ]

            class _EXTENDED_LIMITS(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", _BASIC_LIMITS),
                    ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            info = _EXTENDED_LIMITS()
            info.BasicLimitInformation.LimitFlags = \
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

            SetInformationJobObject = kernel32.SetInformationJobObject
            SetInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            SetInformationJobObject.restype = wintypes.BOOL
            ok = SetInformationJobObject(
                self._handle,
                _JOB_OBJECT_INFORMATION_CLASS_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info))
            if not ok:
                log.warning("SetInformationJobObject failed (code %d)",
                            kernel32.GetLastError())
                self._close()
                return
        except Exception:
            log.warning("Could not create Windows Job Object", exc_info=True)
            self._close()
            return

    def assign(self, process_handle: int) -> bool:
        """Assign an existing process (by handle) to this job."""
        if not self._handle or self._closed:
            return False
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            assign = kernel32.AssignProcessToJobObject
            assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            assign.restype = wintypes.BOOL
            ok = assign(self._handle, process_handle)
            if not ok:
                log.warning("AssignProcessToJobObject failed (code %d); "
                            "console-close handler remains active",
                            kernel32.GetLastError())
                return False
            return True
        except Exception:
            log.warning("Could not assign llama-server to Windows Job Object",
                        exc_info=True)
            return False

    def contains(self, process_handle: int) -> bool:
        """Ask Windows whether *process_handle* belongs to this exact job."""
        if not self._handle or self._closed or sys.platform != "win32":
            return False
        try:
            import ctypes
            from ctypes import wintypes
            inside = wintypes.BOOL()
            check = ctypes.windll.kernel32.IsProcessInJob
            check.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                              ctypes.POINTER(wintypes.BOOL)]
            check.restype = wintypes.BOOL
            return bool(check(process_handle, self._handle,
                              ctypes.byref(inside)) and inside.value)
        except Exception:
            return False

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._handle:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = 0

    def close(self) -> None:
        self._close()

    def terminate(self, exit_code: int = 1) -> bool:
        """Immediately terminate every process assigned to this job."""
        if not self._handle or self._closed or sys.platform != "win32":
            return False
        try:
            import ctypes
            ok = ctypes.windll.kernel32.TerminateJobObject(
                self._handle, int(exit_code))
            if not ok:
                log.warning("TerminateJobObject failed (code %d)",
                            ctypes.windll.kernel32.GetLastError())
                return False
            return True
        except Exception:
            log.warning("Could not terminate Windows Job Object", exc_info=True)
            return False


class ServerManager:
    def __init__(self, config: ConfigManager, runtimes: RuntimeManager,
                 models: ModelsManager, profiles: ProfileManager,
                 events: EventBus, paths: PathManager, logs: LogService) -> None:
        self._config = config
        self._runtimes = runtimes
        self._models = models
        self._profiles = profiles
        self._events = events
        self._paths = paths
        self._logs = logs

        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._server_pid: int | None = None
        self._life_write_fd: int | None = None
        self._child_pidfile: Path | None = None
        self._status = ServerStatus.STOPPED
        self._start_time: float | None = None
        self._health_failures = 0
        self._loaded_models: list[str] = []
        self._active_host: str | None = None
        self._active_port: int | None = None
        self._active_api_key = False
        self._crash_times: list[float] = []   # recent unexpected exits
        self._session = 0                     # bumped on every start/stop

        # Per-instance identity — the Python process PID (``os.getpid()``).
        # Each instance writes a lockfile ``config/instance_{pid}.lock`` so
        # concurrent instances can coexist without stepping on each other.
        self._instance_id = str(os.getpid())
        self._lockfile: Path | None = None

        # Windows Job Object: when Python dies (even forcefully), the job
        # terminates all member processes. No-op on other platforms.
        self._job = _WindowsJob()
        self._console_handler = None
        self._install_console_close_handler()

        # Fallback: kill llama-server if Python exits without a clean shutdown.
        atexit.register(self._atexit_kill)

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def status(self) -> ServerStatus:
        return self._status

    @property
    def uptime(self) -> float:
        if self._start_time and self._status == ServerStatus.RUNNING:
            return time.monotonic() - self._start_time
        return 0.0

    def is_running(self) -> bool:
        return self._status in (ServerStatus.RUNNING, ServerStatus.STARTING)

    def base_url(self) -> str:
        """HTTP base of the managed llama-server, reachable from this process."""
        info = self.connection_info()
        host = info["host"]
        if host == "0.0.0.0":
            host = "127.0.0.1"
        return f"http://{host}:{info['port']}"

    def connection_info(self) -> dict[str, Any]:
        """Return the bind settings used by the live process, if any.

        Settings edits do not affect an already-running child, so consumers
        must not advertise the newly saved host/port until the next start.
        """
        srv = self._config.get().server
        live = self.is_running() and self._active_host is not None
        return {
            "host": self._active_host if live else srv.effective_host(),
            "port": self._active_port if live else srv.port,
            "api_key_required": self._active_api_key if live else bool(srv.api_key),
            "pending_restart": live and (
                self._active_host != srv.effective_host()
                or self._active_port != srv.port
                or self._active_api_key != bool(srv.api_key)),
        }

    def get_status_dict(self) -> dict:
        return {
            "status": self._status.value,
            "uptime": self.uptime,
            "models": self._loaded_models,
            "pid": self._server_pid or (self._process.pid
                                         if self._process else None),
        }

    def build_cmd_preview(self) -> list[str] | None:
        exe = self._runtimes.get_executable()
        if not exe:
            return None
        return self._build_cmd(exe, self._paths.preset_ini, self._config.get())

    def start(self) -> dict[str, Any]:
        """Launch llama-server. Fast (no blocking waits) — callable from the
        UI thread. `reason` is a stable machine-readable code the UI maps to
        i18n strings; `error` is the human-readable detail."""
        with self._lock:
            if self._status not in (ServerStatus.STOPPED, ServerStatus.ERROR):
                return {"ok": False, "reason": "busy",
                        "error": f"Cannot start: server is {self._status.value}"}

            exe = self._runtimes.get_executable()
            if not exe:
                return {"ok": False, "reason": "no_runtime",
                        "error": "No valid runtime selected"}

            cfg = self._config.get()
            preset = self._paths.preset_ini

            profiles_by_model = self._profiles.by_model()
            has_routes = any(
                m.enabled and m.state == "valid"
                and any(p.active for p in profiles_by_model.get(m.id, []))
                for m in self._models.list())
            if not has_routes:
                return {"ok": False, "reason": "no_models",
                        "error": "No enabled model has an active profile"}

            try:
                write_preset(preset, self._models.list(), profiles_by_model,
                             cfg.global_params)
            except Exception as e:
                return {"ok": False, "reason": "preset_failed",
                        "error": f"Could not write models-preset.ini: {e}"}

            if port_in_use(cfg.server.effective_host(), cfg.server.port):
                return {"ok": False, "reason": "port_in_use",
                        "error": f"Port {cfg.server.port} is already in use"}

            cmd = self._build_cmd(exe, preset, cfg)
            self._active_host = cfg.server.effective_host()
            self._active_port = cfg.server.port
            self._active_api_key = bool(cfg.server.api_key)
            self._set_status(ServerStatus.STARTING)
            self._health_failures = 0
            self._loaded_models = []
            self._session += 1
            session = self._session

            # Use a fresh Job for every server session. The manager is built
            # well before autostart runs and its original Job may have been
            # closed by a console/lifecycle event in that interval. Creating
            # it immediately before Popen removes that race and gives this
            # exact child a dedicated lifetime container.
            if sys.platform == "win32":
                self._job.close()
                self._job = _WindowsJob()
                if not self._job._handle:
                    self._set_status(ServerStatus.ERROR)
                    return {
                        "ok": False,
                        "reason": "launch_failed",
                        "error": "Windows could not create a server Job Object",
                    }

            try:
                extra = self._popen_extra()
                launch_cmd = cmd
                life_read_fd = None
                if sys.platform != "win32":
                    life_read_fd, self._life_write_fd = os.pipe()
                    self._child_pidfile = self._paths.config_dir / (
                        f".{_LOCK_PREFIX}{self._instance_id}.child")
                    self._safe_unlink(self._child_pidfile)
                    supervisor = Path(__file__).with_name(
                        "_posix_supervisor.py")
                    if getattr(sys, "frozen", False):
                        launch_cmd = [sys.executable, "--posix-supervisor",
                                      "--fd", str(life_read_fd),
                                      str(self._child_pidfile), *cmd]
                    else:
                        launch_cmd = [sys.executable, str(supervisor), "--fd",
                                      str(life_read_fd),
                                      str(self._child_pidfile), *cmd]
                    extra["pass_fds"] = (life_read_fd,)
                self._process = subprocess.Popen(
                    launch_cmd, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=str(exe.parent), **extra)
                if life_read_fd is not None:
                    os.close(life_read_fd)
            except Exception as e:
                if sys.platform != "win32":
                    if 'life_read_fd' in locals() and life_read_fd is not None:
                        try:
                            os.close(life_read_fd)
                        except OSError:
                            pass
                    self._close_life_pipe()
                log.error("Failed to launch llama-server: %s", e)
                self._active_host = self._active_port = None
                self._active_api_key = False
                self._set_status(ServerStatus.ERROR)
                return {"ok": False, "reason": "launch_failed", "error": str(e)}

            # Place the child in a Job Object so a force-kill of Python
            # also terminates llama-server (Windows only). On POSIX this is a
            # no-op since the child is tracked only via the pid record.
            if sys.platform == "win32":
                try:
                    protected = self._job.assign(self._process._handle)
                    if protected and not self._job.contains(
                            self._process._handle):
                        log.debug("IsProcessInJob did not confirm the newly "
                                  "assigned suspended process (pid %d)",
                                  self._process.pid)
                except Exception:
                    protected = False
                if not protected and self._process.poll() is None:
                    # A malformed executable can exit in the few milliseconds
                    # between Popen and assignment. Give that already-failing
                    # process a brief chance to finish before classifying a
                    # still-live process as dangerously unsupervised.
                    try:
                        self._process.wait(timeout=0.25)
                    except subprocess.TimeoutExpired:
                        pass
                if not protected and self._process.poll() is None:
                    pid = self._process.pid
                    log.error("Refusing unprotected llama-server process "
                              "(pid %d): Windows Job assignment failed", pid)
                    self._kill_tree(pid)
                    try:
                        self._process.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                    if self._process.stdout is not None:
                        try:
                            self._process.stdout.close()
                        except OSError:
                            pass
                    self._process = None
                    self._set_status(ServerStatus.ERROR)
                    return {
                        "ok": False,
                        "reason": "launch_failed",
                        "error": "Windows could not supervise llama-server",
                    }
                if protected and not self._resume_windows_process(
                        self._process._handle):
                    pid = self._process.pid
                    self._job.terminate()
                    self._job.close()
                    try:
                        self._process.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        self._kill_tree(pid)
                    self._process = None
                    self._set_status(ServerStatus.ERROR)
                    return {
                        "ok": False,
                        "reason": "launch_failed",
                        "error": "Windows could not resume llama-server",
                    }

            if sys.platform != "win32":
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    try:
                        self._server_pid = int(
                            self._child_pidfile.read_text().strip())
                        break
                    except (OSError, ValueError):
                        if self._process.poll() is not None:
                            break
                        time.sleep(0.01)
                if self._server_pid is None:
                    self._close_life_pipe()
                    try:
                        self._process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._kill_tree(self._process.pid)
                    self._process = None
                    self._set_status(ServerStatus.ERROR)
                    return {"ok": False, "reason": "launch_failed",
                            "error": "POSIX supervisor failed to start"}

            self._server_pid = self._server_pid or self._process.pid
            self._update_lockfile(pid=self._server_pid)
            self._logs.log("server", "info",
                           "session start: " + " ".join(self._redact_cmd(cmd)))

            threading.Thread(target=self._read_logs, args=(session,),
                             daemon=True, name="server-logs").start()
            threading.Thread(target=self._health_loop, args=(session,),
                             daemon=True, name="server-health").start()

            log.info("llama-server started (pid %d)", self._server_pid)
            return {"ok": True, "pid": self._server_pid}

    def stop_async(self, timeout: int | None = None) -> None:
        """Terminate in a worker thread so the UI never blocks."""
        threading.Thread(target=self.stop, args=(timeout,), daemon=True,
                         name="server-stop").start()

    def stop(self, timeout: int | None = None) -> dict[str, Any]:
        with self._lock:
            if self._status in (ServerStatus.STOPPED, ServerStatus.STOPPING):
                return {"ok": False, "error": "Server is not running"}
            self._set_status(ServerStatus.STOPPING)
            proc = self._process
            timeout = (self._config.get().server.stop_timeout
                       if timeout is None else max(1, timeout))

        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                log.warning("llama-server did not stop in %ds — killing tree",
                            timeout)
                self._kill_tree(self._server_pid or proc.pid)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass

        self._cleanup()
        if self._lockfile:
            self._safe_unlink(self._lockfile)
        return {"ok": True}

    def restart_async(self) -> None:
        def work() -> None:
            if self._status not in (ServerStatus.STOPPED, ServerStatus.ERROR):
                self.stop()
            self.start()
        threading.Thread(target=work, daemon=True, name="server-restart").start()

    def reap_orphan(self) -> None:
        """Reap orphaned llama-server processes from dead instances.

        Each live instance writes a lockfile ``config/instance_{pid}.lock``.
        We scan all lockfiles, skip our own, and for every peer:
          - peer pid is **alive** → another instance is running, skip
          - peer pid is **dead**   → orphan, kill it

        Also cleans up stale lockfiles for pids that no longer exist.
        """
        # Ensure our own lockfile exists.
        self._ensure_lockfile()

        lock_dir = self._paths.config_dir
        my_lock = lock_dir / f"{_LOCK_PREFIX}{self._instance_id}{_LOCK_SUFFIX}"

        for lock_path in sorted(lock_dir.glob(f"{_LOCK_PREFIX}*{_LOCK_SUFFIX}")):
            if lock_path == my_lock:
                continue  # skip our own lock

            peer_data = self._read_lockfile(lock_path)
            if peer_data is None:
                self._safe_unlink(lock_path)
                continue

            peer_llama_pid = peer_data.get("pid_llama")
            peer_python_pid = peer_data.get("pid_python")

            # If the peer Python process is dead AND the llama-server pid is
            # also dead (or absent), it's an orphan — reap it.
            peer_python_alive = self._process_alive(peer_python_pid)
            peer_llama_alive = (
                peer_llama_pid is not None
                and self._pid_matches(peer_llama_pid, _SERVER_IMAGE)
            )

            if peer_python_alive is not False:
                # Alive OR unverifiable (for example access denied): never
                # risk killing a server that may belong to another instance.
                log.debug(
                    "Peer instance %s (python pid %d, llama pid %d), skipping",
                    "alive" if peer_python_alive else "unverifiable",
                    peer_python_pid, peer_llama_pid)
                continue

            if peer_llama_alive:
                # Peer Python died but llama-server is still running — orphan.
                log.warning(
                    "Reaping orphaned llama-server (pid %d) from dead instance "
                    "(python pid %d)", peer_llama_pid, peer_python_pid)
                self._kill_tree(peer_llama_pid)
                self._safe_unlink(lock_path)
            else:
                # Both dead — just clean up the stale lockfile.
                self._safe_unlink(lock_path)

    # ── Lockfile helpers ─────────────────────────────────────────────────────

    def _ensure_lockfile(self) -> None:
        """Write our own ``config/instance_{pid}.lock``."""
        lock_path = self._paths.config_dir / \
            f"{_LOCK_PREFIX}{self._instance_id}{_LOCK_SUFFIX}"
        try:
            lock_path.write_text(json.dumps({
                "pid_python": os.getpid(),
                "pid_llama": None,
            }))
            self._lockfile = lock_path
        except Exception:
            pass

    @staticmethod
    def _read_lockfile(path: Path) -> dict | None:
        """Parse a lockfile, return None on any error."""
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            return None

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    def _process_alive(pid: int) -> bool | None:
        """Return True if alive, False if confirmed dead, None if uncertain."""
        if pid is None:
            return False
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.windll.kernel32
                kernel32.OpenProcess.restype = wintypes.HANDLE
                process = kernel32.OpenProcess(
                    0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
                if not process:
                    # ERROR_INVALID_PARAMETER means the PID does not exist.
                    return False if kernel32.GetLastError() == 87 else None
                try:
                    code = wintypes.DWORD()
                    if not kernel32.GetExitCodeProcess(
                            process, ctypes.byref(code)):
                        return None
                    return code.value == 259  # STILL_ACTIVE
                finally:
                    kernel32.CloseHandle(process)
            except (OSError, ValueError, TypeError):
                return None
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return None
        except OSError:
            return False

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _popen_extra() -> dict:
        """Platform kwargs for the llama-server Popen.

        POSIX needs its own session: _kill_tree() kills the child's process
        group, and without setsid that group would be the app's own.
        """
        if sys.platform == "win32":
            return {"creationflags": subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | _CREATE_SUSPENDED
                    | _CREATE_BREAKAWAY_FROM_JOB}
        return {"start_new_session": True}

    @staticmethod
    def _resume_windows_process(process_handle: int) -> bool:
        """Resume a Popen child created with CREATE_SUSPENDED."""
        if sys.platform != "win32":
            return True
        try:
            import ctypes
            # Popen closes the primary thread handle, but NtResumeProcess
            # resumes every thread through the process handle it retains.
            status = ctypes.windll.ntdll.NtResumeProcess(process_handle)
            return status == 0
        except Exception:
            log.warning("Could not resume protected llama-server", exc_info=True)
            return False

    @staticmethod
    def _pid_matches(pid: int, exe_name: str) -> bool:
        """True when *pid* is alive and runs an image called *exe_name*."""
        try:
            if sys.platform == "win32":
                out = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW).stdout
                first = out.strip().splitlines()[0] if out.strip() else ""
                return first.startswith(f'"{exe_name}"'.lower()) or \
                    exe_name in first.lower()
            proc_exe = Path(f"/proc/{pid}/exe")
            if proc_exe.exists():
                return os.path.realpath(proc_exe).lower().endswith(exe_name)
            if sys.platform == "darwin":
                out = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "comm="],
                    capture_output=True, text=True, timeout=10).stdout.strip()
                return Path(out).name.lower() == exe_name.lower() if out \
                    else False
            # No /proc and not macOS: a bare alive-check would let us kill a
            # reused pid, so refuse to claim a match.
            return False
        except (OSError, subprocess.SubprocessError, IndexError):
            return False

    def _build_cmd(self, exe: Path, preset: Path, cfg) -> list[str]:
        srv = cfg.server
        cmd = [
            str(exe),
            "--models-preset", str(preset),
            "--host", srv.effective_host(),
            "--port", str(srv.port),
            "--models-max", str(srv.max_models),
            "--parallel", str(srv.parallel_slots),
            "--threads", str(srv.cpu_threads),
        ]
        if srv.api_key:
            cmd += ["--api-key", srv.api_key]
        if srv.metrics:
            cmd += ["--metrics"]
        if not srv.cont_batching:
            cmd += ["--no-cont-batching"]
        if srv.extra_args:
            cmd += shlex.split(srv.extra_args)
        return cmd

    @staticmethod
    def _redact_cmd(cmd: list[str]) -> list[str]:
        """Return a log-safe command line with credential values removed."""
        safe = list(cmd)
        for i, arg in enumerate(safe[:-1]):
            if arg == "--api-key":
                safe[i + 1] = "<redacted>"
        return safe

    def _update_lockfile(self, pid: int | None = None) -> None:
        """Refresh our lockfile with current llama-server pid (or None)."""
        if self._lockfile is None:
            return
        try:
            data = {"pid_python": os.getpid(), "pid_llama": pid}
            tmp = self._lockfile.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(self._lockfile)
        except Exception:
            pass

    def _set_status(self, status: ServerStatus) -> None:
        self._status = status
        self._events.publish("server_status", self.get_status_dict())

    def _read_logs(self, session: int) -> None:
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                clean = strip_ansi(raw.decode("utf-8", errors="replace").rstrip())
                if clean:
                    self._logs.log("server", parse_binary_level(clean), clean)
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdout.close()
            except (OSError, ValueError):
                pass

        # EOF: process died or was stopped
        if session != self._session:
            return   # a stop()/start() already took over
        if self._status in (ServerStatus.STARTING, ServerStatus.RUNNING):
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            rc = proc.returncode
            last = [e["message"] for e in
                    self._logs.get(limit=5, sources=["server"])]
            log.warning("llama-server exited unexpectedly (rc=%s)%s", rc,
                        "" if last else " — no output captured "
                        "(check runtime path and shared libraries)")
            for line in last:
                log.warning("  > %s", line)
            self._status = ServerStatus.ERROR
            self._events.publish("server_status", {
                **self.get_status_dict(),
                "error": f"Process exited (rc={rc})",
            })
            self._maybe_schedule_crash_restart()

    def _maybe_schedule_crash_restart(self) -> None:
        """If restart_on_crash is enabled, restart after a short delay —
        at most 3 crashes per 10 minutes, then give up (crashloop guard)."""
        if not self._config.get().server.restart_on_crash:
            return
        now = time.monotonic()
        self._crash_times = [ts for ts in self._crash_times if now - ts < 600]
        self._crash_times.append(now)
        n = len(self._crash_times)
        if n > 3:
            log.error("restart_on_crash: giving up after %d crashes "
                      "within 10 minutes", n)
            return
        delay = 3.0 * n
        log.warning("restart_on_crash: restarting llama-server in %.0fs "
                    "(crash %d/3)", delay, n)
        timer = threading.Timer(delay, self._delayed_restart)
        timer.daemon = True
        timer.start()

    def _delayed_restart(self) -> None:
        if self._status != ServerStatus.ERROR:
            return   # user already intervened
        self._cleanup()
        result = self.start()
        if not result.get("ok"):
            log.error("restart_on_crash: restart failed: %s",
                      result.get("error"))

    def _health_loop(self, session: int) -> None:
        base = self.base_url()
        time.sleep(2)   # give the process a moment to start

        while (session == self._session
               and self._status in (ServerStatus.STARTING, ServerStatus.RUNNING)):
            # A 200 from the port is meaningless if our child is dead —
            # another server could be answering. _read_logs handles state.
            proc = self._process
            if proc is None or proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"{base}/health",
                                            timeout=_HEALTH_TIMEOUT) as r:
                    healthy = r.status == 200
                if healthy:
                    if self._status == ServerStatus.STARTING:
                        # _start_time first: _set_status publishes a status
                        # dict whose `uptime` reads it. Publishing through
                        # _set_status (not just `server_health`) is what moves
                        # the UI off "starting" — every status label in the app
                        # subscribes to `server_status`, not `server_health`.
                        self._start_time = time.monotonic()
                        self._set_status(ServerStatus.RUNNING)
                        log.info("llama-server is healthy")
                    self._health_failures = 0
                    self._fetch_loaded_models(base)
                    self._events.publish("server_health", {
                        "status": self._status.value,
                        "uptime": self.uptime,
                        "models": self._loaded_models,
                    })
            except Exception:
                self._health_failures += 1
                if (self._health_failures >= _HEALTH_MAX_FAILURES
                        and self._status == ServerStatus.RUNNING):
                    log.warning("Health check failed 3 times — marking ERROR")
                    self._status = ServerStatus.ERROR
                    self._events.publish("server_status", {
                        **self.get_status_dict(),
                        "error": "Health check failed",
                    })
                    try:
                        proc.terminate()
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._kill_tree(self._server_pid or proc.pid)
                        try:
                            proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            pass
                    except OSError:
                        self._kill_tree(self._server_pid or proc.pid)
                    self._maybe_schedule_crash_restart()
                    return
            time.sleep(_HEALTH_INTERVAL)

    def _fetch_loaded_models(self, base: str) -> None:
        try:
            req = urllib.request.Request(f"{base}/v1/models")
            key = self._config.get().server.api_key
            if key:
                req.add_header("Authorization", f"Bearer {key}")
            with urllib.request.urlopen(req, timeout=_HEALTH_TIMEOUT) as r:
                if r.status == 200:
                    data = json.loads(r.read().decode("utf-8"))
                    self._loaded_models = [
                        m.get("id", "") for m in data.get("data", [])]
        except Exception:
            pass

    def _atexit_kill(self) -> None:
        """Synchronous last-resort kill run by Python's atexit.

        Note: on Windows the Job Object should handle force-kills before this
        runs, but we keep this as a fallback for clean exits and POSIX systems.
        """
        proc = self._process
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except OSError:
            pass

    def _install_console_close_handler(self) -> None:
        """Kill the managed child when Windows closes the hosting terminal.

        ``atexit`` and Tk's close protocol do not run for CTRL_CLOSE_EVENT.
        A Job Object is the primary protection, but assignment can be denied
        when the parent process is itself constrained by another job. This
        native handler closes our job and also terminates the direct child by
        handle, covering both cases within Windows' short shutdown window.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

            @handler_type
            def on_console_event(event: int) -> bool:
                # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT.
                if event not in (2, 5, 6):
                    return False
                # Explicit termination is stronger and easier to verify than
                # relying only on KILL_ON_JOB_CLOSE. llama-server may itself
                # host model workers, and every descendant must die here.
                self._job.terminate()
                self._job.close()
                proc = self._process
                if proc is not None:
                    try:
                        ctypes.windll.kernel32.TerminateProcess(
                            proc._handle, 1)
                    except Exception:
                        pass
                return True

            if ctypes.windll.kernel32.SetConsoleCtrlHandler(on_console_event,
                                                            True):
                # ctypes callbacks must stay strongly referenced.
                self._console_handler = on_console_event
            else:
                log.warning("SetConsoleCtrlHandler failed (code %d)",
                            ctypes.windll.kernel32.GetLastError())
        except Exception:
            log.warning("Could not install Windows console-close handler",
                        exc_info=True)

    def _cleanup(self) -> None:
        with self._lock:
            self._session += 1   # detach any reader/health threads
            self._process = None
            self._server_pid = None
            self._close_life_pipe()
            if self._child_pidfile:
                self._safe_unlink(self._child_pidfile)
                self._child_pidfile = None
            self._update_lockfile(pid=None)
            self._start_time = None
            self._loaded_models = []
            self._active_host = self._active_port = None
            self._active_api_key = False
            self._health_failures = 0
            self._status = ServerStatus.STOPPED
            self._events.publish("server_status", self.get_status_dict())

    def _close_life_pipe(self) -> None:
        if self._life_write_fd is not None:
            try:
                os.close(self._life_write_fd)
            except OSError:
                pass
            self._life_write_fd = None

    def _kill_tree(self, pid: int) -> None:
        """Kill *pid* and all its children (Windows tree-kill)."""
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.SubprocessError):
                pass
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass

    def shutdown(self) -> None:
        """Best-effort cleanup on app exit: close Job Object, remove lockfile."""
        if sys.platform == "win32" and self._console_handler is not None:
            try:
                import ctypes
                ctypes.windll.kernel32.SetConsoleCtrlHandler(
                    self._console_handler, False)
            except Exception:
                pass
            self._console_handler = None
        try:
            self._job.close()
        except Exception:
            pass
        if self._lockfile:
            self._safe_unlink(self._lockfile)
        # atexit will also fire _atexit_kill; this is a proactive attempt.
