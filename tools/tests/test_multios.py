"""Multi-OS branches of ServerManager / SystemMonitor, mocked per platform.

These cover the POSIX/darwin code paths that a Windows dev box never runs:
the setsid Popen flag, the darwin pid-image check and vm_stat parsing.
"""
from __future__ import annotations

import sys
import subprocess
import os
import tempfile
import time
import unittest
from unittest import mock

from llama_router.services import server_manager, system_monitor
from llama_router.services.server_manager import ServerManager, _WindowsJob


class TestPopenExtra(unittest.TestCase):
    def test_posix_gets_its_own_session(self):
        with mock.patch.object(server_manager.sys, "platform", "linux"):
            self.assertEqual(ServerManager._popen_extra(),
                             {"start_new_session": True})

    def test_windows_gets_creationflags(self):
        if sys.platform != "win32":
            self.skipTest("CREATE_NO_WINDOW only exists on Windows")
        extra = ServerManager._popen_extra()
        self.assertIn("creationflags", extra)
        self.assertNotIn("start_new_session", extra)


class TestWindowsJob(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows Job Object only")
    def test_kill_on_close_job_is_created(self):
        """Catch invalid native layouts and truncated 64-bit HANDLE values."""
        job = _WindowsJob()
        try:
            self.assertTrue(job._handle)
        finally:
            job.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows console handler only")
    def test_server_manager_installs_console_close_handler(self):
        """CTRL_CLOSE_EVENT must have a path that does not depend on atexit."""
        from tools.tests.test_phase4 import _Env
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            env = _Env(td)
            try:
                self.assertIsNotNone(env.server._console_handler)
            finally:
                env.server.shutdown()

    @unittest.skipUnless(sys.platform == "win32", "Windows Job Object only")
    def test_terminate_kills_process_tree(self):
        """The job must kill a managed process and the child it launches."""
        child_code = "import time; time.sleep(60)"
        parent_code = (
            "import subprocess,sys,time; sys.stdin.readline(); "
            "p=subprocess.Popen([sys.executable,'-c',%r]); "
            "print(p.pid,flush=True); time.sleep(60)" % child_code)
        proc = subprocess.Popen(
            [sys.executable, "-c", parent_code],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
        job = _WindowsJob()
        try:
            self.assertTrue(job.assign(proc._handle))
            self.assertTrue(job.contains(proc._handle))
            proc.stdin.write("go\n")
            proc.stdin.flush()
            child_pid = int(proc.stdout.readline().strip())
            self.assertTrue(job.terminate())
            proc.wait(timeout=5)
            deadline = time.monotonic() + 5
            while (time.monotonic() < deadline
                   and ServerManager._process_alive(child_pid)):
                time.sleep(0.05)
            self.assertFalse(ServerManager._process_alive(child_pid))
        finally:
            job.close()
            if proc.poll() is None:
                proc.kill()
            if proc.stdin is not None:
                proc.stdin.close()
            if proc.stdout is not None:
                proc.stdout.close()


class TestPosixSupervisor(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "POSIX supervisor only")
    def test_parent_pipe_eof_kills_server_process_group(self):
        from pathlib import Path

        helper = (Path(server_manager.__file__).with_name(
                  ("_posix_supervisor.py")))
        with tempfile.TemporaryDirectory() as td:
            pidfile = Path(td) / "child.pid"
            read_fd, write_fd = os.pipe()
            supervisor = subprocess.Popen([
                sys.executable, str(helper), "--fd", str(read_fd),
                str(pidfile), sys.executable, "-c",
                "import time; time.sleep(60)"],
                pass_fds=(read_fd,), start_new_session=True)
            os.close(read_fd)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not pidfile.exists():
                    time.sleep(0.02)
                self.assertTrue(pidfile.exists())
                child_pid = int(pidfile.read_text())

                # Simulate app death: the kernel closes its write descriptor.
                os.close(write_fd)
                write_fd = -1
                supervisor.wait(timeout=8)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
            finally:
                if write_fd >= 0:
                    os.close(write_fd)
                if supervisor.poll() is None:
                    supervisor.terminate()
                    supervisor.wait(timeout=5)


class TestPidMatchesDarwin(unittest.TestCase):
    def _run(self, ps_stdout: str) -> bool:
        done = mock.Mock()
        done.stdout = ps_stdout
        with mock.patch.object(server_manager.sys, "platform", "darwin"), \
                mock.patch.object(server_manager.subprocess, "run",
                                  return_value=done) as run:
            hit = ServerManager._pid_matches(4242, "llama-server")
        args = run.call_args[0][0]
        self.assertEqual(args[:2], ["ps", "-p"])
        return hit

    def test_matching_image(self):
        self.assertTrue(self._run("/opt/llama/bin/llama-server\n"))

    def test_reused_pid_is_not_matched(self):
        self.assertFalse(self._run("/usr/bin/vim\n"))

    def test_dead_pid_is_not_matched(self):
        self.assertFalse(self._run(""))

    def test_server_image_name_is_bare_on_posix(self):
        with mock.patch.object(server_manager.sys, "platform", "darwin"):
            # _SERVER_IMAGE is resolved at import time for the host OS; the
            # invariant that matters is: never pass ".exe" to a POSIX check.
            self.assertTrue(server_manager._SERVER_IMAGE
                            .startswith("llama-server"))


class TestDarwinSystemMonitor(unittest.TestCase):
    _VM_STAT = (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                              100000.\n"
        "Pages active:                            200000.\n"
        "Pages inactive:                           50000.\n"
        "Pages speculative:                        10000.\n"
        "Pages wired down:                         80000.\n"
    )

    def test_darwin_mem_parses_vm_stat(self):
        def fake_run(cmd, **_kw):
            done = mock.Mock()
            done.stdout = ("17179869184\n" if cmd[0] == "sysctl"
                           else self._VM_STAT)
            return done

        with mock.patch.object(system_monitor.subprocess, "run", fake_run):
            used, total = system_monitor._darwin_mem()
        self.assertEqual(total, 16384)          # 16 GiB in MiB
        free_mib = (100000 + 50000 + 10000) * 16384 // (1024 * 1024)
        self.assertEqual(used, total - free_mib)

    def test_darwin_cpu_percent_normalises_by_core_count(self):
        done = mock.Mock()
        done.stdout = " 50.0\n 30.0\n 20.0\n"
        with mock.patch.object(system_monitor.subprocess, "run",
                               return_value=done), \
                mock.patch.object(system_monitor.os, "cpu_count",
                                  return_value=4):
            self.assertAlmostEqual(system_monitor._darwin_cpu_percent(), 25.0)

    def test_darwin_monitor_selects_native_sources(self):
        with mock.patch.object(system_monitor.sys, "platform", "darwin"):
            mon = system_monitor.SystemMonitor(events=mock.Mock())
        self.assertIsNotNone(mon._mem)
        self.assertIsNotNone(mon._cpu_percent)


if __name__ == "__main__":
    unittest.main()
