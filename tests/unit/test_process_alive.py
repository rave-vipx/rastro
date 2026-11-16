"""PID liveness never signals a process on Windows (SCHED-003)."""

from __future__ import annotations

import errno
import os

import pytest

from rastro.application import process_alive


class FakeKernel32:
    """Stands in for kernel32: `handle is None` means OpenProcess failed with `error`."""

    def __init__(self, *, handle: int | None, error: int = 0, exit_code: int | None = None):
        self.handle, self.error, self.code = handle, error, exit_code
        self.closed: list[int] = []

    def open(self, pid: int) -> tuple[int | None, int]:
        return self.handle, self.error

    def exit_code(self, handle: int) -> int | None:
        return self.code

    def close(self, handle: int) -> None:
        self.closed.append(handle)


def _never_signal(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    calls: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        raise AssertionError("tests must never send a signal")

    monkeypatch.setattr(os, "kill", kill)
    return calls


@pytest.mark.parametrize(
    ("code", "alive"),
    [(process_alive.STILL_ACTIVE, True), (0, False), (1, False), (None, True)],
)
def test_windows_uses_exit_code_and_never_calls_os_kill(
    monkeypatch: pytest.MonkeyPatch, code: int | None, alive: bool
) -> None:
    calls = _never_signal(monkeypatch)
    api = FakeKernel32(handle=7, exit_code=code)
    monkeypatch.setattr(process_alive, "_IS_WINDOWS", True)
    monkeypatch.setattr(process_alive, "_kernel32", lambda: api)
    assert process_alive.pid_alive(4321) is alive
    assert calls == []
    assert api.closed == [7]


@pytest.mark.parametrize(
    ("error", "alive"),
    [(process_alive.ERROR_ACCESS_DENIED, True), (87, False), (0, False)],
)
def test_windows_open_failure_access_denied_is_alive(
    monkeypatch: pytest.MonkeyPatch, error: int, alive: bool
) -> None:
    calls = _never_signal(monkeypatch)
    api = FakeKernel32(handle=None, error=error)
    monkeypatch.setattr(process_alive, "_IS_WINDOWS", True)
    monkeypatch.setattr(process_alive, "_kernel32", lambda: api)
    assert process_alive.pid_alive(4321) is alive
    assert calls == [] and api.closed == []


def test_nonpositive_pid_is_dead_on_both_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _never_signal(monkeypatch)
    for windows in (True, False):
        monkeypatch.setattr(process_alive, "_IS_WINDOWS", windows)
        assert process_alive.pid_alive(0) is False
        assert process_alive.pid_alive(-5) is False
    assert calls == []


@pytest.mark.parametrize(
    ("raised", "alive"),
    [
        (None, True),
        (ProcessLookupError(errno.ESRCH, "no such process"), False),
        (PermissionError(errno.EPERM, "not permitted"), True),
        (OSError(errno.ESRCH, "gone"), False),
    ],
)
def test_posix_probes_with_signal_zero(
    monkeypatch: pytest.MonkeyPatch, raised: OSError | None, alive: bool
) -> None:
    seen: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        seen.append((pid, sig))
        if raised is not None:
            raise raised

    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(process_alive, "_IS_WINDOWS", False)
    assert process_alive.pid_alive(4321) is alive
    assert seen == [(4321, 0)]
