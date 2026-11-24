"""Unbounded crawl through a real job run: robots, hosts, request budget and resume."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro.application import spider_unbounded
from rastro.application.acquisition import WebAcquirer
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


def run(spec, transport, folder: Path):
    """Checkpoints live under ``folder``: a per-test directory, never the shared cwd."""
    acquirer = WebAcquirer(spec.acquisition, transport=transport, resolver=Resolver())
    return run_job(spec, acquisition=acquirer, base_dir=folder).results[0]


def test_robots_and_allowed_hosts_apply_unbounded(tmp_path):
    transport = Transport(SITE, robots="User-agent: *\nDisallow: /item/2\n")
    result = run(job(), transport, tmp_path)
    rows = {(p.path, p.outcome) for p in result.spider.pages}
    assert ("/item/2", "robots_denied") in rows
    assert ("/x", "off_host") in rows
    assert "/item/2" not in transport.fetched()
    assert not any("evil.example" in url for url in transport.urls)


def test_request_budget_semantics_unbounded(tmp_path):
    """An unbounded crawl is bounded by its time budget, not by ``max_requests``."""
    transport = Transport(SITE)
    result = run(job(budget=2), transport, tmp_path)
    assert sorted(transport.fetched()) == sorted(path for path in SITE if path != "/x")
    assert result.status == Outcome.SUCCESS
    assert result.pagination_stop == "spider_complete"
    assert result.notes is not None and result.notes.crawl is not None
    assert result.notes.crawl.stop == "complete"


def test_unbounded_job_resumes_without_refetch(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(spider_unbounded, "monotonic", clock)
    spec = job()
    first_transport = Transport(SITE, clock=clock, step=25.0)
    first = run(spec, first_transport, tmp_path)
    second_transport = Transport(SITE, clock=clock, step=25.0)
    second = run(spec, second_transport, tmp_path)
    assert first.notes is not None and first.notes.crawl is not None
    assert first.notes.crawl.stop == "time_budget"
    assert set(first_transport.fetched()).isdisjoint(second_transport.fetched())
    assert second.notes is not None and second.notes.crawl is not None
    assert second.notes.crawl.resumed
    names = [r.name for r in first.items] + [r.name for r in second.items]
    assert sorted(names) == ["Chair", "Desk", "Lamp", "List", "Shade"]


def test_unbounded_crawl_runs_without_the_crawl_engine(tmp_path, monkeypatch):
    """An HTTP-only install (no browser extra) paces with the stdlib throttle."""
    monkeypatch.setitem(sys.modules, "scrapling.spiders.throttle", None)
    monkeypatch.setitem(sys.modules, "scrapling.spiders", None)
    transport = Transport(SITE)
    result = run(job(), transport, tmp_path)
    assert result.status == Outcome.SUCCESS
    assert result.notes is not None and result.notes.crawl is not None
    assert result.notes.crawl.stop == "complete"


class Crash(BaseException):
    """A process that dies mid-crawl: nothing below the job run may catch it."""


class Crashing(Transport):
    def __init__(self, *args, crash_at, **kwargs):
        super().__init__(*args, **kwargs)
        self.crash_at = crash_at

    async def get(self, target, **kwargs):
        if urlsplit(target.url).path == self.crash_at:
            raise Crash
        return await super().get(target, **kwargs)


def test_a_crashed_unbounded_crawl_resumes_from_its_periodic_checkpoint(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(spider_unbounded, "monotonic", clock)
    spec = job()
    spec = spec.model_copy(
        update={"spider": spec.spider.model_copy(update={"time_budget_seconds": 3600})}
    )
    with pytest.raises((Crash, BaseExceptionGroup)):
        run(spec, Crashing(SITE, clock=clock, step=40.0, crash_at="/item/4"), tmp_path)
    saved = list(tmp_path.rglob("*.json"))
    assert len(saved) == 1  # written while crawling, not at a stop that never came
    second = run(spec, Transport(SITE, clock=clock, step=1.0), tmp_path)
    assert second.notes is not None and second.notes.crawl is not None
    assert second.notes.crawl.resumed and second.notes.crawl.stop == "complete"
    assert sorted(r.name for r in second.items) == ["Chair", "Desk", "Lamp", "List", "Shade"]
