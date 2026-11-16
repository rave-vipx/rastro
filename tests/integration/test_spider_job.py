"""Offline bounded spider: depth, page and request caps, hosts, paths, robots (N3).

Requirements: N3, SEC-001 (hosts), SEC-003 (robots), SEC-008 (budget), SYNC-002 (retention).
"""

import logging
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.acquisition import WebAcquirer
from rastro.application.dataset_load import load_results
from rastro.application.jobs import run_job
from rastro.domain.acquisition import HttpResponse
from rastro.domain.jobs import Job
from rastro.domain.models import Outcome

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


class Resolver:
    async def resolve(self, host, port):
        return ("93.184.216.34",)


class Transport:
    def __init__(self, pages, robots=""):
        self.pages = pages
        self.robots = robots
        self.urls = []

    async def get(self, target, **kwargs):
        self.urls.append(target.url)
        path = urlsplit(target.url).path
        if path == "/robots.txt":
            if not self.robots:
                return HttpResponse(404, {}, b"", 0)
            raw = self.robots.encode()
            return HttpResponse(200, {"content-type": "text/plain"}, raw, len(raw))
        status, body = self.pages.get(path, (404, ""))
        if status == 302:
            return HttpResponse(302, {"location": body}, b"", 0)
        raw = body.encode()
        return HttpResponse(status, {"content-type": "text/html"}, raw, len(raw))

    def fetched(self):
        return [u for u in self.urls if urlsplit(u).path != "/robots.txt"]


def links(*hrefs):
    anchors = "".join(f'<a class="item" href="{href}">x</a>' for href in hrefs)
    return f"<html><body>{anchors}</body></html>"


def item(name, *hrefs, next=None):
    anchors = "".join(f'<a class="item" href="{href}">x</a>' for href in hrefs)
    anchors += f'<a rel="next" href="{next}">n</a>' if next else ""
    return f"<html><body><article><h2>{name}</h2></article>{anchors}</body></html>"


def job(*, spider=None, budget=30, pagination=False, recipe=None, fields=None, hosts=None):
    recipe = recipe or {
        "strategy": "css",
        "record_selector": "article",
        "fields": {"name": "h2::text"},
    }
    if pagination:
        recipe["pagination"] = {"next": "a[rel=next]::attr(href)", "max_pages": 5}
    return Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"url": "https://shop.test/list"}],
            "fields": fields or [{"name": "name", "type": "string"}],
            "recipe": recipe,
            "spider": {
                "mode": "spider",
                "links": "a.item::attr(href)",
                **(spider or {}),
            },
            "acquisition": {
                "allowed_hosts": hosts or ["shop.test", "other.test"],
                "max_requests": budget,
                "min_interval_seconds": 0,
                "max_retries": 0,
            },
            "dataset": {"name": "shop", "path": "shop.sqlite", "identity": ["name"]},
        }
    )


def run(spec, pages, robots=""):
    transport = Transport(pages, robots)
    acquirer = WebAcquirer(spec.acquisition, transport=transport, resolver=Resolver())
    return run_job(spec, acquisition=acquirer, base_dir=Path.cwd()), transport


def outcomes(result):
    return [(p.depth, p.path, p.outcome) for p in result.spider.pages]


SITE = {
    "/list": (200, links("/item/1", "/item/2")),
    "/item/1": (200, item("Lamp", "/item/1/more")),
    "/item/2": (200, item("Desk")),
    "/item/1/more": (200, item("Shade")),
}


def test_spider_follows_links_and_combines_records():
    report, transport = run(job(), SITE)
    result = report.results[0]
    assert result.status == Outcome.SUCCESS
    assert [r.name for r in result.items] == ["Lamp", "Desk", "Shade"]
    assert result.pagination_stop == "spider_complete"
    assert outcomes(result)[0] == (0, "/list", "no_records")
    assert {o for _, _, o in outcomes(result)[1:]} == {"fetched"}
    assert len(transport.fetched()) == 4
    assert result.spider.pages[0].host == "shop.test"


def test_depth_cap_records_cut_links_and_is_partial():
    report, transport = run(job(spider={"max_depth": 1}), SITE)
    result = report.results[0]
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert result.pagination_stop == "spider_depth_limit"
    assert (2, "/item/1/more", "depth_limit") in outcomes(result)
    assert "https://shop.test/item/1/more" not in transport.urls
    assert sorted(r.name for r in result.items) == ["Desk", "Lamp"]


def test_page_cap_is_partial_and_keeps_accepted_records():
    report, transport = run(job(spider={"max_pages": 2}), SITE)
    result = report.results[0]
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert result.pagination_stop == "spider_page_limit"
    assert len(transport.fetched()) == 2
    assert [r.name for r in result.items] == ["Lamp"]
    assert ("page_limit" in {o for _, _, o in outcomes(result)}) is True


def test_page_cap_never_deletes_dataset_rows(tmp_path):
    spec = job()
    complete, _ = run(spec, SITE)
    store = SQLiteDatasetStore(tmp_path / "shop.sqlite")
    load_results(spec.dataset, ["name"], complete, store)
    capped, _ = run(job(spider={"max_pages": 2}), SITE)
    loaded = load_results(spec.dataset, ["name"], capped, store)
    assert loaded.sources[0].counts["missing"] == 0
    assert len(store.records("shop")) == 3


def test_off_host_link_is_refused_when_same_host():
    pages = {**SITE, "/list": (200, links("/item/2", "https://other.test/item/9"))}
    report, transport = run(job(), pages)
    result = report.results[0]
    assert ("off_host" in {o for _, _, o in outcomes(result)}) is True
    assert all(urlsplit(u).hostname == "shop.test" for u in transport.urls)
    assert result.status == Outcome.SUCCESS  # a filtered link is reported, not a failure


def test_off_host_allowed_only_when_same_host_false_and_host_allowed():
    pages = {
        **SITE,
        "/list": (200, links("/item/2", "https://other.test/item/9", "https://evil.test/item/8")),
        "/item/9": (200, item("Remote")),
    }
    report, transport = run(job(spider={"same_host": False}), pages)
    result = report.results[0]
    hosts = {urlsplit(u).hostname for u in transport.urls}
    assert hosts == {"shop.test", "other.test"}
    assert "Remote" in [r.name for r in result.items]
    assert ("off_host" in {o for _, _, o in outcomes(result)}) is True  # evil.test
    assert [p.host for p in result.spider.pages if p.outcome == "off_host"] == ["evil.test"]


def test_allow_and_deny_match_whole_segments():
    pages = {
        "/list": (200, links("/item/1", "/items/2", "/item", "/item/secret/3")),
        "/item/1": (200, item("One")),
        "/item": (200, item("Root")),
        "/items/2": (200, item("Other")),
        "/item/secret/3": (200, item("Hidden")),
    }
    spec = job(spider={"allow": ["/item"], "deny": ["/item/secret"]})
    report, transport = run(spec, pages)
    result = report.results[0]
    got = {p.path: p.outcome for p in result.spider.pages}
    assert got["/items/2"] == "path_denied"
    assert got["/item/secret/3"] == "path_denied"
    assert got["/item/1"] == "fetched" and got["/item"] == "fetched"
    assert not any(u.endswith(("/items/2", "/item/secret/3")) for u in transport.urls)


def test_robots_denied_discovered_page_is_recorded_not_swallowed():
    report, transport = run(job(), SITE, robots="User-agent: *\nDisallow: /item/2\n")
    result = report.results[0]
    assert (1, "/item/2", "robots_denied") in outcomes(result)
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert any(issue.code == "robots_denied" for issue in result.issues)
    assert "https://shop.test/item/2" not in transport.urls


def test_shared_request_budget_is_not_reset_by_pagination_inside_a_spider_page():
    pages = {
        "/list": (200, links("/item/1", "/item/2", "/item/3")),
        "/item/1": (200, item("One", next="/item/1b")),
        "/item/1b": (200, item("OneB", next="/item/1c")),
        "/item/2": (200, item("Two")),
        "/item/3": (200, item("Three")),
    }
    report, transport = run(job(budget=6, pagination=True), pages)
    result = report.results[0]
    assert len(transport.urls) == 6  # robots + page, three times: one budget for the source
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert result.pagination_stop == "spider_budget"
    assert "budget" in {o for _, _, o in outcomes(result)}
    assert {r.name for r in result.items} == {"One", "OneB"}


def test_page_cap_counts_pagination_documents_too():
    pages = {
        "/list": (200, links("/item/1")),
        "/item/1": (200, item("One", next="/item/1b")),
        "/item/1b": (200, item("OneB", next="/item/1c")),
        "/item/1c": (200, item("OneC")),
    }
    report, transport = run(job(spider={"max_pages": 3}, pagination=True), pages)
    result = report.results[0]
    assert transport.fetched() == [
        "https://shop.test/list",
        "https://shop.test/item/1",
        "https://shop.test/item/1b",
    ]
    assert result.pagination_stop == "spider_page_limit"
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert {r.name for r in result.items} == {"One", "OneB"}


def test_duplicate_links_are_fetched_once_and_queries_never_logged(caplog):
    pages = {
        "/list": (200, links("/item/1", "/item/1#top", "/item/1", "/item/2?token=SECRET-Q")),
        "/item/1": (200, item("One")),
        "/item/2": (200, item("Two")),
    }
    with caplog.at_level(logging.DEBUG):
        report, transport = run(job(), pages)
    result = report.results[0]
    assert transport.urls.count("https://shop.test/item/1") == 1
    assert "https://shop.test/item/2?token=SECRET-Q" in transport.urls  # the query is kept
    assert "SECRET-Q" not in result.spider.model_dump_json()
    assert "SECRET-Q" not in caplog.text
    assert len(result.spider.pages) == 3


def test_spider_starts_only_from_sources_and_robots_cannot_be_granted_by_the_job():
    report, transport = run(job(), SITE)
    assert transport.urls[1] == "https://shop.test/list"  # [0] is robots.txt
    assert report.results[0].spider.pages[0].ordinal == 1


def test_start_page_failure_is_explicit_not_empty_success():
    report, transport = run(job(), {"/list": (500, "private server content")})
    result = report.results[0]
    assert result.status == Outcome.TRANSIENT_FAILURE
    assert not result.records
    assert result.spider.pages[0].outcome == "failed"
    assert len(transport.fetched()) == 1


def test_no_records_anywhere_is_missing_data_not_success():
    pages = {"/list": (200, links("/a")), "/a": (200, links())}
    report, _ = run(job(), pages)
    assert report.results[0].status == Outcome.MISSING_DATA


def test_link_field_reads_the_records_own_link():
    pages = {
        "/list": (
            200,
            '<article><h2>Hub</h2><a href="/item/2">go</a></article>'
            '<article><h2>Dup</h2><a href="/item/2">go</a></article>',
        ),
        "/item/2": (200, '<article><h2>Two</h2><a href="/list">back</a></article>'),
    }
    recipe = {
        "strategy": "css",
        "record_selector": "article",
        "fields": {"name": "h2::text", "url": "a::attr(href)"},
    }
    spec = job(
        recipe=recipe,
        spider={"links": None, "link_field": "url"},
        fields=[{"name": "name", "type": "string"}, {"name": "url", "type": "string"}],
    )
    report, transport = run(spec, pages)
    result = report.results[0]
    assert transport.fetched() == ["https://shop.test/list", "https://shop.test/item/2"]
    assert {r.name for r in result.items} == {"Hub", "Dup", "Two"}


def test_list_mode_is_todays_behaviour():
    spec = job(spider={"mode": "list"})
    report, transport = run(spec, SITE)
    assert transport.fetched() == ["https://shop.test/list"]
    assert report.results[0].spider is None


@pytest.mark.parametrize(
    "href", ["http://127.0.0.1/item/1", "http://localhost/item/1", "http://10.0.0.1/item/1"]
)
def test_private_destinations_are_never_followed(href):
    pages = {**SITE, "/list": (200, links("/item/2", href))}
    report, transport = run(job(spider={"same_host": False}), pages)
    assert {urlsplit(u).hostname for u in transport.urls} == {"shop.test"}
    assert "off_host" in {o for _, _, o in outcomes(report.results[0])}


def test_redirect_off_host_is_a_recorded_failure_not_a_fetch():
    pages = {**SITE, "/item/2": (302, "https://other.test/elsewhere")}
    report, transport = run(job(), pages)
    result = report.results[0]
    assert (1, "/item/2", "off_host") in outcomes(result)
    assert not any(u.startswith("https://other.test/elsewhere") for u in transport.urls)
    assert result.status == Outcome.PARTIAL_SUCCESS


def test_operator_robots_grant_applies_to_spider_pages_and_the_job_cannot_make_one():
    robots = "User-agent: *\nDisallow: /item/2\n"
    spec = job()
    transport = Transport(SITE, robots)
    acquirer = WebAcquirer(spec.acquisition, transport=transport, resolver=Resolver())
    report = run_job(spec, acquisition=acquirer, base_dir=Path.cwd(), ignore_robots=["shop.test"])
    result = report.results[0]
    assert "https://shop.test/item/2" in transport.urls
    assert result.metrics.robots_overridden is True
    assert {o for _, _, o in outcomes(result)} <= {"fetched", "no_records"}


def test_many_refusals_are_counted_but_the_report_stays_bounded():
    hub = links(*[f"https://other{i}.test/x" for i in range(700)], "/item/2")
    report, _ = run(job(), {**SITE, "/list": (200, hub)})
    result = report.results[0]
    assert len(result.spider.pages) <= 600
    assert result.spider.unreported > 0
    assert (1, "/item/2", "fetched") in outcomes(result)
    assert result.spider.discovered == 702  # start + 700 + /item/2


def test_a_discovering_job_without_a_stored_recipe_refuses_to_spider():
    spec = job().model_copy(update={"recipe": None, "discover_recipes": True})
    report, transport = run(spec, SITE)
    result = report.results[0]
    assert result.status == Outcome.PERMANENT_FAILURE
    assert [issue.code for issue in result.issues] == ["spider_needs_recipe"]
    assert transport.urls == []


def test_a_link_to_a_port_the_fetch_would_refuse_never_takes_a_page_slot():
    hub = links(*[f"https://shop.test:8443/x{i}" for i in range(5)], "/item/2")
    report, transport = run(job(spider={"max_pages": 3}), {**SITE, "/list": (200, hub)})
    result = report.results[0]
    got = {p.path: p.outcome for p in result.spider.pages}
    assert [got[f"/x{i}"] for i in range(5)] == ["port_not_allowed"] * 5
    assert got["/item/2"] == "fetched"
    assert "https://shop.test/item/2" in transport.urls
    assert result.status == Outcome.SUCCESS
    assert result.pagination_stop == "spider_complete"


def test_an_operator_granted_port_is_still_followed():
    spec = job()
    pages = {**SITE, "/list": (200, links("https://shop.test:8443/item/2"))}
    transport = Transport(pages)
    acquirer = WebAcquirer(spec.acquisition, transport=transport, resolver=Resolver())
    report = run_job(spec, acquisition=acquirer, base_dir=Path.cwd(), allow_ports=[8443])
    assert "https://shop.test:8443/item/2" in transport.urls
    assert {o for _, _, o in outcomes(report.results[0])} <= {"fetched", "no_records"}


def test_path_params_never_reach_the_report_and_cannot_dodge_a_deny():
    pages = {
        "/list": (
            200,
            links(
                "/item/1;jsessionid=SESSIONSECRET", "https://shop.test//login", "/login;x=1", "/ok"
            ),
        ),
        "/item/1": (200, item("One")),
        "/ok": (200, item("Ok")),
        "/login": (200, item("Login")),
    }
    report, transport = run(job(spider={"deny": ["/login"]}), pages)
    result = report.results[0]
    assert "SESSIONSECRET" not in result.spider.model_dump_json()
    got = [p.path for p in result.spider.pages]
    assert "/item/1" in got
    assert [p.outcome for p in result.spider.pages if p.path == "/login"] == ["path_denied"] * 2
    assert not any("login" in url for url in transport.fetched())
