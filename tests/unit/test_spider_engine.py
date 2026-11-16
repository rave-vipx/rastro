"""Spider engine with a fake page fetcher: concurrency cap, failures, link harvest (N3)."""

import asyncio

import pytest
from pydantic import BaseModel

from rastro import Rastro, Recipe, Source
from rastro.adapters.scrapling import ScraplingParser
from rastro.application.spider import extract_spider
from rastro.application.spider_links import LinkHarvest
from rastro.application.spider_outcome import page_outcome
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.models import ConfigurationError, Limits, Outcome, ResourceLimitError
from rastro.domain.spider import SpiderPolicy


class Item(BaseModel):
    name: str


RECIPE = Recipe(strategy="css", record_selector="article", fields={"name": "h2::text"})
POLICY = AcquisitionPolicy(allowed_hosts=["a.test"], max_concurrency=2, min_interval_seconds=0)


def page(name, *hrefs):
    anchors = "".join(f'<a class="l" href="{h}">x</a>' for h in hrefs)
    return f"<html><body><article><h2>{name}</h2></article>{anchors}</body></html>"


class Site:
    def __init__(self, pages, fail=None):
        self.pages, self.fail = pages, fail or {}
        self.live = self.peak = 0

    async def fetch(self, source, harvest):
        path = source.url.removeprefix("https://a.test")
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(0.01)
            if path in self.fail:
                raise self.fail[path]
            html = self.pages[path]
            result = Rastro().extract_html(html, source=source, schema=Item, recipe=RECIPE)
            harvest.add(source.url, html, result, "html")
            return result
        finally:
            self.live -= 1


async def crawl(site, **spider):
    policy = SpiderPolicy.model_validate({"mode": "spider", "links": "a.l::attr(href)", **spider})
    return await extract_spider(
        source=Source(url="https://a.test/"),
        schema=Item,
        spider=policy,
        policy=POLICY,
        parser=ScraplingParser(),
        limits=Limits(),
        sequential=False,
        fetch_page=site.fetch,
    )


@pytest.mark.asyncio
async def test_concurrency_never_exceeds_the_acquisition_cap_and_order_is_stable():
    site = Site(
        {"/": page("Root", "/1", "/2", "/3", "/4"), **{f"/{i}": page(f"P{i}") for i in range(1, 5)}}
    )
    result = await crawl(site)
    assert site.peak == 2
    assert [item.name for item in result.items] == ["Root", "P1", "P2", "P3", "P4"]
    assert result.status == Outcome.SUCCESS


@pytest.mark.asyncio
async def test_a_later_page_that_raises_is_a_recorded_failure_and_the_rest_survive():
    site = Site(
        {"/": page("Root", "/1", "/2"), "/2": page("P2")},
        fail={"/1": ResourceLimitError("too many records")},
    )
    result = await crawl(site)
    assert [p.outcome for p in result.spider.pages] == ["fetched", "failed", "fetched"]
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert result.pagination_stop == "spider_page_failed"
    assert [item.name for item in result.items] == ["Root", "P2"]


@pytest.mark.asyncio
async def test_the_start_page_error_is_raised_as_without_a_spider():
    site = Site({"/": ""}, fail={"/": ConfigurationError("bad recipe")})
    with pytest.raises(ConfigurationError, match="bad recipe"):
        await crawl(site)


def test_link_expression_that_cannot_run_is_reported_not_swallowed():
    harvest = LinkHarvest(
        SpiderPolicy(mode="spider", links="a::attr(href)"), ScraplingParser(), Limits()
    )
    result = Rastro().extract_html(
        "<p/>", source=Source(url="https://a.test/"), schema=Item, recipe=RECIPE
    )
    harvest.add("https://a.test/", "<a href='/x'>", result, "json")  # links reads HTML only
    assert harvest.failed and harvest.hrefs == []


def test_page_outcomes_follow_status_and_issue_codes():
    from datetime import UTC, datetime

    from rastro.application.outcomes import failure_result

    def fail(code, status=Outcome.PERMANENT_FAILURE):
        return failure_result(
            status=status,
            issue_code=code,
            source=Source(url="https://a.test/"),
            schema=Item,
            started_at=datetime.now(UTC),
        )

    assert page_outcome(fail("robots_denied", Outcome.ACCESS_RESTRICTION)) == "robots_denied"
    assert page_outcome(fail("request_limit")) == "budget"
    assert page_outcome(fail("https_downgrade")) == "https_downgrade"
    assert page_outcome(fail("anything")) == "failed"
    assert page_outcome(fail("x", Outcome.MISSING_DATA)) == "no_records"


@pytest.mark.asyncio
async def test_an_acquirer_that_does_not_charge_the_scope_is_counted_by_delta():
    from rastro.application.acquisition_state import PAGINATION_SCOPE
    from rastro.application.spider_page import fetch_spider_page
    from rastro.domain.acquisition import AcquisitionResult

    class Free:
        async def acquire(self, source, *, max_bytes, representation):
            await asyncio.sleep(0.01)
            return AcquisitionResult(
                url=source.url, body="<p/>", status=Outcome.SUCCESS, issue_code=None, requests=2
            )

    async def extract(fetched):
        source = Source(url=fetched.url)
        return Rastro().extract_html("<p/>", source=source, schema=Item, recipe=RECIPE)

    remaining = []

    async def fetch(source, harvest):
        result = await fetch_spider_page(
            source,
            schema=Item,
            recipe=RECIPE,
            parser=ScraplingParser(),
            limits=Limits(),
            acquisition=Free(),
            policy=POLICY,
            extract_page=extract,
        )
        if source.url.endswith("a.test/"):
            harvest.hrefs.extend(f"https://a.test/{i}" for i in range(10))
        remaining.append(PAGINATION_SCOPE.get().remaining)
        return result

    site = Site({})
    site.fetch = fetch
    await crawl(site, max_pages=30)
    assert min(remaining) == 30 - 2 * 11


@pytest.mark.asyncio
async def test_max_depth_four_records_the_depth_limit_refusal_and_keeps_the_records():
    chain = {"/": page("P0", "/1")}
    chain.update({f"/{i}": page(f"P{i}", f"/{i + 1}") for i in range(1, 5)})
    chain["/5"] = page("never fetched")
    result = await crawl(Site(chain), max_depth=4)
    assert [item.name for item in result.items] == ["P0", "P1", "P2", "P3", "P4"]
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert "spider_depth_limit" in [issue.code for issue in result.issues]
    refused = result.spider.pages[-1]
    assert (refused.path, refused.depth, refused.outcome) == ("/5", 5, "depth_limit")


@pytest.mark.asyncio
async def test_a_long_deny_prefix_is_enforced_on_the_whole_path():
    long_prefix = "/" + "x" * 270
    site = Site({"/": page("Root", long_prefix + "/a"), long_prefix + "/a": page("Hostile")})
    result = await crawl(site, deny=[long_prefix])
    assert [item.name for item in result.items] == ["Root"]
    refused = result.spider.pages[-1]
    assert refused.outcome == "path_denied"
    assert len(refused.path) <= 256  # only the report field is shortened


@pytest.mark.asyncio
async def test_a_long_allow_prefix_still_admits_its_pages():
    long_prefix = "/" + "y" * 270
    site = Site({"/": page("Root", long_prefix + "/a"), long_prefix + "/a": page("Kept")})
    result = await crawl(site, allow=["/", long_prefix])
    assert [item.name for item in result.items] == ["Root", "Kept"]
