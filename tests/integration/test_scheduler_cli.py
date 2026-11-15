"""Integration tests for `rastro job watch`, locks, and recovery (SCHED-001..007, SYNC-001)."""

from __future__ import annotations

import json
import os
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import rastro
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.run_lock import checkpoint_path, dataset_lock, lock_path
from rastro.application.scheduler import watch_job
from rastro.cli import main
from rastro.domain.jobs import Job


def _write_fixture(
    tmp_path: Path, *, include_schedule: bool = True, include_dataset: bool = True
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "a.html").write_text('<div class="item"><h2>Alpha</h2></div>', encoding="utf-8")
    (tmp_path / "b.html").write_text('<div class="item"><h2>Beta</h2></div>', encoding="utf-8")
    lines = [
        "format_version = 1",
        "",
        "[recipe]",
        'strategy = "css"',
        'record_selector = ".item"',
        "[recipe.fields]",
        'title = "h2::text"',
        "",
        "[[fields]]",
        'name = "title"',
        'type = "string"',
        "",
        "[[sources]]",
        'path = "a.html"',
        'url = "https://a.example/items"',
        "",
        "[[sources]]",
        'path = "b.html"',
        'url = "https://b.example/items"',
        'every = "1h"',
    ]
    if include_dataset:
        lines.extend(
            [
                "",
                "[dataset]",
                'name = "items"',
                'path = "items.sqlite"',
                'identity = ["title"]',
            ]
        )
    if include_schedule:
        lines.extend(
            [
                "",
                "[schedule]",
                'every = "6h"',
                'jitter = "5m"',
            ]
        )
    job_file = tmp_path / "job.toml"
    job_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return job_file


def test_watch_without_dataset_or_schedule_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SCHED-002: watch requires [schedule] and [dataset]; otherwise ConfigurationError (exit 2)."""
    no_dataset = _write_fixture(tmp_path / "no_ds", include_dataset=False)
    assert main(["job", "watch", str(no_dataset), "--once"]) == 2
    err = json.loads(capsys.readouterr().err)
    assert err["error"] == "configuration_error"

    no_schedule = _write_fixture(tmp_path / "no_sched", include_schedule=False)
    assert main(["job", "watch", str(no_schedule), "--once"]) == 2
    err = json.loads(capsys.readouterr().err)
    assert err["error"] == "configuration_error"


def test_watch_once_exit_code_passthrough(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SCHED-002, SCHED-007: --once runs one tick and passes through the load exit code."""
    job_file = _write_fixture(tmp_path)
    assert main(["job", "watch", str(job_file), "--once"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert len(payload["sources"]) == 2

    # Inject a fake load runner returning exit code 3 (PARTIAL_SUCCESS).
    t0 = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    code = watch_job(
        job_file,
        once=True,
        clock=lambda: t0 + timedelta(hours=7),
        sleep=lambda _: None,
        load_runner=lambda _file, _job: 3,
    )
    assert code == 3


def test_watch_loop_runs_only_due_sources_and_sleeps_with_deterministic_jitter(
    tmp_path: Path,
) -> None:
    """SCHED-001, SCHED-002, SCHED-006, SYNC-001: ticks run due sources and sleep with jitter."""
    job_file = _write_fixture(tmp_path)
    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    slept: list[float] = []
    ran_urls: list[tuple[str, ...]] = []

    def fake_clock() -> datetime:
        return now

    def fake_sleep(seconds: float) -> None:
        nonlocal now
        slept.append(seconds)
        now = now + timedelta(seconds=seconds)

    def fake_runner(_path: Path, active_job: Job) -> int:
        urls = tuple(
            s.resolved_source_url() if hasattr(s, "resolved_source_url") else s.url
            for s in active_job.sources
        )
        ran_urls.append(urls)
        return 0

    exit_code = watch_job(
        job_file,
        clock=fake_clock,
        sleep=fake_sleep,
        load_runner=fake_runner,
        max_ticks=2,
    )
    assert exit_code == 0
    # Tick 1: both never-attempted sources run.
    assert ran_urls[0] == ("https://a.example/items", "https://b.example/items")
    # Sleep after tick 1 is 1h + jitter(<=5m) for source 2.
    assert len(slept) == 1
    assert 3600.0 <= slept[0] <= 3900.0
    # Tick 2: only the 1h source is due.
    assert ran_urls[1] == ("https://b.example/items",)


def test_lock_held_exits_4_run_in_progress_and_stale_lock_replaced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """SCHED-003: live lock -> exit 4 run_in_progress; stale lock -> replaced with WARNING."""
    job_file = _write_fixture(tmp_path)
    db_path = tmp_path / "items.sqlite"

    with dataset_lock(db_path, timeout_seconds=60.0, source_count=2):
        assert main(["job", "load", str(job_file)]) == 4
        err = json.loads(capsys.readouterr().err)
        assert err["error"] == "run_in_progress"
        assert err["location"] == "dataset.path"

        assert main(["job", "watch", str(job_file), "--once"]) == 4
        err2 = json.loads(capsys.readouterr().err)
        assert err2["error"] == "run_in_progress"

    # Write a stale lock (dead pid) and verify `job load` replaces it with a WARNING and succeeds.
    lock_path(db_path).write_text(
        '{"pid": 99999999, "started_at": "2020-01-01T00:00:00+00:00"}', encoding="utf-8"
    )
    with caplog.at_level("WARNING", logger="rastro"):
        assert main(["job", "load", str(job_file)]) == 0
    capsys.readouterr()
    assert any("stale_lock" in r.getMessage() for r in caplog.records)
    assert not lock_path(db_path).exists()


def test_interrupted_run_resumes_remaining_sources_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SCHED-004: interrupted load leaves .run.json; next load runs only remaining sources."""
    job_file = _write_fixture(tmp_path)
    db_path = tmp_path / "items.sqlite"
    real_run_job = rastro.application.job_dataset.run_job
    seen_urls: list[str] = []
    fail_second = True

    def interrupting_run_job(job: Job, **kwargs: Any) -> Any:
        for src in job.sources:
            url = src.resolved_source_url() if hasattr(src, "resolved_source_url") else src.url
            seen_urls.append(url)
            if fail_second and url == "https://b.example/items":
                raise RuntimeError("simulated interruption on source 2")
        return real_run_job(job, **kwargs)

    monkeypatch.setattr("rastro.application.job_dataset.run_job", interrupting_run_job)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        rastro.load_job_file(job_file)

    # Source 1 was committed and checkpointed; lock was released in finally.
    assert not lock_path(db_path).exists()
    assert checkpoint_path(db_path).is_file()
    store = SQLiteDatasetStore(db_path)
    assert [r.fields["title"] for r in store.records("items")] == ["Alpha"]

    # Resume: only source 2 runs; source 1 is not re-run, deleted, or marked failed.
    seen_urls.clear()
    fail_second = False
    resumed = rastro.load_job_file(job_file)
    assert seen_urls == ["https://b.example/items"]
    assert [s.source_url for s in resumed.sources] == ["https://b.example/items"]
    assert not checkpoint_path(db_path).exists()
    assert sorted(str(r.fields["title"]) for r in store.records("items")) == ["Alpha", "Beta"]
    statuses = {s.source_url: s for s in store.source_status("items")}
    assert statuses["https://a.example/items"].stale is False
    assert statuses["https://b.example/items"].stale is False


def test_sigint_finishes_current_source_releases_lock_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SCHED-002, SCHED-003, SCHED-004: SIGINT finishes current source, releases lock, exits 0."""
    job_file = _write_fixture(tmp_path)
    db_path = tmp_path / "items.sqlite"
    real_run_job = rastro.application.job_dataset.run_job
    ran: list[str] = []

    def sigint_on_first_source(job: Job, **kwargs: Any) -> Any:
        result = real_run_job(job, **kwargs)
        for src in job.sources:
            url = src.resolved_source_url() if hasattr(src, "resolved_source_url") else src.url
            ran.append(url)
        os.kill(os.getpid(), signal.SIGINT)
        return result

    monkeypatch.setattr("rastro.application.job_dataset.run_job", sigint_on_first_source)
    assert main(["job", "watch", str(job_file)]) == 0
    assert ran == ["https://a.example/items"]
    assert not lock_path(db_path).exists()
    assert checkpoint_path(db_path).is_file()
