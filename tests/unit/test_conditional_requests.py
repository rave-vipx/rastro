"""SYNC-004, ALERT-006: successful loads retain records on a conditional 304."""

import sqlite3

import pytest

from rastro.adapters.dns import SystemResolver
from rastro.adapters.http import CurlTransport
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.job_dataset import load_job_file
from rastro.domain.acquisition import HttpResponse

JOB = """format_version = 1
[[fields]]
name = "title"
type = "string"
[[sources]]
kind = "url"
url = "https://a.example/list?private=query"
[recipe]
strategy = "css"
record_selector = "article"
[recipe.fields]
title = "h1::text"
[acquisition]
allowed_hosts = ["a.example", "b.example"]
min_interval_seconds = 0
[dataset]
name = "d"
path = "d.sqlite"
identity = ["title"]
"""


def test_200_then_304_keeps_records_and_updates_status(
    tmp_path, monkeypatch, caplog, offline_cli_loop
):
    path = tmp_path / "job.toml"
    path.write_text(JOB)
    calls = []
    version = 1

    async def resolve(self, host, port):
        return ("8.8.8.8",)

    async def get(self, target, *, headers, **kwargs):
        calls.append((target.url, dict(headers)))
        if target.url.endswith("robots.txt"):
            assert "If-None-Match" not in headers
            return HttpResponse(404, {}, b"", 0)
        if version == 2:
            assert headers["If-None-Match"] == '"first"'
            assert headers["If-Modified-Since"] == "Wed, 01 Jan 2025 00:00:00 GMT"
            return HttpResponse(304, {}, b"", 0)
        body = b"<article><h1>one</h1></article>"
        return HttpResponse(
            200,
            {
                "content-type": "text/html",
                "etag": '"first"' if version == 1 else '"third"',
                **({"last-modified": "Wed, 01 Jan 2025 00:00:00 GMT"} if version == 1 else {}),
            },
            body,
            len(body),
        )

    monkeypatch.setattr(SystemResolver, "resolve", resolve)
    monkeypatch.setattr(CurlTransport, "get", get)
    first = load_job_file(path)
    assert first.exit_code == 0
    store = SQLiteDatasetStore(tmp_path / "d.sqlite")
    before = store.records("d")
    first_status = store.source_status("d")[0]
    version = 2
    second = load_job_file(path, drift=True)
    assert second.exit_code == 0
    assert second.sources[0].status == "unchanged"
    assert second.sources[0].counts["unchanged"] == 1
    assert second.drift == ()
    assert store.records("d") == before
    status = store.source_status("d")[0]
    assert status.last_attempt == status.last_success > first_status.last_success
    assert not status.stale
    assert "source_failed" not in caplog.text
    version = 3
    assert load_job_file(path).exit_code == 0
    assert store.get_validators("d", "https://a.example/list?private=query") == {"etag": '"third"'}
    assert store.get_validators("d", "https://a.example/list?private=other") == {}
    with sqlite3.connect(tmp_path / "d.sqlite") as db:
        assert "private=query" not in str(db.execute("SELECT * FROM dataset_validators").fetchall())


@pytest.mark.asyncio
async def test_no_dataset_never_enables_conditional_requests():
    from rastro.application.acquisition import WebAcquirer
    from rastro.domain.acquisition import AcquisitionPolicy
    from rastro.domain.models import Source
    from tests.unit.test_cookie_jar import Resolver, Transport

    transport = Transport()
    client = WebAcquirer(
        AcquisitionPolicy(allowed_hosts=("a.example",), min_interval_seconds=0),
        transport=transport,
        resolver=Resolver(),
    )
    for _ in range(2):
        await client.acquire(Source(url="https://a.example/"), max_bytes=100)
    assert all("If-None-Match" not in headers for _, headers in transport.calls)


def test_redirect_validators_never_reach_intermediate_or_other_host(
    tmp_path, monkeypatch, offline_cli_loop
):
    path = tmp_path / "job.toml"
    path.write_text(JOB)
    repeated = False

    async def resolve(self, host, port):
        return ("8.8.8.8",)

    async def get(self, target, *, headers, **kwargs):
        if target.url.endswith("robots.txt"):
            assert "If-None-Match" not in headers
            return HttpResponse(404, {}, b"", 0)
        if "a.example" in target.url:
            assert "If-None-Match" not in headers
            return HttpResponse(302, {"location": "https://b.example/final?another=secret"}, b"", 0)
        if repeated:
            assert headers["If-None-Match"] == '"final"'
            return HttpResponse(304, {}, b"", 0)
        body = b"<article><h1>one</h1></article>"
        return HttpResponse(200, {"content-type": "text/html", "etag": '"final"'}, body, len(body))

    monkeypatch.setattr(SystemResolver, "resolve", resolve)
    monkeypatch.setattr(CurlTransport, "get", get)
    assert load_job_file(path).exit_code == 0
    before = SQLiteDatasetStore(tmp_path / "d.sqlite").records("d")
    from datetime import timedelta

    from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
    from rastro.domain.drift import FieldStructure
    from tests.integration.test_sqlite_drift import snapshot

    monitor = SQLiteDriftMonitor(tmp_path / "d.sqlite")
    baseline = snapshot(source_url="https://b.example/final")
    monitor.assess("d", baseline)
    monitor.assess(
        "d",
        snapshot(
            source_url=baseline.source_url,
            run_id="changed",
            observed_at=baseline.observed_at + timedelta(seconds=1),
            fields={
                "title": FieldStructure(
                    selector_match_count=0, field_coverage=0, validation_failure_rate=1
                )
            },
        ),
    )
    open_before = monitor.open_drift("d")
    assert open_before
    repeated = True
    loaded = load_job_file(path, drift=True)
    assert loaded.exit_code == 4 and loaded.sources[0].status == "unchanged"
    assert loaded.drift == ()
    assert loaded.open_drift == monitor.open_drift("d") == open_before
    assert SQLiteDatasetStore(tmp_path / "d.sqlite").records("d") == before


def test_unsolicited_304_is_failure_and_failed_200_does_not_save_validators(
    tmp_path, monkeypatch, offline_cli_loop
):
    path = tmp_path / "job.toml"
    path.write_text(JOB)
    status = 304

    async def resolve(self, host, port):
        return ("8.8.8.8",)

    async def get(self, target, *, headers, **kwargs):
        if target.url.endswith("robots.txt"):
            return HttpResponse(404, {}, b"", 0)
        return HttpResponse(
            status, {"content-type": "text/html", "etag": '"bad"'}, b"<p>no data</p>", 14
        )

    monkeypatch.setattr(SystemResolver, "resolve", resolve)
    monkeypatch.setattr(CurlTransport, "get", get)
    assert load_job_file(path).exit_code == 1
    status = 200
    assert load_job_file(path).exit_code == 1
    store = SQLiteDatasetStore(tmp_path / "d.sqlite")
    assert store.get_validators("d", "https://a.example/list?private=query") == {}
    assert store.records("d") == ()


def test_validator_storage_error_is_explicit(tmp_path, monkeypatch, offline_cli_loop):
    from rastro.domain.dataset import DatasetStorageError

    path = tmp_path / "job.toml"
    path.write_text(JOB)

    async def resolve(self, host, port):
        return ("8.8.8.8",)

    async def get(self, target, *, headers, **kwargs):
        if target.url.endswith("robots.txt"):
            return HttpResponse(404, {}, b"", 0)
        body = b"<article><h1>one</h1></article>"
        return HttpResponse(200, {"content-type": "text/html"}, body, len(body))

    def broken(*args):
        raise DatasetStorageError("Synthetic storage failure")

    monkeypatch.setattr(SystemResolver, "resolve", resolve)
    monkeypatch.setattr(CurlTransport, "get", get)
    monkeypatch.setattr(SQLiteDatasetStore, "get_validators", broken)
    assert load_job_file(path).exit_code == 5


def test_missing_http_extra_is_a_configuration_error(tmp_path, monkeypatch):
    import builtins

    from rastro.domain.models import ConfigurationError

    path = tmp_path / "job.toml"
    path.write_text(JOB)
    real_import = builtins.__import__

    def deny(name, *args, **kwargs):
        if name == "rastro.adapters.http":
            raise ModuleNotFoundError("No module named 'curl_cffi'", name="curl_cffi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny)
    with pytest.raises(ConfigurationError, match=r"rastro-engine\[http\]"):
        load_job_file(path)
