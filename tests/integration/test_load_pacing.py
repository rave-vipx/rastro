"""One acquisition port per load keeps host pacing across sources (SCHED-004, SEC-008)."""

from __future__ import annotations

import itertools
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

import pytest

from rastro.application.acquisition import WebAcquirer
from rastro.application.job_dataset import load_job_file
from rastro.domain.acquisition import HttpResponse

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
MIN_INTERVAL = 0.3
PAGE = b'<div class="item"><h2>Alpha</h2></div>'


class Resolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)


class Transport:
    def __init__(self) -> None:
        self.requests: list[tuple[float, str]] = []

    async def get(self, target, **kwargs) -> HttpResponse:
        self.requests.append((perf_counter(), urlsplit(target.url).path))
        if urlsplit(target.url).path == "/robots.txt":
            return HttpResponse(404, {}, b"", 0)
        return HttpResponse(200, {"content-type": "text/html"}, PAGE, len(PAGE))


def test_two_same_host_sources_stay_min_interval_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_file = tmp_path / "job.toml"
    job_file.write_text(
        f"""
format_version = 1
[recipe]
strategy = "css"
record_selector = ".item"
[recipe.fields]
title = "h2::text"
[[fields]]
name = "title"
type = "string"
[[sources]]
url = "https://pace.test/one"
[[sources]]
url = "https://pace.test/two"
[acquisition]
allowed_hosts = ["pace.test"]
min_interval_seconds = {MIN_INTERVAL}
max_retries = 0
[dataset]
name = "items"
path = "items.sqlite"
identity = ["title"]
""",
        encoding="utf-8",
    )
    transport, built = Transport(), []

    def one_acquirer(job, conditional):
        built.append(job.acquisition)
        return WebAcquirer(
            job.acquisition, transport=transport, resolver=Resolver(), conditional=conditional
        )

    monkeypatch.setattr("rastro.application.job_dataset._web_acquirer", one_acquirer)
    loaded = load_job_file(job_file)
    assert [s.status.value for s in loaded.sources] == ["success", "success"]
    assert len(built) == 1
    stamps = [stamp for stamp, _ in transport.requests]
    assert {path for _, path in transport.requests} == {"/robots.txt", "/one", "/two"}
    assert len(stamps) >= 4
    assert min(b - a for a, b in itertools.pairwise(stamps)) >= MIN_INTERVAL - 0.03
