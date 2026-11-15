"""Unit tests for goal-driven source proposals and SearXNG adapter (DISC-001..002, SEC-005)."""

from __future__ import annotations

import json

import httpx
import pytest

from rastro.adapters.searxng import SearXNGSearchProvider
from rastro.application.source_proposals import (
    SourceProposal,
    acquire_approved_pages,
    clean_proposal,
    propose_sources,
)
from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest
from rastro.domain.models import ConfigurationError
from rastro.ports.search import SearchHit, SearchProvider

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


class FakeSearchProvider:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.queries: list[tuple[str, int]] = []

    async def search(self, query: str, limit: int) -> tuple[SearchHit, ...]:
        self.queries.append((query, limit))
        return tuple(self.hits[:limit])


class FakeModel:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls = 0

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_search_provider_protocol_and_proposals_from_search():
    """DISC-001..002: SearchProvider returns proposals with origin='search' and 0 model calls."""
    provider = FakeSearchProvider(
        [
            SearchHit("https://agenda.example/events?utm=1#top", "Agenda\x1b[31m Events"),
            SearchHit("https://tickets.example.org/shows", "Tickets"),
            SearchHit("https://agenda.example/events?dup=2", "Duplicate stripped query"),
        ]
    )
    assert isinstance(provider, SearchProvider)
    model = FakeModel('{"sources": []}')
    proposals = propose_sources("dance events in uruguay", search=provider, model=model)
    assert model.calls == 0
    assert proposals == (
        SourceProposal(
            url="https://agenda.example/events",
            host="agenda.example",
            title="Agenda [31m Events",
            origin="search",
        ),
        SourceProposal(
            url="https://tickets.example.org/shows",
            host="tickets.example.org",
            title="Tickets",
            origin="search",
        ),
    )


def test_model_proposed_junk_urls_are_filtered_and_capped():
    """SEC-005, DISC-001: model output is untrusted; keep only valid public HTTP(S) URLs, cap 10."""
    raw_items = [
        {"url": "ftp://files.example/list", "title": "FTP"},
        {"url": "javascript:alert(1)", "title": "XSS"},
        {"url": "file:///etc/passwd", "title": "Local"},
        {"url": "https://user:secret@private.example/p", "title": "Creds"},
        {"url": "http://127.0.0.1:8080/internal", "title": "Loopback port"},
        {"url": "http://127.0.0.1/internal", "title": "Loopback"},
        {"url": "http://169.254.169.254/latest/meta-data", "title": "Metadata"},
        {"url": "http://93.184.216.34/public-ip", "title": "Public IP"},
        {"url": "http://localhost/admin", "title": "Localhost"},
        {"url": "https://test.invalid/page", "title": "Reserved invalid"},
        {"url": "https://bad\x00host.example/p", "title": "Control char"},
        "https://string-entry.example/list",
        {"url": "not a url", "title": "Garbage"},
        *[
            {"url": f"https://site-{i}.example/events?secret={i}", "title": f"Site {i}"}
            for i in range(1, 15)
        ],
    ]
    model = FakeModel(json.dumps({"sources": raw_items}))
    proposals = propose_sources("find events", search="model", model=model)
    assert model.calls == 1
    assert len(proposals) == 10
    assert all(p.origin == "model" for p in proposals)
    assert proposals[0].url == "http://93.184.216.34/public-ip"
    assert proposals[1].url == "https://string-entry.example/list"
    assert clean_proposal("https://[::1]/x", "", "model") is None


def test_model_errors_and_invalid_inputs_raise_configuration_error():
    """SEC-005: invalid goal, missing model, or malformed model output raises ConfigurationError."""
    with pytest.raises(ConfigurationError, match="Goal text"):
        propose_sources("   ")
    with pytest.raises(ConfigurationError, match="--search model"):
        propose_sources("events", search="model", model=None)
    with pytest.raises(ConfigurationError, match="valid JSON"):
        propose_sources("events", search="model", model=FakeModel("not json"))
    with pytest.raises(ConfigurationError, match="valid JSON"):
        propose_sources("events", search="model", model=FakeModel(ModelError("boom")))
    with pytest.raises(ConfigurationError, match="valid JSON"):
        propose_sources(
            "events",
            search="model",
            model=FakeModel('{"sources": []}'),
            policy=InferencePolicy(max_response_bytes=5),
        )
    with pytest.raises(ConfigurationError, match="'sources' list"):
        propose_sources("events", search="model", model=FakeModel('{"other": 123}'))


def test_no_provider_and_no_model_raises_configuration_error(monkeypatch):
    """DISC-001, P-002: missing both search provider and model raises ConfigurationError."""
    monkeypatch.delenv("RASTRO_SEARXNG_URL", raising=False)
    monkeypatch.delenv("RASTRO_MODEL", raising=False)
    with pytest.raises(ConfigurationError, match="RASTRO_SEARXNG_URL") as exc_info:
        propose_sources("dance events")
    assert "model" in str(exc_info.value).lower()

    model = FakeModel('{"sources": [{"url": "https://fallback.example/a"}]}')
    proposals = propose_sources("dance events", model=model)
    assert len(proposals) == 1 and proposals[0].origin == "model"


def test_searxng_adapter_validation_and_error_handling(monkeypatch):
    """DISC-001, SEC-004: SearXNG adapter validates URLs, sizes, status and JSON strictly."""
    monkeypatch.delenv("RASTRO_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("RASTRO_SEARXNG_URL", raising=False)
    with pytest.raises(ConfigurationError, match="RASTRO_SEARXNG_URL"):
        SearXNGSearchProvider()
    with pytest.raises(ConfigurationError, match="allow_remote"):
        SearXNGSearchProvider("https://searx.example/search")
    with pytest.raises(ConfigurationError, match="HTTPS"):
        SearXNGSearchProvider("http://searx.example/search", allow_remote=True)
    for bad in (
        "ftp://127.0.0.1/s",
        "http://u:p@127.0.0.1/s",
        "http://127.0.0.1:0/s",
        "http://127.0.0.1/../s",
    ):
        with pytest.raises(ConfigurationError, match="SearXNG origin"):
            SearXNGSearchProvider(bad)

    ipv6 = SearXNGSearchProvider("http://[::1]:8080")
    assert "[::1]:8080/search" in repr(ipv6)
    import asyncio

    with (
        asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner,
        pytest.raises(ConfigurationError, match="Search query"),
    ):
        runner.run(ipv6.search("ok", limit=99))

    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params["q"] == "tango"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://tango.example/agenda", "title": "Tango Agenda"},
                    {"url": "https://milonga.example/list", "title": "Milongas"},
                ]
            },
        )

    monkeypatch.setenv("RASTRO_SEARXNG_URL", "https://searx.example")
    adapter = SearXNGSearchProvider(allow_remote=True, transport=httpx.MockTransport(ok_handler))
    proposals = propose_sources("tango", provider=adapter)
    assert len(proposals) == 2 and proposals[0].host == "tango.example"

    for resp, match in (
        (httpx.Response(503), "non-200"),
        (httpx.Response(200, headers={"content-encoding": "x-custom"}, content=b"{}"), "identity"),
        (httpx.Response(200, content=b"x" * 100), "max_response_bytes"),
        (httpx.Response(200, content=b"not-json"), "invalid JSON"),
        (httpx.Response(200, json={"bad": 1}), "'results' list"),
    ):
        bad_adapter = SearXNGSearchProvider(
            "http://127.0.0.1:8080",
            max_response_bytes=50,
            transport=httpx.MockTransport(lambda _r, r=resp: r),
        )
        with pytest.raises(ConfigurationError, match=match):
            propose_sources("tango", provider=bad_adapter)


def test_acquire_approved_pages_validation():
    """DISC-002: acquire_approved_pages rejects invalid or empty host lists."""
    with pytest.raises(ConfigurationError, match="Approved hosts"):
        acquire_approved_pages(["https://a.example/1"], ["bad/host"])
    with pytest.raises(ConfigurationError, match="At least one approved host"):
        acquire_approved_pages(["https://a.example/1"], ["   "])
    hosts, _policy, results = acquire_approved_pages(
        ["https://other.example/1"], ["approved.example"]
    )
    assert hosts == ("approved.example",)
    assert results == ()


def test_searxng_without_httpx_raises_a_configuration_error(monkeypatch: pytest.MonkeyPatch):
    """DISC-001: httpx lives in optional extras; a missing one names the extra to install."""
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "httpx", None)
    monkeypatch.delitem(sys.modules, "rastro.adapters.searxng", raising=False)
    monkeypatch.setenv("RASTRO_SEARXNG_URL", "http://127.0.0.1:8080")
    importlib.import_module("rastro.adapters.searxng")  # module import never needs httpx
    for search in ("searxng", None):
        with pytest.raises(ConfigurationError, match=r"rastro-engine\[openai\]"):
            propose_sources("tango", search=search)


def test_searxng_accepts_localhost_as_loopback(monkeypatch: pytest.MonkeyPatch):
    """SEC-004: localhost is loopback like 127.0.0.1; remote names still need opt-in."""
    monkeypatch.delenv("RASTRO_ALLOW_REMOTE", raising=False)
    assert "http://localhost:8080/search" in repr(SearXNGSearchProvider("http://localhost:8080"))
    assert "http://localhost/search" in repr(SearXNGSearchProvider("http://LOCALHOST"))
    with pytest.raises(ConfigurationError, match="allow_remote"):
        SearXNGSearchProvider("http://localhost.example.com:8080")


def test_searxng_goal_does_not_resolve_the_model(monkeypatch: pytest.MonkeyPatch):
    """DISC-001: choosing SearXNG never resolves RASTRO_MODEL; --search model says so."""
    monkeypatch.setenv("RASTRO_MODEL", "not a valid model spec")
    monkeypatch.setenv("RASTRO_SEARXNG_URL", "http://127.0.0.1:8080")

    async def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"url": "https://a.example/x", "title": "A"}]})

    adapter = SearXNGSearchProvider(transport=httpx.MockTransport(handler))
    assert propose_sources("tango", provider=adapter)[0].host == "a.example"
    assert propose_sources("tango", provider=adapter, search="searxng")[0].host == "a.example"
    monkeypatch.delenv("RASTRO_MODEL")
    with pytest.raises(ConfigurationError, match="--search model") as info:
        propose_sources("tango", search="model")
    assert "RASTRO_SEARXNG_URL" not in str(info.value) and "RASTRO_MODEL" in str(info.value)
