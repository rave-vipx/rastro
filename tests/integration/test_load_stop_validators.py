"""A stopped load commits validators only for the sources it ran (SCHED-004, SYNC-004)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.acquisition import WebAcquirer
from rastro.application.job_dataset import load_job_file
from rastro.domain.acquisition import HttpResponse

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
PAGE = b'<div class="item"><h2>Alpha</h2></div>'
URLS = ("https://stop.test/one", "https://stop.test/two", "https://stop.test/three")


class Resolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)


class Transport:
    async def get(self, target, **kwargs) -> HttpResponse:
        path = urlsplit(target.url).path
        if path == "/robots.txt":
            return HttpResponse(404, {}, b"", 0)
        headers = {"content-type": "text/html", "etag": f'"v{path[1:]}"'}
        return HttpResponse(200, headers, PAGE, len(PAGE))


@pytest.fixture
def job_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "job.toml"
    sources = "\n".join(f'[[sources]]\nurl = "{url}"' for url in URLS)
    path.write_text(
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
{sources}
[acquisition]
allowed_hosts = ["stop.test"]
min_interval_seconds = 0
max_retries = 0
[dataset]
name = "items"
path = "items.sqlite"
identity = ["title"]
""",
        encoding="utf-8",
    )

    def acquirer(job, conditional):
        return WebAcquirer(
            job.acquisition, transport=Transport(), resolver=Resolver(), conditional=conditional
        )

    monkeypatch.setattr("rastro.application.job_dataset.job_web_acquirer", acquirer)
    return path


def _stored(tmp_path: Path) -> list[bool]:
    store = SQLiteDatasetStore(tmp_path / "items.sqlite")
    return [bool(store.get_validators("items", url)) for url in URLS]


def test_stop_early_commits_validators_for_completed_sources_only(
    job_file: Path, tmp_path: Path
) -> None:
    loaded = load_job_file(job_file, should_stop=lambda: True)
    assert [s.status.value for s in loaded.sources] == ["success"]
    assert loaded.status != "storage_error"
    assert _stored(tmp_path) == [True, False, False]


def test_resume_after_stop_commits_the_remaining_validators(job_file: Path, tmp_path: Path) -> None:
    load_job_file(job_file, should_stop=lambda: True)
    resumed = load_job_file(job_file)
    assert [s.status.value for s in resumed.sources] == ["success", "success"]
    assert resumed.status != "storage_error"
    assert _stored(tmp_path) == [True, True, True]


def test_full_run_commits_every_validator(job_file: Path, tmp_path: Path) -> None:
    loaded = load_job_file(job_file)
    assert [s.status.value for s in loaded.sources] == ["success"] * 3
    assert _stored(tmp_path) == [True, True, True]
