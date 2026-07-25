"""Windows system-tray icon — pure ctypes, no assets, no dependencies.

`WinTray` runs a message-only window with its own WNDPROC on a daemon thread
and registers the bundled application icon with Shell_NotifyIconW.
Left click and the right-click menu's Restore publish ``tray_restore``;
Quit publishes ``tray_quit``. Both land on the EventBus, so the UI reacts on
the Tk thread via App._pump — no cross-thread widget calls here.

Windows-only: check :func:`is_supported` before constructing.
"""
from __future__ import annotations

import logging
import math
import sys
import threading

from llama_router.core.events import EventBus
from llama_router.core.paths import asset_path
from llama_router.i18n import t

log = logging.getLogger(__name__)

_WM_DESTROY = 0x0002
_WM_CLOSE = 0x0010
_WM_LBUTTONUP = 0x0202
_WM_RBUTTONUP = 0x0205
_WM_TRAY = 0x0400 + 1           # WM_USER + 1, our notify-icon callback
_WM_REFRESH_ICON = 0x0400 + 2
_WM_ANNOUNCE = 0x0400 + 3
_NIN_BALLOONUSERCLICK = 0x0405
_NIF_MESSAGE, _NIF_ICON, _NIF_TIP, _NIF_INFO = 0x1, 0x2, 0x4, 0x10
_NIF_SHOWTIP = 0x80
_NIM_ADD, _NIM_MODIFY, _NIM_DELETE, _NIM_SETVERSION = 0x0, 0x1, 0x2, 0x4
_NOTIFYICON_VERSION_4 = 4
_TPM_RIGHTBUTTON, _TPM_RETURNCMD, _TPM_NONOTIFY = 0x2, 0x100, 0x80
_MF_STRING, _MF_GRAYED, _MF_SEPARATOR = 0x0, 0x1, 0x800
_ID_RESTORE, _ID_START, _ID_STOP, _ID_RESTART, _ID_QUIT = 1, 2, 3, 4, 5
_IDI_APPLICATION = 32512
_HWND_MESSAGE = -3
_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x0010
_NIIF_USER = 0x00000004
_NIIF_NONE = 0x00000000


def is_supported() -> bool:
    return sys.platform == "win32"


class WinTray:
    """Owns the icon for the lifetime between show() and destroy()."""

    def __init__(self, events: EventBus, tooltip: str = "llama-router",
                 colors: dict[str, str] | None = None,
                 running: bool = False) -> None:
        if not is_supported():
            raise RuntimeError("WinTray is Windows-only")
        self._events = events
        self._tooltip = tooltip
        self._colors = dict(colors or {})
        self._status = "running" if running else "stopped"
        self._running = self._status == "running"
        self._hwnd = 0
        self._shown = False
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Public API (UI thread) ───────────────────────────────────────────────

    def show(self) -> bool:
        """Add the icon and return whether Explorer accepted it."""
        if self._thread and self._thread.is_alive():
            return self._shown
        self._shown = False
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="tray")
        self._thread.start()
        self._ready.wait(timeout=3)
        return self._shown

    def set_running(self, running: bool) -> None:
        """Refresh the tray mark when the managed server changes state."""
        self.set_server_status("running" if running else "stopped")

    def set_server_status(self, status: str) -> None:
        """Update icon and context-menu availability from server status."""
        status = getattr(status, "value", status)
        running = status == "running"
        if status == self._status:
            return
        self._status = status
        self._running = running
        if self._hwnd:
            import ctypes
            ctypes.windll.user32.PostMessageW(
                self._hwnd, _WM_REFRESH_ICON, 0, 0)

    def set_colors(self, colors: dict[str, str]) -> None:
        """Apply a new theme palette to the visible tray icon."""
        colors = dict(colors)
        if colors == self._colors:
            return
        self._colors = colors
        if self._hwnd:
            import ctypes
            ctypes.windll.user32.PostMessageW(
                self._hwnd, _WM_REFRESH_ICON, 0, 0)

    def announce_hidden(self) -> None:
        """Tell the user the app moved to the tray."""
        if self._hwnd:
            import ctypes
            ctypes.windll.user32.PostMessageW(self._hwnd, _WM_ANNOUNCE, 0, 0)

    def destroy(self) -> None:
        """Remove the icon and stop the message loop."""
        if self._hwnd:
            import ctypes
            ctypes.windll.user32.PostMessageW(self._hwnd, _WM_CLOSE, 0, 0)
        self._hwnd = 0
        self._shown = False

    # ── Message-loop thread ──────────────────────────────────────────────────

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD),
                        ("hWnd", wintypes.HWND),
                        ("uID", wintypes.UINT),
                        ("uFlags", wintypes.UINT),
                        ("uCallbackMessage", wintypes.UINT),
                        ("hIcon", wintypes.HICON),
                        ("szTip", wintypes.WCHAR * 128),
                        ("dwState", wintypes.DWORD),
                        ("dwStateMask", wintypes.DWORD),
                        ("szInfo", wintypes.WCHAR * 256),
                        ("uVersion", wintypes.UINT),
                        ("szInfoTitle", wintypes.WCHAR * 64),
                        ("dwInfoFlags", wintypes.DWORD),
                        ("guidItem", ctypes.c_byte * 16),
                        ("hBalloonIcon", wintypes.HICON)]

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                     wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM)
        # Handles are 64-bit: without explicit prototypes ctypes truncates
        # them to c_int and CreateWindowExW gets a garbage parent handle.
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE,
            wintypes.LPVOID]
        user32.LoadIconW.restype = wintypes.HICON
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR,
                                            wintypes.HINSTANCE]
        ctypes.windll.kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        nid = NOTIFYICONDATAW()
        owned_icon = 0
        balloon_icon = 0

        def refresh_icon() -> None:
            nonlocal owned_icon
            new_icon = self._load_icon()
            old_icon = owned_icon
            old_flags = nid.uFlags
            if new_icon:
                nid.hIcon = new_icon
            nid.szTip = self._tooltip_text()
            nid.uFlags = _NIF_TIP | _NIF_SHOWTIP | (
                _NIF_ICON if new_icon else 0)
            if shell32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(nid)):
                if new_icon:
                    owned_icon = new_icon
                if new_icon and old_icon:
                    user32.DestroyIcon(old_icon)
            elif new_icon:
                user32.DestroyIcon(new_icon)
                nid.hIcon = old_icon
            nid.uFlags = old_flags

        def announce_hidden() -> None:
            nonlocal balloon_icon
            if balloon_icon:
                user32.DestroyIcon(balloon_icon)
            balloon_icon = self._load_bundled_icon(48)
            nid.uFlags = _NIF_INFO
            nid.szInfoTitle = "llama-router"
            nid.szInfo = t("Still running here — click to restore.")
            nid.dwInfoFlags = _NIIF_USER if balloon_icon else _NIIF_NONE
            nid.hBalloonIcon = balloon_icon or None
            announced = bool(
                shell32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(nid)))
            if not announced:
                nid.dwInfoFlags = _NIIF_NONE
                nid.hBalloonIcon = None
                announced = bool(
                    shell32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(nid)))
            if not announced:
                log.warning("tray: Explorer rejected the notification balloon")
            nid.uFlags = (_NIF_MESSAGE | _NIF_ICON | _NIF_TIP
                          | _NIF_SHOWTIP)

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == _WM_TRAY:
                event = lparam & 0xFFFF
                if event in (_WM_LBUTTONUP, _NIN_BALLOONUSERCLICK):
                    self._events.publish("tray_restore", {})
                elif event == _WM_RBUTTONUP:
                    self._popup(hwnd)
            elif msg == _WM_REFRESH_ICON:
                refresh_icon()
                return 0
            elif msg == _WM_ANNOUNCE:
                announce_hidden()
                return 0
            elif msg == _WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            elif msg == _WM_DESTROY:
                shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(nid))
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc = WNDPROC(wndproc)   # keep a reference or the callback is GC'd
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT),
                        ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE),
                        ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR)]

        cls = WNDCLASSW()
        cls.lpfnWndProc = proc
        cls.hInstance = hinst
        cls.lpszClassName = f"llama-router-tray-{id(self)}"
        if not user32.RegisterClassW(ctypes.byref(cls)):
            log.warning("tray: RegisterClassW failed")
            self._ready.set()
            return

        hwnd = user32.CreateWindowExW(0, cls.lpszClassName, None, 0, 0, 0,
                                      0, 0, wintypes.HWND(_HWND_MESSAGE),
                                      None, hinst, None)
        if not hwnd:
            log.warning("tray: CreateWindowExW failed")
            user32.UnregisterClassW(cls.lpszClassName, hinst)
            self._ready.set()
            return
        self._hwnd = hwnd

        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = (_NIF_MESSAGE | _NIF_ICON | _NIF_TIP
                      | _NIF_SHOWTIP)
        nid.uCallbackMessage = _WM_TRAY
        owned_icon = self._load_icon()
        nid.hIcon = (owned_icon
                     or user32.LoadIconW(None,
                                         wintypes.LPCWSTR(_IDI_APPLICATION)))
        nid.szTip = self._tooltip_text()
        self._shown = bool(
            shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(nid)))
        self._ready.set()
        if not self._shown:
            log.error("tray: Shell_NotifyIconW(NIM_ADD) failed; keeping window visible")
            user32.DestroyWindow(hwnd)
            self._hwnd = 0
            user32.UnregisterClassW(cls.lpszClassName, hinst)
            if owned_icon:
                user32.DestroyIcon(owned_icon)
            return

        # Opt in to current callback/notification behaviour before announcing.
        nid.uVersion = _NOTIFYICON_VERSION_4
        shell32.Shell_NotifyIconW(_NIM_SETVERSION, ctypes.byref(nid))

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self._hwnd = 0
        self._shown = False
        user32.UnregisterClassW(cls.lpszClassName, hinst)
        # Loaded/generated icons are caller-owned. The stock fallback is shared
        # and must not be destroyed, so release only our owned icon.
        if owned_icon:
            user32.DestroyIcon(owned_icon)
        if balloon_icon:
            user32.DestroyIcon(balloon_icon)

    def _tooltip_text(self) -> str:
        """Return the Windows hover text, including the current server state."""
        return f"{self._tooltip} status: {self._status}"[:127]

    def _load_icon(self):
        """Build the themed icon, falling back to the bundled ICO."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.DestroyIcon.argtypes = [wintypes.HICON]
        user32.DestroyIcon.restype = wintypes.BOOL
        icon = self._make_icon(self._colors, self._running)
        if icon:
            return icon

        return self._load_bundled_icon(32)

    @staticmethod
    def _load_bundled_icon(size: int):
        """Load a caller-owned resolution from the packaged multi-size ICO."""
        import ctypes
        from ctypes import wintypes

        path = asset_path("app_icon.ico")
        if not path.exists():
            return None
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HICON
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT]
        return user32.LoadImageW(
            None, str(path), _IMAGE_ICON, size, size, _LR_LOADFROMFILE)

    @staticmethod
    def _make_icon(colors: dict[str, str], running: bool = False):
        """Build a theme-coloured llama + Wi-Fi HICON without dependencies."""
        import ctypes

        n = 32
        xor = bytearray(n * n * 4)
        and_ = bytearray(b"\xff" * (n * n // 8))

        def rgb(key: str, fallback: str) -> tuple[int, int, int]:
            value = colors.get(key, fallback).lstrip("#")
            return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))

        border = rgb("border", "#223445")
        accent = rgb("accent", "#3fd7e6")
        accent_hi = rgb("accent_hi", "#74e8f4")

        def put(x: int, y: int, color: tuple[int, int, int]) -> None:
            if 0 <= x < n and 0 <= y < n:
                i = (y * n + x) * 4
                r, g, b = color
                xor[i:i + 4] = bytes((b, g, r, 255))
                and_[y * (n // 8) + x // 8] &= ~(1 << (7 - x % 8))

        def clear(x: int, y: int) -> None:
            if 0 <= x < n and 0 <= y < n:
                i = (y * n + x) * 4
                xor[i:i + 4] = b"\x00\x00\x00\x00"
                and_[y * (n // 8) + x // 8] |= 1 << (7 - x % 8)

        def inside_poly(x: float, y: float,
                        points: tuple[tuple[float, float], ...]) -> bool:
            hit = False
            j = len(points) - 1
            for i, (xi, yi) in enumerate(points):
                xj, yj = points[j]
                if ((yi > y) != (yj > y)
                        and x < (xj - xi) * (y - yi) / (yj - yi) + xi):
                    hit = not hit
                j = i
            return hit

        # Original artwork bounds are roughly 330x350. Fit those bounds into
        # 30x30 so the glyph is as large and legible as a tray icon allows.
        scale = 30 / 350

        def tx(x: float) -> float:
            return 1.8 + (x - 125) * scale

        def ty(y: float) -> float:
            return 1 + (y - 75) * scale

        def scaled(points):
            return tuple((tx(x), ty(y)) for x, y in points)

        ears = (
            scaled(((145, 175), (165, 255), (135, 255))),
            scaled(((205, 165), (225, 245), (195, 245))),
        )
        head = scaled(((135, 255), (250, 245), (310, 295), (310, 335),
                       (235, 335), (235, 415), (165, 415), (165, 305),
                       (135, 285)))

        for y in range(n):
            for x in range(n):
                px, py = x + 0.5, y + 0.5
                if inside_poly(px, py, head) or any(
                        inside_poly(px, py, ear) for ear in ears):
                    put(x, y, accent)

        # Eye cut-out and emitter.
        for y in range(n):
            for x in range(n):
                if ((x - tx(252.5)) ** 2 + (y - ty(277.5)) ** 2
                        <= max(0.8, 7.5 * scale) ** 2):
                    clear(x, y)
                if ((x - tx(291)) ** 2 + (y - ty(241)) ** 2
                        <= max(0.8, 6 * scale) ** 2):
                    put(x, y, accent_hi)
                dx = x + 0.5 - tx(280)
                dy = ty(250) - (y + 0.5)
                angle = math.degrees(math.atan2(dy, dx))
                distance = math.hypot(dx, dy)
                if 2 <= angle <= 82 and any(
                        abs(distance - radius * scale) <= 0.6
                        for radius in (50, 90, 130, 170)):
                    put(x, y, accent_hi if running else border)

        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.CreateIcon.restype = wintypes.HICON
        hicon = user32.CreateIcon(None, n, n, 1, 32,
                                  bytes(and_), bytes(xor))
        return hicon or None

    def _popup(self, hwnd) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, _MF_STRING, _ID_RESTORE, t("Restore"))
        user32.AppendMenuW(menu, _MF_SEPARATOR, 0, None)
        busy = self._status in ("starting", "stopping")
        can_start = self._status in ("stopped", "error")
        can_stop = self._status in ("starting", "running", "error")
        can_restart = not busy
        user32.AppendMenuW(menu, _MF_STRING if can_start else _MF_GRAYED,
                           _ID_START, t("Start server"))
        user32.AppendMenuW(menu, _MF_STRING if can_stop else _MF_GRAYED,
                           _ID_STOP, t("Stop"))
        user32.AppendMenuW(menu, _MF_STRING if can_restart else _MF_GRAYED,
                           _ID_RESTART, t("Restart"))
        user32.AppendMenuW(menu, _MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, _MF_STRING, _ID_QUIT, t("Quit"))
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # Required by TrackPopupMenu so the menu closes on an outside click.
        user32.SetForegroundWindow(hwnd)
        choice = user32.TrackPopupMenu(
            menu, _TPM_RIGHTBUTTON | _TPM_RETURNCMD | _TPM_NONOTIFY,
            pt.x, pt.y, 0, hwnd, None)
        user32.DestroyMenu(menu)
        if choice == _ID_RESTORE:
            self._events.publish("tray_restore", {})
        elif choice == _ID_START:
            self._events.publish("tray_start", {})
        elif choice == _ID_STOP:
            self._events.publish("tray_stop", {})
        elif choice == _ID_RESTART:
            self._events.publish("tray_restart", {})
        elif choice == _ID_QUIT:
            self._events.publish("tray_quit", {})
