"""SCR-003, SEC-005: SDK consent parity and render-mode routing for undeclared URLs."""

from unittest.mock import AsyncMock

import pytest

from rastro.application.operator_trust import input_origin
from rastro.domain.acquisition import AcquisitionResult, HttpResponse
from rastro.domain.models import ConfigurationError, Outcome, Source

STEALTH_JOB = """format_version = 1
recipe_memory = "recipes.sqlite"

[acquisition]
allowed_hosts = ["example.com"]
min_interval_seconds = 0

[dataset]
name = "jobs"
path = "jobs.sqlite"
identity = ["title"]

[[sources]]
url = "https://example.com/"
render = "stealth"

[sources.recipe]
version = 1
strategy = "css"
record_selector = "p"

[sources.recipe.fields]
title = "::text"

[[fields]]
name = "title"
type = "string"
"""


class _Stop(Exception):
    pass


def _job(tmp_path):
    path = tmp_path / "job.toml"
    path.write_text(STEALTH_JOB, encoding="utf-8")
    return path


@pytest.mark.filterwarnings("ignore:allow_stealth= is deprecated:DeprecationWarning")
def test_load_job_file_forwards_allow_stealth(tmp_path, monkeypatch):
    import rastro.application.job_dataset as module

    seen = []

    def fake_run_job(job, **options):
        seen.append(options["allow_stealth"])
        raise _Stop

    monkeypatch.setattr(module, "run_job", fake_run_job)
    import rastro.adapters.playwright_browser as playwright
    import rastro.adapters.scrapling_stealth as stealth

    monkeypatch.setattr(stealth, "ScraplingStealth", lambda: AsyncMock())
    monkeypatch.setattr(playwright, "PlaywrightBrowser", lambda: AsyncMock())
    with pytest.raises(_Stop):
        module.load_job_file(_job(tmp_path), allow_stealth=True)
    assert seen == [True]
    # Untrusted input without a grant is refused up front, before run_job.
    for kwargs in ({"allow_stealth": False}, {}):
        with input_origin("mcp"), pytest.raises(ConfigurationError, match="for mcp input: stealth"):
            module.load_job_file(_job(tmp_path), **kwargs)
    assert seen == [True]


@pytest.mark.parametrize("entry", ["repair_job_source", "repair_job_sources", "promote_job_recipe"])
def test_repair_entry_points_forward_allow_stealth(entry, tmp_path, monkeypatch):
    import rastro.application.job_repair as module

    seen = []

    def fake_load(path, allow, **options):
        seen.append(options["allow_stealth"])
        raise _Stop

    monkeypatch.setattr(module.RepairContext, "load", staticmethod(fake_load))
    args = {
        "repair_job_source": (1,),
        "repair_job_sources": (None,),
        "promote_job_recipe": (1, 1),
    }[entry]
    extra = {"reason": "x"} if entry == "promote_job_recipe" else {}
    for flag in (True, False):
        with pytest.raises(_Stop):
            getattr(module, entry)(_job(tmp_path), *args, allow_stealth=flag, **extra)
    assert seen == [True, False]


@pytest.mark.filterwarnings("ignore:allow_stealth= is deprecated:DeprecationWarning")
@pytest.mark.asyncio
async def test_repair_evidence_fetch_renders_with_stealth_consent(tmp_path, monkeypatch):
    import rastro.adapters.dns as dns
    import rastro.adapters.http as http
    import rastro.adapters.scrapling_stealth as stealth
    from rastro.application.repair_context import RepairContext

    monkeypatch.delenv("RASTRO_ALLOW_STEALTH", raising=False)
    renderer = AsyncMock()
    renderer.render.return_value = AcquisitionResult(
        "https://example.com/", "<p>Rendered</p>", Outcome.SUCCESS, None, http_status=200
    )
    transport = AsyncMock()
    transport.get.return_value = HttpResponse(404, {}, b"", 0)
    resolver = AsyncMock()
    resolver.resolve.return_value = ("93.184.216.34",)
    monkeypatch.setattr(stealth, "ScraplingStealth", lambda: renderer)
    monkeypatch.setattr(http, "CurlTransport", lambda: transport)
    monkeypatch.setattr(dns, "SystemResolver", lambda: resolver)
    source = Source(url="https://example.com/")
    with input_origin("mcp"):
        with pytest.raises(ConfigurationError, match="for mcp input: stealth"):
            await RepairContext.load(_job(tmp_path), False).document(1, source)
        context = RepairContext.load(_job(tmp_path), False, allow_stealth=True)
        assert await context.document(1, source) == "<p>Rendered</p>"
    renderer.render.assert_awaited_once()


def _routing(modes, default="http"):
    from rastro.browser import _JobAcquisition

    clients = {name: AsyncMock() for name in {"http", "browser", "stealth"}}
    for name, client in clients.items():
        client.acquire.return_value = name
    return _JobAcquisition(modes, clients, default), clients


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "modes,default,url,expected",
    [
        # next-page URL on a host whose declared sources agree
        (
            {"https://example.com/a": "browser", "https://example.com/b": "browser"},
            "http",
            "https://example.com/a?page=2",
            "browser",
        ),
        # siblings disagree: job-level default
        (
            {"https://example.com/a": "browser", "https://example.com/b": "http"},
            "stealth",
            "https://example.com/a?page=2",
            "stealth",
        ),
        # exact host only: a subdomain or other host is not a sibling
        (
            {"https://example.com/a": "browser"},
            "http",
            "https://www.example.com/a?page=2",
            "http",
        ),
        # declared URLs are still routed by their own mode
        ({"https://example.com/a": "browser"}, "http", "https://example.com/a", "browser"),
    ],
)
async def test_unknown_url_uses_host_mode_or_default(modes, default, url, expected):
    routed, _ = _routing(modes, default)
    result = await routed.acquire(Source(url=url), max_bytes=100, representation="html")
    assert result == expected


def test_default_mode_gets_a_client_and_consent(monkeypatch):
    from rastro.browser import job_render_modes
    from rastro.domain.jobs import Job

    monkeypatch.delenv("RASTRO_ALLOW_STEALTH", raising=False)
    job = Job.model_validate(
        {
            "format_version": 1,
            "fields": [{"name": "title", "type": "string"}],
            "discover_recipes": True,
            "sources": [{"url": "https://example.com/a", "render": "browser"}],
            "acquisition": {"allowed_hosts": ["example.com"], "render": "stealth"},
        }
    )
    with input_origin("mcp"), pytest.raises(ConfigurationError, match="for mcp input: stealth"):
        job_render_modes(job)
    assert job_render_modes(job) == {"https://example.com/a": "browser"}


@pytest.mark.asyncio
async def test_next_page_on_the_same_host_is_rendered_in_the_sources_mode(monkeypatch):
    import rastro.adapters.dns as dns
    import rastro.adapters.http as http
    import rastro.adapters.playwright_browser as playwright
    from rastro.browser import job_acquisition
    from rastro.domain.jobs import Job

    renderer = AsyncMock()
    renderer.render.return_value = AcquisitionResult(
        "https://example.com/jobs?page=2", "<p>Page 2</p>", Outcome.SUCCESS, None, http_status=200
    )
    transport = AsyncMock()
    transport.get.return_value = HttpResponse(404, {}, b"", 0)
    resolver = AsyncMock()
    resolver.resolve.return_value = ("93.184.216.34",)
    monkeypatch.setattr(playwright, "PlaywrightBrowser", lambda: renderer)
    monkeypatch.setattr(http, "CurlTransport", lambda: transport)
    monkeypatch.setattr(dns, "SystemResolver", lambda: resolver)
    job = Job.model_validate(
        {
            "format_version": 1,
            "fields": [{"name": "title", "type": "string"}],
            "discover_recipes": True,
            "sources": [{"url": "https://example.com/jobs", "render": "browser"}],
            "acquisition": {"allowed_hosts": ["example.com"], "min_interval_seconds": 0},
        }
    )
    acquisition = job_acquisition(job, None)
    result = await acquisition.acquire(
        Source(url="https://example.com/jobs?page=2"), max_bytes=1000, representation="html"
    )
    assert result.body == "<p>Page 2</p>"
    renderer.render.assert_awaited_once()
    transport.get.assert_awaited_once()  # robots.txt only; the page itself was rendered
