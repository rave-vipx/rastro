"""Integration tests for starting a job from a goal or sites (DISC-001..003, P-002)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from rastro.application.acquisition import WebAcquirer
from rastro.application.job_new_sites import new_job_from_goal, new_job_from_sites
from rastro.cli import main
from rastro.domain.acquisition import (
    AcquisitionPolicy,
    AcquisitionResult,
    HttpResponse,
    PinnedTarget,
    ProxyRoute,
)
from rastro.domain.intelligence import InferencePolicy, ModelRequest
from rastro.domain.models import ConfigurationError, Outcome, Representation, Source
from rastro.ports.search import SearchHit

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

EVENTS_HTML = """
<html>
  <body>
    <div class="list">
      <article class="event">
        <h2 class="title">Concierto Sinfónico</h2>
        <span class="venue">Teatro Solís</span>
        <time class="starts_on" datetime="2026-12-01">1 Dec</time>
        <a class="url" href="/e/1">Details</a>
      </article>
      <article class="event">
        <h2 class="title">Ballet Nacional</h2>
        <span class="venue">Auditorio Adela Reta</span>
        <time class="starts_on" datetime="2026-12-05">5 Dec</time>
        <a class="url" href="/e/2">Details</a>
      </article>
      <article class="event">
        <h2 class="title">Milonga Callejera</h2>
        <span class="venue">Plaza Matriz</span>
        <time class="starts_on" datetime="2026-12-10">10 Dec</time>
        <a class="url" href="/e/3">Details</a>
      </article>
    </div>
  </body>
</html>
"""


class FakeSearchProvider:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits

    async def search(self, query: str, limit: int) -> tuple[SearchHit, ...]:
        return tuple(self.hits[:limit])


class FakeAcquirer:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.fetched: list[str] = []

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation = "html"
    ) -> AcquisitionResult:
        self.fetched.append(source.url)
        body = self.pages.get(source.url)
        if body is None:
            return AcquisitionResult(
                url=source.url,
                body=None,
                status=Outcome.PERMANENT_FAILURE,
                issue_code="http_404",
                http_status=404,
                requests=1,
            )
        return AcquisitionResult(
            url=source.url,
            body=body,
            status=Outcome.SUCCESS,
            issue_code=None,
            http_status=200,
            requests=1,
            bytes_received=len(body.encode("utf-8")),
        )


class FakeHttpTransport:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requests: list[str] = []

    async def get(
        self,
        target: PinnedTarget,
        *,
        headers: dict[str, str],
        timeout: float,
        max_bytes: int,
        proxy: ProxyRoute | None,
    ) -> HttpResponse:
        self.requests.append(target.url)
        if target.url.endswith("/robots.txt"):
            robots = b"User-agent: *\nAllow: /\n"
            return HttpResponse(status=200, headers={}, body=robots, bytes_received=len(robots))
        body = self.pages.get(target.url, "").encode("utf-8")
        return HttpResponse(
            status=200 if body else 404,
            headers={"content-type": "text/html; charset=utf-8"},
            body=body,
            bytes_received=len(body),
        )


class FakePublicResolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)


class FakeModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        self.calls += 1
        return self.response


def test_goal_without_approve_writes_nothing_and_lists_proposals(tmp_path: Path):
    """DISC-001, DISC-002: without --approve, print proposals only and write no file."""
    search = FakeSearchProvider(
        [
            SearchHit("https://approved.example/events", "Approved Events"),
            SearchHit("https://unapproved.example/events", "Other Events"),
        ]
    )
    acquirer = FakeAcquirer({"https://approved.example/events": EVENTS_HTML})
    job_path = tmp_path / "job.toml"
    result = new_job_from_goal(
        job_path,
        goal="cultural events in Puerto Claro",
        provider=search,
        acquisition=acquirer,
    )
    assert not job_path.exists()
    assert acquirer.fetched == []
    assert result.created is None
    assert [p.host for p in result.proposals] == ["approved.example", "unapproved.example"]
    assert all(p.origin == "search" for p in result.proposals)


def test_goal_with_approve_fetches_only_approved_host_up_to_3_pages_via_web_acquirer(
    tmp_path: Path,
):
    """DISC-002, DISC-003: WebAcquirer fetches at most 3 pages on approved host; writes job."""
    hits = [SearchHit(f"https://approved.example/p{i}", f"Page {i}") for i in range(1, 6)] + [
        SearchHit("https://unapproved.example/secret", "Unapproved"),
        SearchHit("https://sub.approved.example/events", "Subdomain not exact match"),
    ]
    pages = {f"https://approved.example/p{i}": EVENTS_HTML for i in range(1, 6)}
    pages["https://unapproved.example/secret"] = EVENTS_HTML
    pages["https://sub.approved.example/events"] = EVENTS_HTML

    transport = FakeHttpTransport(pages)
    web_acquirer = WebAcquirer(
        AcquisitionPolicy(allowed_hosts=("approved.example",), min_interval_seconds=0.0),
        transport=transport,
        resolver=FakePublicResolver(),
    )
    model = FakeModel("{}")
    job_path = tmp_path / "job.toml"

    result = new_job_from_goal(
        job_path,
        goal="cultural events",
        approve=("approved.example",),
        provider=FakeSearchProvider(hits),
        acquisition=web_acquirer,
        model=model,
    )
    assert model.calls == 0
    page_Reqs = [u for u in transport.requests if not u.endswith("/robots.txt")]
    assert page_Reqs == [
        "https://approved.example/p1",
        "https://approved.example/p2",
        "https://approved.example/p3",
    ]
    assert job_path.exists()
    parsed = tomllib.loads(job_path.read_text(encoding="utf-8"))
    assert parsed["acquisition"]["allowed_hosts"] == ["approved.example"]
    assert len(result.preview) == 9
    assert result.preview[0]["title"] == "Concierto Sinfónico"
    assert any(f.name == "title" and f.example == "Concierto Sinfónico" for f in result.fields)


async def _noop() -> None:
    return None


def test_no_observable_records_or_failed_fetch_writes_no_job(tmp_path: Path):
    """DISC-003: if no record can be observed or fetch fails, fail and write no job."""
    search = FakeSearchProvider([SearchHit("https://empty.example/events", "Empty")])
    acquirer = FakeAcquirer(
        {"https://empty.example/events": "<html><body><h1>Nothing</h1></body></html>"}
    )
    job_path = tmp_path / "job.toml"
    with pytest.raises(ConfigurationError, match="insufficient_evidence"):
        new_job_from_goal(
            job_path,
            goal="events",
            approve=("empty.example",),
            provider=search,
            acquisition=acquirer,
        )
    assert not job_path.exists()

    with pytest.raises(ConfigurationError, match="permanent_failure"):
        new_job_from_goal(
            job_path,
            goal="events",
            approve=("empty.example",),
            provider=search,
            acquisition=FakeAcquirer({}),
        )
    assert not job_path.exists()

    with pytest.raises(ConfigurationError, match="Pass -o"):
        new_job_from_goal(
            None,
            goal="events",
            approve=("empty.example",),
            provider=search,
            acquisition=acquirer,
        )


def test_sites_only_mode_uses_urls_as_approval_and_previews_real_records(tmp_path: Path):
    """DISC-003: `rastro new --site URL -o job.toml` proposes fields and previews real records."""
    acquirer = FakeAcquirer({"http://agenda.example/shows": EVENTS_HTML})
    job_path = tmp_path / "job.toml"
    result = new_job_from_sites(
        job_path,
        sites=("http://agenda.example/shows",),
        acquisition=acquirer,
    )
    assert job_path.exists()
    parsed = tomllib.loads(job_path.read_text(encoding="utf-8"))
    assert parsed["acquisition"]["allowed_hosts"] == ["agenda.example"]
    assert parsed["acquisition"]["allow_http"] is True
    assert parsed["sources"] == [{"kind": "url", "url": "http://agenda.example/shows"}]
    assert len(result.preview) == 3
    assert result.preview[0]["venue"] == "Teatro Solís"

    with pytest.raises(ConfigurationError, match="valid public HTTP"):
        new_job_from_sites(tmp_path / "bad.toml", sites=("ftp://bad.example",))


def test_cli_new_from_goal_and_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """DISC-001, DISC-002, DISC-003: CLI `rastro new --goal` and `rastro new --site`."""
    import rastro.cli_suggest as cli_suggest

    search = FakeSearchProvider(
        [
            SearchHit("https://approved.example/events", "Approved Events"),
            SearchHit("https://other.example/events", "Other"),
        ]
    )
    acquirer = FakeAcquirer({"https://approved.example/events": EVENTS_HTML})
    monkeypatch.setattr(cli_suggest, "_default_search_provider", lambda _args: search)
    monkeypatch.setattr(cli_suggest, "_default_acquirer", lambda _policy: acquirer)

    job_path = tmp_path / "goal_job.toml"
    assert main(["new", "--goal", "events", "-o", str(job_path)]) == 0
    proposal_out = json.loads(capsys.readouterr().out)
    assert not job_path.exists()
    assert len(proposal_out["proposals"]) == 2
    assert proposal_out["proposals"][0]["host"] == "approved.example"

    assert (
        main(
            [
                "new",
                "--goal",
                "events",
                "--approve",
                "approved.example",
                "--fields",
                "title,venue",
                "-o",
                str(job_path),
            ]
        )
        == 0
    )
    created_out = json.loads(capsys.readouterr().out)
    assert job_path.exists()
    assert created_out["allowed_hosts"] == ["approved.example"]
    assert created_out["preview"][0] == {"title": "Concierto Sinfónico", "venue": "Teatro Solís"}

    site_job = tmp_path / "site_job.toml"
    assert main(["new", "--site", "https://approved.example/events", "-o", str(site_job)]) == 0
    site_out = json.loads(capsys.readouterr().out)
    assert site_job.exists()
    assert len(site_out["preview"]) == 3

    assert main(["new", "--goal", "events", "--site", "https://approved.example/events"]) == 2
    assert main(["new", str(site_job), "-o", str(site_job), "--goal", "events"]) == 2
    assert main(["new", "--site", "https://approved.example/events"]) == 2
    assert main(["new", "--site", "ftp://bad.example", "-o", str(tmp_path / "x.toml")]) == 2
    assert (
        main(["new", "--goal", "events", "--approve", "bad/host", "-o", str(tmp_path / "y.toml")])
        == 2
    )


def test_cli_missing_http_extra_is_reported_not_masked_as_bad_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    """DISC-002: only host validation errors say 'exact hostnames'; a missing extra says so."""
    import rastro.cli_suggest as cli_suggest

    def missing(_policy: AcquisitionPolicy):
        raise ConfigurationError("Install rastro-engine[http] for HTTP acquisition")

    monkeypatch.setattr(cli_suggest, "_default_acquirer", missing)
    site = ["new", "--site", "https://approved.example/events", "-o", str(tmp_path / "a.toml")]
    assert main(site) == 2
    err = capsys.readouterr().err
    assert "rastro-engine[http]" in err and "exact hostnames" not in err
    goal = ["new", "--goal", "x", "--approve", "approved.example", "-o", str(tmp_path / "b.toml")]
    assert main(goal) == 2
    err = capsys.readouterr().err
    assert "rastro-engine[http]" in err and "exact hostnames" not in err
    assert (
        main(["new", "--goal", "x", "--approve", "bad/host", "-o", str(tmp_path / "c.toml")]) == 2
    )
    assert "exact hostnames" in capsys.readouterr().err
