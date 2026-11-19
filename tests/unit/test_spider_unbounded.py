"""Unbounded crawl with a fake fetcher and clock: time budget, resume, report, throttle."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from rastro import Rastro, Recipe, Source
from rastro.adapters.scrapling import ScraplingParser
from rastro.application.capability_gate import CapabilityRefused, require_capabilities
from rastro.application.crawl_checkpoint import checkpoint_store, crawl_state
from rastro.application.operator_trust import input_origin, set_process_trust
from rastro.application.outcomes import failure_result
from rastro.application.spider import extract_spider
from rastro.application.spider_unbounded import CrawlRuntime
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.jobs import Job
from rastro.domain.model_trust import OperatorTrust
from rastro.domain.models import Limits, Outcome
from rastro.domain.spider import SpiderPolicy


class Item(BaseModel):
    name: str


RECIPE = Recipe(strategy="css", record_selector="article", fields={"name": "h2::text"})
POLICY = AcquisitionPolicy(allowed_hosts=["a.example"], max_concurrency=1, min_interval_seconds=0)


def page(name, *hrefs):
    anchors = "".join(f'<a class="l" href="{h}">x</a>' for h in hrefs)
    return f"<html><body><article><h2>{name}</h2></article>{anchors}</body></html>"


class Site:
    """Every fetch advances the fake clock by ``step`` seconds."""

    def __init__(self, pages, step=10.0, status=None):
        self.pages, self.step, self.status = pages, step, status or {}
        self.now = 0.0
        self.fetched: list[str] = []
        self.slept: list[float] = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)

    async def fetch(self, source, harvest):
        path = source.url.removeprefix("https://a.example")
        self.fetched.append(path)
        self.now += self.step
        if path in self.status:
            return failure_result(
                status=Outcome.TRANSIENT_FAILURE,
                issue_code="rate_limited",
                source=source,
                schema=Item,
                started_at=datetime.now(UTC),
            ).model_copy(update={"http_status": self.status[path]})
        html = self.pages[path]
        result = Rastro().extract_html(html, source=source, schema=Item, recipe=RECIPE)
        harvest.add(source.url, html, result, "html")
        return result


def leaves(count):
    return {"/": page("Root", *(f"/{i}" for i in range(1, count + 1)))} | {
        f"/{i}": page(f"P{i}") for i in range(1, count + 1)
    }


async def crawl(site, *, throttle=None, limits=None, **spider):
    values = {"mode": "spider", "links": "a.l::attr(href)", "max_pages": "unbounded"}
    return await extract_spider(
        source=Source(url="https://a.example/"),
        schema=Item,
        spider=SpiderPolicy.model_validate({**values, "time_budget_seconds": 60, **spider}),
        policy=POLICY,
        parser=ScraplingParser(),
        limits=limits or Limits(),
        sequential=True,
        fetch_page=site.fetch,
        runtime=CrawlRuntime(clock=site.clock, sleep=site.sleep, throttle=throttle),
    )


@pytest.mark.asyncio
async def test_unbounded_resumes_without_refetch(tmp_path):
    site = Site(leaves(8))
    with crawl_state(tmp_path, "job"):
        first = await crawl(site)
        run_one = list(site.fetched)
        assert first.notes is not None and first.notes.crawl is not None
        assert first.notes.crawl.stop == "time_budget"
        assert first.notes.crawl.checkpoint == "saved"
        assert first.notes.crawl.frontier_remaining == 9 - len(run_one)
        assert first.status == Outcome.PARTIAL_SUCCESS
        assert "crawl_time_budget" in {issue.code for issue in first.issues}
        site.fetched.clear()
        second = await crawl(site)
    run_two = site.fetched
    assert set(run_one).isdisjoint(run_two)
    assert sorted(run_one + run_two) == sorted(leaves(8))
    assert second.notes is not None and second.notes.crawl is not None
    assert second.notes.crawl.resumed and second.notes.crawl.checkpoint == "finished"
    assert second.notes.crawl.stop == "complete"
    assert "crawl_resumed" in {issue.code for issue in second.issues}
    assert second.source_url == first.source_url == "https://a.example/"
    assert {r.evidence["name"].source_url for r in second.records} == {"https://a.example/"}
    with crawl_state(tmp_path, "job"):
        store = checkpoint_store("https://a.example/", SpiderPolicy(**_unbounded()))
    assert store is not None and not store.path.exists()


def _unbounded():
    return {"mode": "spider", "links": "a.l::attr(href)", "max_pages": "unbounded"}


@pytest.mark.asyncio
async def test_a_tampered_checkpoint_is_discarded_with_an_issue_and_the_crawl_starts_over(
    tmp_path,
):
    site = Site(leaves(2))
    with crawl_state(tmp_path, "job"):
        store = checkpoint_store("https://a.example/", SpiderPolicy(**_unbounded()))
        assert store is not None
        store.path.parent.mkdir(parents=True)
        store.path.write_text('{"version": 99}')
        result = await crawl(site)
    assert site.fetched == ["/", "/1", "/2"]
    assert result.notes is not None and result.notes.crawl is not None
    assert result.notes.crawl.checkpoint == "discarded"
    assert "crawl_checkpoint_discarded" in {issue.code for issue in result.issues}


@pytest.mark.asyncio
async def test_without_a_state_folder_the_crawl_still_stops_at_its_time_budget():
    site = Site(leaves(8))
    result = await crawl(site)
    assert result.notes is not None and result.notes.crawl is not None
    assert result.notes.crawl.stop == "time_budget"
    assert result.notes.crawl.checkpoint == "none"
    assert result.notes.crawl.mode == "unbounded"
    assert result.pagination_stop == "spider_time_budget"


@pytest.mark.asyncio
async def test_unbounded_crawl_is_not_capped_by_a_page_count():
    site = Site(leaves(150), step=0.0)
    result = await crawl(site)
    assert len(site.fetched) == 151
    assert result.status == Outcome.SUCCESS
    assert result.notes is not None and result.notes.crawl is not None
    assert result.notes.crawl.pages_fetched == 151 and result.notes.crawl.stop == "complete"


@pytest.mark.asyncio
async def test_the_report_summarises_outcomes_beyond_its_row_cap():
    site = Site(leaves(650), step=0.0)
    result = await crawl(site)
    report = result.spider
    assert report is not None
    assert len(report.pages) == 600
    assert report.unreported == 51
    assert report.outcomes == {"fetched": 651}


@pytest.mark.asyncio
async def test_records_past_the_record_limit_are_left_for_the_next_run(tmp_path):
    site = Site(
        {
            "/": page("Root", "/1", "/2"),
            "/1": page("P1", "/3"),
            "/2": page("P2", "/4"),
            "/3": page("P3"),
            "/4": page("P4"),
        },
        step=0.0,
    )
    with crawl_state(tmp_path, "job"):
        first = await crawl(site, limits=Limits(max_records=2))
        assert [item.name for item in first.items] == ["Root", "P1"]
        assert first.notes is not None and first.notes.crawl is not None
        assert first.notes.crawl.stop == "stopped" and first.notes.crawl.checkpoint == "saved"
        site.fetched.clear()
        second = await crawl(site, limits=Limits(max_records=10))
    assert sorted(item.name for item in second.items) == ["P2", "P3", "P4"]
    assert site.fetched.count("/2") == 1


class SpyThrottle:
    def __init__(self):
        self.delay = 0.0
        self.records: list[tuple[str, bool, float | None]] = []

    def delay_for(self, host, floor):
        return max(floor, self.delay)

    def record(self, host, *, latency, ok, retry_after):
        self.records.append((host, ok, retry_after))
        if not ok:
            self.delay = max(self.delay * 2, 4.0)
        return self.delay


@pytest.mark.asyncio
async def test_autothrottle_backoff_on_429():
    site = Site(leaves(3), step=0.0, status={"/1": 429})
    throttle = SpyThrottle()
    await crawl(site, throttle=throttle)
    assert [ok for _, ok, _ in throttle.records] == [True, False, True, True]
    assert {host for host, _, _ in throttle.records} == {"a.example"}
    assert site.slept and site.slept[0] == 4.0


def unbounded_job() -> Job:
    return Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"url": "https://a.example/"}],
            "fields": [{"name": "name", "type": "string"}],
            "recipe": {"strategy": "css", "record_selector": "a", "fields": {"name": "a::text"}},
            "acquisition": {"allowed_hosts": ["a.example"]},
            "spider": _unbounded(),
        }
    )


def test_unbounded_refused_for_bundle_origin():
    assert "unbounded_crawl" in require_capabilities(unbounded_job()).granted
    with input_origin("bundle"), pytest.raises(CapabilityRefused) as caught:
        require_capabilities(unbounded_job())
    assert caught.value.decision.refused == {"unbounded_crawl"}
    assert "--allow-untrusted bundle:unbounded_crawl" in (caught.value.hint or "")
    previous = set_process_trust(
        OperatorTrust(untrusted_capabilities={"bundle": {"unbounded_crawl"}})
    )
    try:
        with input_origin("bundle"):
            assert require_capabilities(unbounded_job()).granted == {"unbounded_crawl"}
    finally:
        set_process_trust(previous)
