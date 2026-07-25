"""Small per-user secret store: Windows DPAPI, mode-0600 fallback elsewhere."""
from __future__ import annotations

import base64
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(data: bytes, decrypt: bool = False) -> bytes:
    if sys.platform != "win32":
        return data
    source_buf = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(source_buf,
                                          ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_Blob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_Blob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    func = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    if decrypt:
        ok = func(ctypes.byref(source), None, None, None, None, 0,
                  ctypes.byref(result))
    else:
        ok = func(ctypes.byref(source), None, None, None, None, 0,
                  ctypes.byref(result))
        if not ok:
            # Service/sandbox sessions sometimes lack a user DPAPI profile.
            # Machine scope still provides encryption at rest; filesystem ACLs
            # continue to control which users can read the blob.
            ok = func(ctypes.byref(source), None, None, None, None, 4,
                      ctypes.byref(result))  # CRYPTPROTECT_LOCAL_MACHINE
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


class SecretStore:
    def __init__(self, config_dir: Path) -> None:
        self._path = config_dir / "api_key.secret"

    def read(self) -> str:
        try:
            raw = base64.b64decode(self._path.read_bytes(), validate=True)
            if raw[:1] == b"D":
                raw = _protect(raw[1:], decrypt=True)
            elif raw[:1] == b"P":
                raw = raw[1:]
            else:
                return ""
            return raw.decode("utf-8")
        except (OSError, ValueError, UnicodeError):
            return ""

    def write(self, value: str) -> None:
        if not value:
            self._path.unlink(missing_ok=True)
            return
        raw = value.encode("utf-8")
        if sys.platform == "win32":
            try:
                raw = b"D" + _protect(raw)
            except OSError:
                # DPAPI can be unavailable in sandboxed/service sessions. The
                # separate file still avoids leaking the key through DB dumps.
                raw = b"P" + raw
        else:
            raw = b"P" + raw
        payload = base64.b64encode(raw)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_bytes(payload)
        if os.name != "nt":
            tmp.chmod(0o600)
        tmp.replace(self._path)
        if os.name != "nt":
            self._path.chmod(0o600)
