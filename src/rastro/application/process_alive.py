"""Is a process id alive? Never signals on Windows, where signal 0 is `CTRL_C_EVENT`."""

from __future__ import annotations

import errno
import os
from typing import Any, Protocol, cast

_IS_WINDOWS = os.name == "nt"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5


class Kernel32(Protocol):
    def open(self, pid: int) -> tuple[int | None, int]:
        """`(handle, 0)`, or `(None, last_error)` when the process cannot be opened."""

    def exit_code(self, handle: int) -> int | None: ...

    def close(self, handle: int) -> None: ...


class _CtypesKernel32:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes, self._wintypes = ctypes, wintypes
        self._dll = dll = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
        dll.OpenProcess.restype = wintypes.HANDLE
        dll.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        dll.GetExitCodeProcess.restype = wintypes.BOOL
        dll.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        dll.CloseHandle.argtypes = [wintypes.HANDLE]

    def open(self, pid: int) -> tuple[int | None, int]:
        handle = self._dll.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        return (handle, 0) if handle else (None, cast(Any, self._ctypes).get_last_error())

    def exit_code(self, handle: int) -> int | None:
        code = self._wintypes.DWORD()
        ok = self._dll.GetExitCodeProcess(handle, self._ctypes.byref(code))
        return int(code.value) if ok else None

    def close(self, handle: int) -> None:
        self._dll.CloseHandle(handle)


def _kernel32() -> Kernel32:
    return _CtypesKernel32()


def _windows_alive(pid: int) -> bool:
    api = _kernel32()
    handle, error = api.open(pid)
    if handle is None:
        return error == ERROR_ACCESS_DENIED  # exists, but belongs to someone else
    try:
        code = api.exit_code(handle)
    finally:
        api.close(handle)
    return code is None or code == STILL_ACTIVE


def _posix_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as error:
        return error.errno != errno.ESRCH  # EPERM: alive, owned by another user
    return True


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    return _windows_alive(pid) if _IS_WINDOWS else _posix_alive(pid)
