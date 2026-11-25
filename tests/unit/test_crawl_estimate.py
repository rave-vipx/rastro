"""Discovery note counts and crawl dry-run estimation without network access."""

import json
import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro.application.acquisition import WebAcquirer
from rastro.application.crawl_estimate import CrawlEstimate, estimate
from rastro.application.jobs import run_job
from rastro.application.run_report import report_for_run
from rastro.application.spider_links import LinkHarvest
from rastro.cli import main
from rastro.domain.acquisition import AcquisitionPolicy, HttpResponse, PinnedTarget
from rastro.domain.browser_controls import HostLimits
from rastro.domain.jobs import Job
from rastro.domain.models import Limits
from rastro.domain.source_notes import DiscoveryNote
from rastro.domain.spider import SpiderPolicy

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

XML = "application/xml"
NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


class _Resolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)


class _Transport:
    def __init__(self, pages: dict[str, tuple[int, str, str]]) -> None:
        self.pages = pages

    async def get(self, target: PinnedTarget, **kwargs: object) -> HttpResponse:
        path = urlsplit(target.url).path
        if path == "/robots.txt":
            return HttpResponse(404, {"content-type": "text/plain"}, b"", 0)
        status, kind, body = self.pages.get(path, (404, "text/html", ""))
        raw = body.encode()
        return HttpResponse(status, {"content-type": kind}, raw, len(raw))


def _urlset(*locs: str | tuple[str, str | None]) -> str:
    rows = "".join(
        f"<url><loc>{loc[0]}</loc>"
        + (f"<lastmod>{loc[1]}</lastmod>" if loc[1] is not None else "")
        + "</url>"
        if isinstance(loc, tuple)
        else f"<url><loc>{loc}</loc></url>"
        for loc in locs
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="{NS}">{rows}</urlset>'


def _index(*locs: str) -> str:
    rows = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return f'<?xml version="1.0"?><sitemapindex xmlns="{NS}">{rows}</sitemapindex>'


def _event(name: str) -> tuple[int, str, str]:
    data = json.dumps({"@context": "https://schema.org", "@type": "Event", "name": name})
    return (200, "text/html", f'<html><script type="application/ld+json">{data}</script></html>')


def _sitemap_job(
    *,
    spider: dict[str, object] | None = None,
    acquisition: dict[str, object] | None = None,
) -> Job:
    return Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"url": "https://agenda.example/sitemap.xml"}],
            "fields": [{"name": "name", "type": "string"}],
            "recipe": {"strategy": "jsonld", "item_type": "Event", "fields": {"name": "/name"}},
            "spider": {"mode": "spider", "seed": "sitemap", "max_depth": 1, **(spider or {})},
            "acquisition": {
                "allowed_hosts": ["agenda.example"],
                "max_requests": 60,
                "min_interval_seconds": 0,
                "max_retries": 0,
                **(acquisition or {}),
            },
        }
    )


def _run_sitemap(spec: Job, pages: dict[str, tuple[int, str, str]]):
    transport = _Transport(pages)
    assert spec.acquisition is not None
    acquirer = WebAcquirer(spec.acquisition, transport=transport, resolver=_Resolver())
    return run_job(spec, acquisition=acquirer, base_dir=Path.cwd())


def test_discovery_note_counts_filtered() -> None:
    locs = [f"https://agenda.example/item/{i}" for i in range(1, 7)] + [
        f"https://agenda.example/keep/{i}" for i in range(1, 5)
    ]
    pages = {"/sitemap.xml": (200, XML, _urlset(*locs))} | {
        f"/keep/{i}": _event(f"Event {i}") for i in range(1, 5)
    }
    run_result = _run_sitemap(_sitemap_job(spider={"allow_pattern": r"^/keep/\d+$"}), pages)
    result = run_result.results[0]
    assert result.notes is not None
    note = result.notes.discovery
    assert note == DiscoveryNote(
        seed="sitemap", order="document", locs_found=10, locs_filtered=6, locs_admitted=4
    )
    assert run_result.report is not None and run_result.report.sources[0].discovery == note


def test_lastmod_order_in_report() -> None:
    entries: list[tuple[str, str | None]] = [
        ("https://agenda.example/item/old", "2026-01-01"),
        ("https://agenda.example/item/new", "2026-06-01"),
        ("https://agenda.example/skip/1", "2026-07-01"),
    ]
    pages = {
        "/sitemap.xml": (200, XML, _urlset(*entries)),
        "/item/old": _event("Old"),
        "/item/new": _event("New"),
    }
    spec = _sitemap_job(spider={"seed_order": "lastmod", "allow": ["/item"], "max_pages": 2})
    report = report_for_run("job.toml", _run_sitemap(spec, pages))
    assert report.sources[0].discovery == DiscoveryNote(
        seed="sitemap", order="lastmod", locs_found=3, locs_filtered=1, locs_admitted=1
    )


def test_link_harvest_exposes_discovery_note_and_index_totals() -> None:
    from rastro.adapters.scrapling import ScraplingParser

    policy = SpiderPolicy(mode="spider", seed="sitemap", seed_order="reverse")
    harvest = LinkHarvest(policy, ScraplingParser(), Limits())
    assert harvest.discovery == DiscoveryNote(seed="sitemap", order="reverse")

    pages = {
        "/sitemap.xml": (200, XML, _index("/s1.xml", "/s2.xml")),
        "/s1.xml": (200, XML, _urlset("/item/1", "/skip/1")),
        "/s2.xml": (200, XML, _urlset("/item/2")),
        "/item/1": _event("One"),
        "/item/2": _event("Two"),
    }
    run_result = _run_sitemap(
        _sitemap_job(spider={"seed_order": "reverse", "allow": ["/item"]}), pages
    )
    assert run_result.results[0].notes is not None
    assert run_result.results[0].notes.discovery == DiscoveryNote(
        seed="sitemap", order="reverse", locs_found=3, locs_filtered=1, locs_admitted=2
    )


def test_estimate_never_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: object, **kwargs: object) -> object:
        raise OSError("network access is forbidden during estimate")

    for attr in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, attr, _blocked)

    est = estimate(_sitemap_job(spider={"max_pages": 25}, acquisition={"max_requests": 30}))
    assert isinstance(est, CrawlEstimate) and est.requests_max == 25
    assert 0 <= est.minutes_min <= est.minutes_max


def test_estimate_uses_host_interval() -> None:
    spec = _sitemap_job(
        spider={"max_pages": 100},
        acquisition={"max_requests": 100, "min_interval_seconds": 1.0},
    )
    est = estimate(spec)
    assert est.requests_max == 100 and 1.6 <= est.minutes_min <= est.minutes_max

    host_policy = AcquisitionPolicy(
        allowed_hosts=("agenda.example",), max_requests=100, min_interval_seconds=0.1
    )
    object.__setattr__(
        host_policy, "hosts", {"agenda.example": HostLimits(min_interval_seconds=1.0)}
    )
    host_est = estimate(spec.model_copy(update={"acquisition": host_policy}))
    assert host_est.requests_max == 100 and host_est.minutes_min >= 1.6


def test_cli_job_estimate_outputs_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    job_file = tmp_path / "job.toml"
    spec = _sitemap_job(
        spider={"max_pages": 100},
        acquisition={"max_requests": 100, "min_interval_seconds": 1.0},
    )
    job_file.write_text(spec.model_dump_json(), encoding="utf-8")
    job_json = tmp_path / "job.json"
    job_json.write_text(spec.model_dump_json(), encoding="utf-8")
    assert main(["job", "estimate", str(job_json)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["requests_max"] == 100
    assert 1.6 <= payload["minutes_min"] <= payload["minutes_max"]


def test_estimate_file_sources_and_non_spider_pagination() -> None:
    file_job = Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"path": "page.html"}],
            "fields": [{"name": "name", "type": "string"}],
            "recipe": {"strategy": "css", "record_selector": "h1", "fields": {"name": "::text"}},
        }
    )
    assert estimate(file_job) == CrawlEstimate(requests_max=0, minutes_min=0.0, minutes_max=0.0)

    paginated_job = Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"url": "https://agenda.example/list"}],
            "fields": [{"name": "name", "type": "string"}],
            "recipe": {
                "strategy": "css",
                "record_selector": "h1",
                "fields": {"name": "::text"},
                "pagination": {"next": "a.next::attr(href)", "max_pages": 5},
            },
            "scroll": {"max_steps": 2},
            "acquisition": {
                "allowed_hosts": ["agenda.example"],
                "max_requests": 30,
                "min_interval_seconds": 1.0,
            },
        }
    )
    est = estimate(paginated_job)
    assert est.requests_max == 15 and 0 < est.minutes_min <= est.minutes_max
