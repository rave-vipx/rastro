"""A blocked URL target climbs the acquisition ladder before it is extracted."""

from __future__ import annotations

import asyncio

import pytest

from rastro import scrape_target
from rastro.application.blocked_hint import blocked_hint
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.models import Limits, Outcome

URL = "https://ladder.test/page"
pytestmark = pytest.mark.usefixtures("offline_cli_loop")


class Port:
    def __init__(self, level: str, blocked: set[str], seen: list[str]) -> None:
        self.level, self.blocked, self.seen = level, blocked, seen

    async def acquire(self, source, *, max_bytes, representation) -> AcquisitionResult:
        self.seen.append(self.level)
        if self.level in self.blocked:
            return AcquisitionResult(
                url=source.url,
                body=None,
                status=Outcome.ACCESS_RESTRICTION,
                issue_code="access_restricted",
                http_status=403,
                blocked=blocked_hint("access_restricted", 403, "challenge_unsolved"),
            )
        return AcquisitionResult(
            url=source.url, body="<p>page</p>", status=Outcome.SUCCESS, issue_code=None
        )


def site(monkeypatch, blocked: set[str]) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(scrape_target, "web_acquirer", lambda policy: Port("http", blocked, seen))
    monkeypatch.setattr(scrape_target, "ladder_port", lambda rung: Port(rung.level, blocked, seen))
    return seen


def load():
    return asyncio.run(
        scrape_target.load_page(
            URL, url=None, locale="en-US", limits=Limits(), representation="html"
        ),
        loop_factory=lambda: asyncio.SelectorEventLoop(),
    )


def test_a_blocked_target_climbs_until_a_level_returns_it(monkeypatch) -> None:
    seen = site(monkeypatch, {"http", "impersonation", "browser"})
    page = load()
    assert page.document == "<p>page</p>"
    assert seen == ["http", "impersonation", "browser", "stealth"]
    assert [(s.level, s.outcome) for s in page.ladder] == [
        ("http", "blocked"),
        ("impersonation", "blocked"),
        ("browser", "blocked"),
        ("stealth", "ok"),
    ]


def test_an_open_target_is_fetched_once_without_a_ladder(monkeypatch) -> None:
    seen = site(monkeypatch, set())
    page = load()
    assert seen == ["http"] and page.ladder == ()


def test_a_target_blocked_at_every_level_keeps_the_explicit_refusal(monkeypatch) -> None:
    site(monkeypatch, {"http", "impersonation", "browser", "stealth", "challenge"})
    page = load()
    assert page.document is None
    assert page.fetched is not None and page.fetched.status == Outcome.ACCESS_RESTRICTION
    assert page.ladder[-1].outcome == "blocked"
