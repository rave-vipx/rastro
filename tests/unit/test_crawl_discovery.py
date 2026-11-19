"""Link discovery and page identities preserve useful queries without storing secrets."""

import pytest
from pydantic import BaseModel, ValidationError

from rastro import Rastro, Source
from rastro.adapters.scrapling import ScraplingParser
from rastro.application.pagination import extract_paginated
from rastro.application.spider import extract_spider
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.models import CssRecipe, Limits, Outcome
from rastro.domain.spider import SpiderPolicy

BASE = "https://catalogue.example/"
RECIPE = CssRecipe(record_selector="h1", fields={"name": "::text"})


class Item(BaseModel):
    name: str


def extract(url, body):
    return Rastro().extract_html(body, source=Source(url=url), schema=Item, recipe=RECIPE)


async def crawl(pages, **settings):
    fetched = []

    async def fetch(source, harvest):
        fetched.append(source.url)
        body = pages[source.url.removeprefix(BASE)]
        result = extract(source.url, body)
        harvest.add(source.url, body, result, "html")
        return result

    result = await extract_spider(
        source=Source(url=BASE),
        schema=Item,
        spider=SpiderPolicy.model_validate(
            {"mode": "spider", "links": "a::attr(href)", **settings}
        ),
        policy=None,
        parser=ScraplingParser(),
        limits=Limits(),
        sequential=True,
        fetch_page=fetch,
    )
    return result, fetched


@pytest.mark.parametrize(
    "links", ["a.x, a.y::attr(href)", ["a.x, a.y::attr(href)"], "a], b::attr(href)"]
)
def test_links_comma_group_refused_with_hint(links):
    with pytest.raises(ValidationError, match=r"link_group_refused.*list"):
        SpiderPolicy.model_validate({"mode": "spider", "links": links})


@pytest.mark.parametrize("links", [[], ["a::attr(href)", "a::text"]])
def test_links_require_nonempty_attribute_expressions(links):
    with pytest.raises(ValidationError):
        SpiderPolicy.model_validate({"mode": "spider", "links": links})


@pytest.mark.asyncio
async def test_links_list_follows_hrefs_only():
    result, fetched = await crawl(
        {
            "": '<h1>Root</h1><a class="x" href="one">HTML is not a URL</a>'
            '<a class="y" href="two">Neither is this</a><a href="ignored">Ignored</a>',
            "one": "<h1>One</h1>",
            "two": "<h1>Two</h1>",
        },
        links=["a.x::attr(href)", "a.y::attr(href)"],
    )
    assert fetched == [BASE, BASE + "one", BASE + "two"]
    assert [item.name for item in result.items] == ["Root", "One", "Two"]


@pytest.mark.asyncio
async def test_deny_pattern_before_cap():
    result, fetched = await crawl(
        {
            "": '<h1>Root</h1><a href="archive/old">Old</a><a href="fresh">Fresh</a>',
            "fresh": "<h1>Fresh</h1>",
        },
        deny_pattern="^/archive/",
        max_pages=2,
    )
    assert fetched == [BASE, BASE + "fresh"]
    assert result.status == Outcome.SUCCESS
    assert result.spider.pages[1].outcome == "path_denied"


@pytest.mark.parametrize("pattern", ["(", "(?=x)", r"(x)\1", "", "x" * 513])
def test_deny_pattern_is_bounded_and_linear_time(pattern):
    with pytest.raises(ValidationError):
        SpiderPolicy(mode="spider", links="a::attr(href)", deny_pattern=pattern)


@pytest.mark.asyncio
async def test_spider_seen_set_uses_canonical_identity():
    result, fetched = await crawl(
        {
            "": '<a href="?page=2&amp;b=1">Two</a>'
            '<a href="?utm_source=x&amp;b=1&amp;page=2">Duplicate</a>'
            '<a href="?page=3">Three</a>',
            "?page=2&b=1": "<h1>Two</h1>",
            "?page=3": "<h1>Three</h1>",
        }
    )
    assert fetched == [BASE, BASE + "?page=2&b=1", BASE + "?page=3"]
    assert [r.evidence["name"].page_url for r in result.records] == [
        BASE + "?b=1&page=2",
        BASE + "?page=3",
    ]
    assert "?" not in result.spider.model_dump_json()


async def paginate(pages, *, final_url=None):
    fetched = []

    class Acquisition:
        async def acquire(self, source, **kwargs):
            fetched.append(source.url)
            return AcquisitionResult(
                url=final_url or source.url,
                body=pages[source.url],
                status=Outcome.SUCCESS,
                issue_code=None,
                http_status=200,
            )

    async def extract_page(page):
        return extract(page.url, page.body)

    result = await extract_paginated(
        source=Source(url=next(iter(pages))),
        schema=Item,
        recipe=CssRecipe(
            record_selector="h1",
            fields={"name": "::text"},
            pagination={"next": "a::attr(href)"},
        ),
        parser=ScraplingParser(),
        limits=Limits(),
        acquisition=Acquisition(),
        policy=None,
        extract_page=extract_page,
    )
    return result, fetched


@pytest.mark.asyncio
async def test_page2_page3_distinct_provenance():
    result, fetched = await paginate(
        {
            BASE + "?page=2&token=hidden": '<h1>Two</h1><a href="?page=3">Next</a>',
            BASE + "?page=3": "<h1>Three</h1>",
        }
    )
    assert len(fetched) == 2
    assert [r.evidence["name"].page_url for r in result.records] == [
        BASE + "?page=2",
        BASE + "?page=3",
    ]
    assert "hidden" not in result.model_dump_json()
    assert result.source_url == BASE


@pytest.mark.asyncio
async def test_pagination_repeat_uses_canonical_identity():
    result, fetched = await paginate(
        {BASE + "?page=2&b=1": '<h1>Two</h1><a href="?utm_source=x&amp;b=1&amp;page=2">Same</a>'}
    )
    assert result.pagination_stop == "repeat"
    assert len(fetched) == 1


@pytest.mark.asyncio
async def test_redirected_page_uses_final_canonical_identity():
    result, fetched = await paginate(
        {BASE: '<h1>Two</h1><a href="?utm_source=x&amp;page=2">Same</a>'},
        final_url=BASE + "?page=2&token=hidden",
    )
    assert fetched == [BASE]
    assert result.records[0].evidence["name"].page_url == BASE + "?page=2"
    assert result.pagination_stop == "repeat"


@pytest.mark.parametrize(
    "links",
    [
        ["a::attr(href)"] * 9,
        ["a::attr(href)", "a:has(b)::attr(href)"],
        ["a::attr(href)", "x" * 1025 + "::attr(href)"],
    ],
)
def test_link_lists_keep_selector_cost_and_size_limits(links):
    with pytest.raises(ValidationError):
        SpiderPolicy.model_validate({"mode": "spider", "links": links})


@pytest.mark.asyncio
async def test_quoted_commas_are_not_selector_groups():
    result, fetched = await crawl(
        {"": '<a title="x,y" href="one">One</a>', "one": "<h1>One</h1>"},
        links='a[title="x,y"]::attr(href)',
    )
    assert fetched == [BASE, BASE + "one"]
    assert result.items[0].name == "One"


@pytest.mark.asyncio
async def test_existing_attribute_link_expressions_remain_supported():
    result, fetched = await crawl(
        {"": '<a data-href="one">One</a>', "one": "<h1>One</h1>"},
        links="a::attr(data-href)",
    )
    assert fetched == [BASE, BASE + "one"]
    assert result.items[0].name == "One"
