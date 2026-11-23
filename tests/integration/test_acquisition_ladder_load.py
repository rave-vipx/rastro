"""A blocked source climbs the acquisition ladder; the lowest working level is remembered."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.acquisition import WebAcquirer
from rastro.application.active_recipes import source_key
from rastro.application.checkpointed_load import run_checkpointed_load
from rastro.application.job_dataset import load_job_file
from rastro.application.job_loader import load_job
from rastro.application.ladder_levels import PROBE_EVERY, floor_level
from rastro.domain.acquisition import HttpResponse
from rastro.domain.jobs import Job, UrlJobSource

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
PAGE = b'<div class="item"><h2>Alpha</h2></div>'
URL = "https://ladder.test/list"


class Resolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)


class Transport:
    def __init__(self, blocked: bool) -> None:
        self.blocked = blocked

    async def get(self, target, **kwargs) -> HttpResponse:
        if urlsplit(target.url).path == "/robots.txt":
            return HttpResponse(404, {}, b"", 0)
        if self.blocked:
            return HttpResponse(403, {"cf-mitigated": "challenge"}, b"", 0)
        return HttpResponse(200, {"content-type": "text/html"}, PAGE, len(PAGE))


def level_of(job: Job) -> str:
    assert job.acquisition is not None
    source = next(s for s in job.sources if isinstance(s, UrlJobSource))
    render = source.render if "render" in source.model_fields_set else job.acquisition.render
    return floor_level(job.acquisition.model_copy(update={"render": render}))


class Site:
    """A fake site that refuses every level in ``blocked``; records the levels it saw."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, blocked: set[str]) -> None:
        self.blocked = blocked
        self.built: list[str] = []
        monkeypatch.setattr("rastro.application.job_dataset._web_acquirer", self.acquirer)

    def acquirer(self, job: Job, conditional):
        level = level_of(job)
        self.built.append(level)
        transport = _Leveled(self, level)
        return WebAcquirer(job.acquisition, transport=transport, resolver=Resolver())


class _Leveled(Transport):
    def __init__(self, site: Site, level: str) -> None:
        self.site, self.level = site, level

    @property
    def blocked(self) -> bool:  # read per request, so a test can lift the block
        return self.level in self.site.blocked


@pytest.fixture
def job_file(tmp_path: Path) -> Path:
    path = tmp_path / "job.toml"
    path.write_text(
        f"""
format_version = 1
recipe_memory = "recipes.sqlite"
[recipe]
strategy = "css"
record_selector = ".item"
[recipe.fields]
title = "h2::text"
[[fields]]
name = "title"
type = "string"
[[sources]]
url = "{URL}"
[acquisition]
allowed_hosts = ["ladder.test"]
min_interval_seconds = 0
max_retries = 0
[dataset]
name = "items"
path = "items.sqlite"
identity = ["title"]
""",
        encoding="utf-8",
    )
    return path


def _ladder(loaded) -> list[tuple[str, str]]:
    use = loaded.report.sources[0].acquisition
    assert use is not None
    return [(step.level, step.outcome) for step in use.ladder]


def _stored_level(job_file: Path) -> tuple[str | None, int]:
    job = load_job(job_file)
    stored = SQLiteRecipeRepository(job_file.parent / "recipes.sqlite").get(
        source_key(job, job.sources[0])
    )
    assert stored is not None
    return stored.metadata.start_level, stored.metadata.level_runs


def test_ladder_climbs_http_to_browser_on_block(job_file, monkeypatch) -> None:
    Site(monkeypatch, blocked={"http", "impersonation"})
    loaded = load_job_file(job_file)
    assert [s.status.value for s in loaded.sources] == ["success"]
    assert loaded.report.sources[0].records_accepted == 1
    assert _ladder(loaded) == [("http", "blocked"), ("impersonation", "blocked"), ("browser", "ok")]


def test_every_step_in_report(job_file, monkeypatch) -> None:
    Site(monkeypatch, blocked={"http", "impersonation", "browser", "stealth", "challenge"})
    runs = []

    def recorded(*args, **kwargs):
        runs.append(outcome := run_checkpointed_load(*args, **kwargs))
        return outcome

    monkeypatch.setattr("rastro.application.job_dataset.run_checkpointed_load", recorded)
    loaded = load_job_file(job_file)
    source = loaded.report.sources[0]
    assert source.reason == "blocked" and source.blocked is not None
    assert _ladder(loaded) == [
        ("http", "blocked"),
        ("impersonation", "blocked"),
        ("browser", "blocked"),
        ("stealth", "blocked"),
        ("challenge", "blocked"),  # no solver plugin is installed, so the ladder ends here
    ]
    result = runs[0][0].results[0]
    assert "ladder_exhausted" in {issue.code for issue in result.issues}


def test_lowest_working_level_remembered(job_file, monkeypatch) -> None:
    site = Site(monkeypatch, blocked={"http", "impersonation"})
    load_job_file(job_file)
    assert _stored_level(job_file) == ("browser", 0)

    second = load_job_file(job_file)  # starts where it worked: no new attempt below it
    assert _ladder(second) == [("browser", "ok")]
    assert second.report.sources[0].acquisition.start_level == "browser"
    assert _stored_level(job_file) == ("browser", 1)

    site.blocked = {"http", "impersonation", "browser"}  # a new block: climb from there
    third = load_job_file(job_file)
    assert _ladder(third) == [("browser", "blocked"), ("stealth", "ok")]
    assert _stored_level(job_file) == ("stealth", 0)


def test_ladder_drops_back_down_to_detect_a_lifted_block(job_file, monkeypatch) -> None:
    site = Site(monkeypatch, blocked={"http"})
    load_job_file(job_file)
    assert _stored_level(job_file) == ("impersonation", 0)
    site.blocked = set()
    for _ in range(PROBE_EVERY - 1):
        assert _ladder(load_job_file(job_file)) == [("impersonation", "ok")]
    probe = load_job_file(job_file)
    assert _ladder(probe) == [("http", "ok")]
    assert _stored_level(job_file) == ("http", 0)


def test_an_unblocked_source_has_one_step_and_builds_no_other_level(job_file, monkeypatch) -> None:
    site = Site(monkeypatch, blocked=set())
    loaded = load_job_file(job_file)
    assert _ladder(loaded) == [("http", "ok")]
    assert site.built == ["http"]
