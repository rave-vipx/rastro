"""Offline sitemap seed for the bounded spider (N16, N3).

Requirements: N16, N3, SEC-001 (hosts), SEC-003 (robots), SEC-008 (budget, byte caps).
"""

import gzip
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro.application.acquisition import WebAcquirer
from rastro.application.jobs import run_job
from rastro.domain.acquisition import HttpResponse
from rastro.domain.jobs import Job
from rastro.domain.models import Outcome

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

XML = "application/xml"
NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


class Resolver:
    async def resolve(self, host, port):
        return ("93.184.216.34",)


class Transport:
    def __init__(self, pages, robots=""):
        self.pages, self.robots, self.urls = pages, robots, []

    async def get(self, target, **kwargs):
        self.urls.append(target.url)
        path = urlsplit(target.url).path
        if path == "/robots.txt":
            raw = self.robots.encode()
            return HttpResponse(200 if raw else 404, {"content-type": "text/plain"}, raw, len(raw))
        status, kind, body = self.pages.get(path, (404, "text/html", ""))
        raw = body if isinstance(body, bytes) else body.encode()
        return HttpResponse(status, {"content-type": kind}, raw, len(raw))

    def fetched(self):
        return [urlsplit(u).path for u in self.urls if urlsplit(u).path != "/robots.txt"]


def urlset(*locs):
    rows = "".join(f"<url><loc>{loc}</loc></url>" for loc in locs)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="{NS}">{rows}</urlset>'


def index(*locs):
    rows = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return f'<?xml version="1.0"?><sitemapindex xmlns="{NS}">{rows}</sitemapindex>'


def event(name):
    data = json.dumps({"@context": "https://schema.org", "@type": "Event", "name": name})
    return (200, "text/html", f'<html><script type="application/ld+json">{data}</script></html>')


def job(*, spider=None, budget=60, hosts=None, render="http"):
    return Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"url": "https://dance.test/sitemap.xml"}],
            "fields": [{"name": "name", "type": "string"}],
            "recipe": {"strategy": "jsonld", "item_type": "Event", "fields": {"name": "/name"}},
            "spider": {"mode": "spider", "seed": "sitemap", "max_depth": 1, **(spider or {})},
            "acquisition": {
                "allowed_hosts": hosts or ["dance.test", "other.test"],
                "max_requests": budget,
                "min_interval_seconds": 0,
                "max_retries": 0,
                "render": render,
            },
            "dataset": {"name": "events", "path": "events.sqlite", "identity": ["name"]},
        }
    )


def run(spec, pages, robots=""):
    transport = Transport(pages, robots)
    acquirer = WebAcquirer(spec.acquisition, transport=transport, resolver=Resolver())
    report = run_job(spec, acquisition=acquirer, base_dir=Path.cwd())
    return report.results[0], transport


def rows(result):
    return [(p.depth, p.path, p.outcome) for p in result.spider.pages]


def codes(result):
    return {issue.code for issue in result.issues}


def test_urlset_seeds_detail_pages_extracted_with_a_jsonld_recipe():
    pages = {
        "/sitemap.xml": (200, XML, urlset("https://dance.test/evento/1", "/evento/2")),
        "/evento/1": event("Tango"),
        "/evento/2": event("Salsa"),
    }
    result, transport = run(job(), pages)
    assert result.status == Outcome.SUCCESS
    assert result.pagination_stop == "spider_complete"
    assert [r.name for r in result.items] == ["Tango", "Salsa"]  # document order
    assert rows(result)[0] == (0, "/sitemap.xml", "no_records")
    assert transport.fetched() == ["/sitemap.xml", "/evento/1", "/evento/2"]


def test_off_host_and_denied_prefix_locs_are_skipped_and_reported():
    locs = (
        "https://evil.test/evento/9",
        "https://dance.test/admin/1",
        "https://dance.test/evento/1",
    )
    pages = {"/sitemap.xml": (200, "text/xml", urlset(*locs)), "/evento/1": event("Tango")}
    result, transport = run(job(spider={"allow": ["/evento"]}), pages)
    assert rows(result)[1:] == [
        (1, "/evento/9", "off_host"),
        (1, "/admin/1", "path_denied"),
        (1, "/evento/1", "fetched"),
    ]
    assert result.status == Outcome.SUCCESS
    assert transport.fetched() == ["/sitemap.xml", "/evento/1"]


def test_sitemap_index_is_followed_one_level_and_nested_indexes_fail_explicitly():
    pages = {
        "/sitemap.xml": (200, XML, index("/s1.xml", "/s2.xml", "https://evil.test/s3.xml")),
        "/s1.xml": (200, XML, urlset("/evento/1")),
        "/s2.xml": (200, XML, index("/s4.xml")),
        "/evento/1": event("Tango"),
    }
    result, transport = run(job(), pages)
    assert rows(result) == [
        (0, "/sitemap.xml", "no_records"),
        (0, "/s1.xml", "no_records"),
        (0, "/s2.xml", "failed"),
        (0, "/s3.xml", "off_host"),
        (1, "/evento/1", "fetched"),
    ]
    assert "sitemap_nested_index" in codes(result)
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert "/s4.xml" not in transport.fetched()


def test_gzip_sitemap_is_inflated_within_the_byte_cap():
    raw = gzip.compress(urlset("/evento/1").encode())
    pages = {"/sitemap.xml.gz": (200, "application/gzip", raw), "/evento/1": event("Tango")}
    spec = job().model_copy(
        update={
            "sources": (
                job().sources[0].model_copy(update={"url": "https://dance.test/sitemap.xml.gz"}),
            )
        }
    )
    result, _ = run(spec, pages)
    assert [r.name for r in result.items] == ["Tango"]


def test_page_cap_selects_the_first_locs_in_document_order_and_reports_the_cap():
    locs = [f"/evento/{n}" for n in range(50)]
    pages = {"/sitemap.xml": (200, XML, urlset(*locs))} | {loc: event(loc) for loc in locs}
    result, transport = run(job(spider={"max_pages": 21}), pages)  # the sitemap is a page too
    assert [r.name for r in result.items] == locs[:20]
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert result.pagination_stop == "spider_page_limit"
    assert [o for _, _, o in rows(result)].count("page_limit") == 30
    assert len(transport.fetched()) == 21


def test_request_budget_caps_the_sitemap_crawl_too():
    pages = {"/sitemap.xml": (200, XML, urlset("/evento/1", "/evento/2")), "/evento/1": event("A")}
    result, transport = run(job(budget=3), pages)  # robots, sitemap, one page
    assert result.pagination_stop == "spider_budget"
    assert transport.fetched() == ["/sitemap.xml", "/evento/1"]


BILLION_LAUGHS = (
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
    '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
    f'<urlset xmlns="{NS}"><url><loc>/evento/&lol2;</loc></url></urlset>'
)
XXE = (
    '<?xml version="1.0"?><!DOCTYPE u [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    f'<urlset xmlns="{NS}"><url><loc>&x;</loc></url></urlset>'
)


@pytest.mark.parametrize("payload", [BILLION_LAUGHS, XXE, f'<urlset xmlns="{NS}"><url><loc>&x;'])
def test_dtd_entities_and_broken_xml_are_refused_without_expansion(payload):
    result, transport = run(job(), {"/sitemap.xml": (200, XML, payload)})
    assert "sitemap_invalid" in codes(result)
    assert result.status == Outcome.INVALID_DATA
    assert result.pagination_stop == "spider_page_failed"
    assert transport.fetched() == ["/sitemap.xml"]


def test_oversized_sitemap_is_refused():
    big = urlset(*(f"/evento/{n}" for n in range(60_000)))
    result, _ = run(job(), {"/sitemap.xml": (200, XML, big)})
    assert codes(result) & {"body_too_large", "sitemap_too_large"}
    assert result.status != Outcome.SUCCESS and not result.records


def test_gzip_bomb_is_refused_by_the_inflated_size_cap():
    bomb = gzip.compress(b"<urlset>" + b" " * 30_000_000 + b"</urlset>")
    result, _ = run(job(), {"/sitemap.xml": (200, XML, bomb)})
    assert "sitemap_too_large" in codes(result)
    assert not result.records


def test_robots_denied_sitemap_is_explicit():
    result, transport = run(
        job(),
        {"/sitemap.xml": (200, XML, urlset("/evento/1"))},
        robots="User-agent: *\nDisallow: /sitemap",
    )
    assert rows(result) == [(0, "/sitemap.xml", "robots_denied")]
    assert "robots_denied" in codes(result)
    assert result.status != Outcome.SUCCESS
    assert transport.fetched() == []


@pytest.mark.parametrize("kind", ["text/html", "application/json"])
def test_non_xml_media_type_is_refused(kind):
    result, _ = run(job(), {"/sitemap.xml": (200, kind, urlset("/evento/1"))})
    assert "unsupported_content_type" in codes(result)
    assert result.status == Outcome.PERMANENT_FAILURE


def test_empty_sitemap_is_missing_data_never_an_empty_success():
    result, _ = run(job(), {"/sitemap.xml": (200, XML, urlset())})
    assert result.status == Outcome.MISSING_DATA
    assert "sitemap_empty" in codes(result)


def test_wrong_root_element_is_invalid():
    result, _ = run(job(), {"/sitemap.xml": (200, XML, "<rss><channel/></rss>")})
    assert "sitemap_invalid" in codes(result)


def test_a_browser_rendered_acquirer_reports_the_sitemap_seed_unsupported():
    spec = job()
    acquirer = WebAcquirer(
        spec.acquisition.model_copy(update={"render": "browser"}),
        transport=Transport({}),
        resolver=Resolver(),
    )
    result = run_job(spec, acquisition=acquirer, base_dir=Path.cwd()).results[0]
    assert "sitemap_unsupported" in codes(result)
    assert result.status == Outcome.PERMANENT_FAILURE


def test_an_acquisition_port_without_xml_support_is_explicit():
    class HtmlOnly:
        async def acquire(self, source, *, max_bytes, representation):
            raise AssertionError("the sitemap must not be fetched as a page")

    result = run_job(job(), acquisition=HtmlOnly(), base_dir=Path.cwd()).results[0]
    assert "sitemap_unsupported" in codes(result)


def test_links_from_detail_pages_are_followed_past_the_sitemap():
    linked = event("Tango")[2].replace("</html>", '<a class="more" href="/evento/2">m</a></html>')
    pages = {
        "/sitemap.xml": (200, XML, urlset("/evento/1")),
        "/evento/1": (200, "text/html", linked),
        "/evento/2": event("Salsa"),
    }
    spec = job(spider={"max_depth": 2, "links": "a.more::attr(href)"})
    result, _ = run(spec, pages)
    assert [r.name for r in result.items] == ["Tango", "Salsa"]
    assert rows(result)[-1] == (2, "/evento/2", "fetched")
