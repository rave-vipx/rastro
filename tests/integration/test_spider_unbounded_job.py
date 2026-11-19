"""Unbounded crawl through a real job run: robots, hosts, request budget and resume."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro.application import spider_unbounded
from rastro.application.acquisition import WebAcquirer
from rastro.application.crawl_checkpoint import crawl_key, crawl_state
from rastro.application.jobs import run_job
from rastro.domain.acquisition import HttpResponse
from rastro.domain.jobs import Job
from rastro.domain.models import Outcome

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


class Resolver:
    async def resolve(self, host, port):
        return ("93.184.216.34",)


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


class Transport:
    def __init__(self, pages, robots="", clock=None, step=0.0):
        self.pages, self.robots, self.clock, self.step = pages, robots, clock, step
        self.urls: list[str] = []

    async def get(self, target, **kwargs):
        self.urls.append(target.url)
        path = urlsplit(target.url).path
        if path == "/robots.txt":
            raw = self.robots.encode()
            return HttpResponse(200 if raw else 404, {"content-type": "text/plain"}, raw, len(raw))
        if self.clock is not None:
            self.clock.now += self.step
        body = self.pages.get(path)
        raw = (body or "").encode()
        return HttpResponse(200 if body else 404, {"content-type": "text/html"}, raw, len(raw))

    def fetched(self):
        return [urlsplit(u).path for u in self.urls if urlsplit(u).path != "/robots.txt"]


def item(name, *hrefs):
    anchors = "".join(f'<a class="item" href="{href}">x</a>' for href in hrefs)
    return f"<html><body><article><h2>{name}</h2></article>{anchors}</body></html>"


def job(*, budget=30) -> Job:
    return Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"url": "https://shop.example/list"}],
            "fields": [{"name": "name", "type": "string"}],
            "recipe": {
                "strategy": "css",
                "record_selector": "article",
                "fields": {"name": "h2::text"},
            },
            "spider": {
                "mode": "spider",
                "links": "a.item::attr(href)",
                "max_pages": "unbounded",
                "time_budget_seconds": 60,
            },
            "acquisition": {
                "allowed_hosts": ["shop.example"],
                "max_requests": budget,
                "min_interval_seconds": 0,
                "max_retries": 0,
            },
        }
    )


SITE = {
    "/list": item("List", "/item/1", "/item/2", "/item/3", "https://evil.example/x"),
    "/item/1": item("Lamp", "/item/4"),
    "/item/2": item("Desk"),
    "/item/3": item("Chair"),
    "/item/4": item("Shade"),
}


def run(spec, transport):
    acquirer = WebAcquirer(spec.acquisition, transport=transport, resolver=Resolver())
    return run_job(spec, acquisition=acquirer, base_dir=Path.cwd()).results[0]


def test_robots_and_allowed_hosts_apply_unbounded():
    transport = Transport(SITE, robots="User-agent: *\nDisallow: /item/2\n")
    result = run(job(), transport)
    rows = {(p.path, p.outcome) for p in result.spider.pages}
    assert ("/item/2", "robots_denied") in rows
    assert ("/x", "off_host") in rows
    assert "/item/2" not in transport.fetched()
    assert not any("evil.example" in url for url in transport.urls)


def test_request_budget_semantics_unbounded():
    """An unbounded crawl is bounded by its time budget, not by ``max_requests``."""
    transport = Transport(SITE)
    result = run(job(budget=2), transport)
    assert sorted(transport.fetched()) == sorted(path for path in SITE if path != "/x")
    assert result.status == Outcome.SUCCESS
    assert result.pagination_stop == "spider_complete"
    assert result.notes is not None and result.notes.crawl is not None
    assert result.notes.crawl.stop == "complete"


def test_unbounded_job_resumes_without_refetch(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(spider_unbounded, "monotonic", clock)
    spec = job()
    with crawl_state(tmp_path, crawl_key(spec)):
        first_transport = Transport(SITE, clock=clock, step=25.0)
        first = run(spec, first_transport)
        second_transport = Transport(SITE, clock=clock, step=25.0)
        second = run(spec, second_transport)
    assert first.notes is not None and first.notes.crawl is not None
    assert first.notes.crawl.stop == "time_budget"
    assert set(first_transport.fetched()).isdisjoint(second_transport.fetched())
    assert second.notes is not None and second.notes.crawl is not None
    assert second.notes.crawl.resumed
    names = [r.name for r in first.items] + [r.name for r in second.items]
    assert sorted(names) == ["Chair", "Desk", "Lamp", "List", "Shade"]
