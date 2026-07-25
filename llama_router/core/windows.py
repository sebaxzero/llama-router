"""Small Windows integration helpers; every function is a no-op elsewhere."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

APP_USER_MODEL_ID = "LlamaRouter.Desktop"


def configure_app_identity(icon_path: Path) -> None:
    """Give hosted/script runs their own Windows name and notification icon.

    Frozen builds already have executable metadata, but using the same AUMID
    keeps taskbar and notification attribution stable in both modes.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        shell32 = ctypes.windll.shell32
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [
            ctypes.c_wchar_p]
        shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        result = shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID)
        if result != 0:
            log.debug("SetCurrentProcessExplicitAppUserModelID failed: %#x",
                      result)
    except Exception:
        log.debug("Could not set Windows AppUserModelID", exc_info=True)

    # HKCU is sufficient and does not require elevation. Explorer consults
    # this metadata when a hosted process (python.exe) emits a notification.
    try:
        import winreg
        key_path = rf"Software\Classes\AppUserModelId\{APP_USER_MODEL_ID}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_EXPAND_SZ,
                              "llama-router")
            registered_icon = (Path(sys.executable) if getattr(
                sys, "frozen", False) else icon_path.resolve())
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_EXPAND_SZ,
                              str(registered_icon))
            winreg.SetValueEx(key, "IconBackgroundColor", 0, winreg.REG_SZ,
                              "FF0A1016")
    except OSError:
        log.debug("Could not register Windows app identity", exc_info=True)
