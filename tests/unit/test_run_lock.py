"""Lock takeover and release races."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from rastro.application import run_lock
from rastro.application.errors import RunInProgressError
from rastro.application.run_lock import dataset_lock, lock_path

T0 = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def _content(db: Path) -> dict[str, Any]:
    return json.loads(lock_path(db).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture
def stack() -> Iterator[ExitStack]:
    with ExitStack() as holder:
        yield holder


def test_lock_holds_pid_and_random_token(tmp_path: Path) -> None:
    db = tmp_path / "items.sqlite"
    with dataset_lock(db, clock=lambda: T0):
        first = _content(db)
    with dataset_lock(db, clock=lambda: T0):
        second = _content(db)
    assert {"pid", "token", "started_at"} <= first.keys()
    assert len(first["token"]) >= 16 and first["token"] != second["token"]


def test_two_starters_judging_one_lock_stale_cannot_both_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stack: ExitStack
) -> None:
    """A rival takes the stale lock between our judgement and our takeover."""
    db = tmp_path / "items.sqlite"
    lock_path(db).write_text('{"pid": 99999999, "started_at": "2020-01-01T00:00:00+00:00"}')
    real: Callable[..., str | None] = run_lock._stale_reason
    rivals: list[None] = []

    def judged_then_rival_wins(*args: Any, **kwargs: Any) -> str | None:
        reason = real(*args, **kwargs)
        if not rivals:
            rivals.append(None)
            stack.enter_context(dataset_lock(db, clock=lambda: T0, is_pid_alive=lambda _: False))
        return reason

    monkeypatch.setattr(run_lock, "_stale_reason", judged_then_rival_wins)
    with (
        pytest.raises(RunInProgressError),
        dataset_lock(db, clock=lambda: T0, is_pid_alive=lambda _: False),
    ):
        pytest.fail("the second starter must not run")
    assert "token" in _content(db)  # the rival's lock is still in place
    stack.close()
    assert not lock_path(db).exists()


def test_expired_holder_release_leaves_its_successors_lock(
    tmp_path: Path, stack: ExitStack
) -> None:
    """An expired-but-running holder must not delete the lock its successor took over."""
    db = tmp_path / "items.sqlite"
    with dataset_lock(db, timeout_seconds=60.0, clock=lambda: T0):
        successor = stack.enter_context(
            dataset_lock(db, timeout_seconds=60.0, clock=lambda: T0 + timedelta(hours=3))
        )
        taken = lock_path(db).read_text()
    assert successor == lock_path(db)
    assert lock_path(db).read_text() == taken
    stack.close()
    assert not lock_path(db).exists()
