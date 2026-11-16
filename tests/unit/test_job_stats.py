"""Per-source metrics, aggregates and `rastro job stats` (OBS-003..007, SYNC-007 report)."""

import json
from datetime import timedelta

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.job_stats import job_stats
from rastro.cli import main
from rastro.domain.metrics import RunMetric, percentile
from rastro.domain.models import ConfigurationError
from tests.contracts.test_dataset_metrics_migration import NOW
from tests.integration.test_open_drift_load import JOB, page

SECOND = """
[[sources]]
kind = "file"
path = "page.html"
url = "https://other.example/list?token=secret"

[sources.recipe]
version = 1
strategy = "css"
record_selector = ".item"
require_evidence = true

[sources.recipe.fields]
sku = "::attr(data-sku)"
name = "h2::text"
"""
A, B = "https://shop.example/list", "https://other.example/list"


def metric(n, url=A, *, outcome="success", seconds=2.0, network=1.0, **kw):
    base = {
        "run_id": f"r{n}",
        "source_url": url,
        "host": url.split("/")[2],
        "strategy": "explicit",
        "started_at": NOW + timedelta(hours=n),
        "finished_at": NOW + timedelta(hours=n, seconds=seconds),
        "outcome": outcome,
        "http_status": 200,
        "requests": 1,
        "retries": 0,
        "bytes": 10,
        "network_seconds": network,
        "processing_seconds": seconds - network,
        "records": 4,
        "required_fields_ok": 4,
        "fields_validated": 8,
    }
    return RunMetric(**{**base, **kw})


@pytest.fixture
def job(tmp_path):
    (tmp_path / "job.toml").write_text(JOB + SECOND, encoding="utf-8")
    (tmp_path / "page.html").write_text(page(3), encoding="utf-8")
    return tmp_path / "job.toml"


def stats(job, now=NOW + timedelta(days=1), **kw):
    return job_stats(job, now=now, **kw)


def test_a_load_writes_one_row_per_source_without_urls_queries(job):
    assert main(["job", "load", str(job)]) == 0
    rows = SQLiteDatasetStore(job.parent / "data.sqlite").run_metrics("items")
    assert [r.source_url for r in rows] == [A, B]  # the query never reaches the database
    assert {r.run_id for r in rows} == {rows[0].run_id}
    first = rows[0]
    assert (first.host, first.outcome, first.records, first.required_fields_ok) == (
        "shop.example",
        "success",
        3,
        3,
    )
    assert first.fields_validated > 0 and first.processing_seconds >= 0
    assert first.render_seconds == 0 and first.model_calls == 0
    assert b"secret" not in (job.parent / "data.sqlite").read_bytes()


def test_no_history_is_an_explicit_empty_report(job, capsys):
    assert stats(job) == {"schema_version": 1, "groups": [], "reason": "no_runs"}
    assert main(["job", "stats", str(job)]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "no_runs"
    store = SQLiteDatasetStore(job.parent / "data.sqlite")
    store.record_run_metrics("items", [metric(0)])
    assert stats(job, since="1h")["reason"] == "no_runs"  # all rows older than the window


def test_percentile_interpolates():
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([10.0], 95) == 10.0
    assert percentile([0.0, 10.0], 95) == pytest.approx(9.5)


def test_aggregates_latency_rates_freshness_and_coverage(job):
    store = SQLiteDatasetStore(job.parent / "data.sqlite")
    store.record_run_metrics(
        "items",
        [
            metric(0, seconds=2, network=1),
            metric(1, seconds=4, network=3, requests=2, retries=1, required_fields_ok=2),
            metric(
                2,
                seconds=10,
                network=5,
                outcome="transient_failure",
                records=0,
                required_fields_ok=0,
                model_calls=2,
            ),
            metric(3, B),
        ],
    )
    report = stats(job)
    assert report["schema_version"] == 1 and report["by"] == "source"
    group = next(g for g in report["groups"] if g["key"] == A)
    assert (group["runs"], group["pages"], group["records"]) == (3, 4, 8)
    assert group["success_rate"] == pytest.approx(2 / 3)
    assert group["error_rate"] == pytest.approx(1 / 3)
    assert group["retry_rate"] == pytest.approx(1 / 4)
    assert group["total_seconds"] == {"p50": 4.0, "p95": pytest.approx(9.4)}
    assert group["network_seconds"] == {"p50": 3.0, "p95": pytest.approx(4.8)}
    assert group["records_per_minute"] == pytest.approx(8 / (16 / 60))
    assert group["model_calls"] == 2
    assert group["coverage"] == pytest.approx(6 / 8)
    # Freshness counts from the last *success* (hour 1, finished after 4 s), not the failure.
    assert group["freshness_seconds"] == pytest.approx(
        (NOW + timedelta(days=1) - (NOW + timedelta(hours=1, seconds=4))).total_seconds()
    )
    assert [d["day"] for d in group["trend"]] == ["2026-11-15"]
    assert group["trend"][0]["runs"] == 3
    assert group["observed_interval_seconds"] == 3602.0  # SYNC-007: report only


def test_groups_by_host_strategy_day_and_since_window(job):
    store = SQLiteDatasetStore(job.parent / "data.sqlite")
    store.record_run_metrics(
        "items", [metric(0), metric(30, B, strategy="model"), metric(31, B, strategy="model")]
    )
    now = NOW + timedelta(days=3)
    assert {g["key"] for g in stats(job, now=now, by="host")["groups"]} == {
        "shop.example",
        "other.example",
    }
    assert {g["key"] for g in stats(job, now=now, by="strategy")["groups"]} == {
        "explicit",
        "model",
    }
    days = {g["key"] for g in stats(job, now=now, by="day")["groups"]}
    assert days == {"2026-11-15", "2026-11-16"}
    recent = stats(job, now=now, since="2d")
    assert [g["key"] for g in recent["groups"]] == [B]


def test_never_succeeded_group_has_no_freshness_or_coverage(job):
    store = SQLiteDatasetStore(job.parent / "data.sqlite")
    store.record_run_metrics(
        "items", [metric(0, outcome="access_restriction", records=0, required_fields_ok=0)]
    )
    (group,) = stats(job)["groups"]
    assert group["freshness_seconds"] is None and group["coverage"] is None
    assert group["success_rate"] == 0 and group["error_rate"] == 1


@pytest.mark.parametrize("since", ["7", "0d", "-1d", "d7", "7w", "1.5d", ""])
def test_bad_since_is_a_configuration_error(job, since):
    with pytest.raises(ConfigurationError):
        stats(job, since=since)


def test_postgres_datasets_are_refused_for_now(tmp_path):
    text = JOB.replace('path = "data.sqlite"', 'backend = "postgres"\ndsn_env = "RASTRO_DSN"')
    (tmp_path / "job.toml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="stats need the local SQLite dataset"):
        job_stats(tmp_path / "job.toml")


def test_stats_without_a_dataset_file_reports_no_runs(job):
    assert not (job.parent / "data.sqlite").exists()
    assert stats(job)["reason"] == "no_runs"


def test_stores_without_metrics_record_nothing():
    """Postgres-backed stores do not implement the recorder: loading must not fail."""
    from rastro.application.run_metrics import record_run_metrics

    assert record_run_metrics(object(), "items", None, ()) is False  # type: ignore[arg-type]
