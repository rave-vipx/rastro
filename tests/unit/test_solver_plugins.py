"""Solver plugins come only from installed entry points and stay inside the job's hosts."""

from __future__ import annotations

import asyncio
from importlib.metadata import EntryPoint

import pytest

from rastro.application import solver_plugins
from rastro.application.solver_plugins import GROUP, SolverChain, installed_solvers
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.jobs import Job
from rastro.domain.models import Outcome, Source

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
POLICY = AcquisitionPolicy(allowed_hosts=("solve.test",))
SOURCE = Source(url="https://solve.test/page", locale="en-US")


class Fixed:
    def __init__(self, url: str = SOURCE.url, body: str = "<p>ok</p>") -> None:
        self.url, self.body = url, body

    async def acquire(self, source, *, max_bytes, representation) -> AcquisitionResult:
        return AcquisitionResult(
            url=self.url, body=self.body, status=Outcome.SUCCESS, issue_code=None
        )


class Broken:
    async def acquire(self, source, *, max_bytes, representation) -> AcquisitionResult:
        raise RuntimeError("plugin bug")


def plugins(monkeypatch: pytest.MonkeyPatch, loaded: dict[str, object]) -> None:
    points = [EntryPoint(name, f"plugin_{name}:factory", GROUP) for name in loaded]
    points.append(EntryPoint("other", "plugin_other:factory", "another.group"))

    def entry_points(*, group: str):
        return [point for point in points if point.group == group]

    monkeypatch.setattr(solver_plugins, "entry_points", entry_points)
    monkeypatch.setattr(EntryPoint, "load", lambda self: loaded[self.name])


def acquire(chain: SolverChain, max_bytes: int = 1_000) -> AcquisitionResult:
    return run(chain.acquire(SOURCE, max_bytes=max_bytes, representation="html"))


def run(coroutine):
    return asyncio.run(coroutine, loop_factory=lambda: asyncio.SelectorEventLoop())


def test_solver_plugin_only_from_entry_points(monkeypatch) -> None:
    plugins(monkeypatch, {"zeta": lambda policy: Fixed(), "alpha": lambda policy: Fixed()})
    assert [name for name, _ in installed_solvers()] == ["alpha", "zeta"]  # stable order
    # A job can never name a solver: there is no key for it.
    with pytest.raises(ValueError):
        Job.model_validate(
            {
                "format_version": 1,
                "fields": [{"name": "title", "type": "string"}],
                "sources": [{"url": SOURCE.url}],
                "acquisition": {"allowed_hosts": ["solve.test"], "solver": "plugin_evil:run"},
            }
        )


def test_a_plugin_that_fails_to_load_is_skipped(monkeypatch) -> None:
    plugins(monkeypatch, {"good": lambda policy: Fixed()})
    monkeypatch.setattr(
        EntryPoint,
        "load",
        lambda self: (
            (_ for _ in ()).throw(ImportError("missing"))
            if self.name != "good"
            else (lambda policy: Fixed())
        ),
    )
    assert [name for name, _ in installed_solvers()] == ["good"]


def test_the_first_solver_that_returns_the_page_wins() -> None:
    chain = SolverChain(POLICY, (("broken", lambda p: Broken()), ("fixed", lambda p: Fixed())))
    result = acquire(chain)
    assert result.status == Outcome.SUCCESS and result.body == "<p>ok</p>"


@pytest.mark.parametrize(
    "plugin",
    [
        Fixed(url="https://elsewhere.test/page"),  # left the job's allowed hosts
        Fixed(body="x" * 2_000),  # over the byte limit
        Fixed(body=b"<p>bytes</p>"),  # type: ignore[arg-type]  # a body that is not text
        Broken(),
    ],
)
def test_a_result_outside_the_job_limits_is_refused(plugin) -> None:
    result = acquire(SolverChain(POLICY, (("only", lambda p: plugin),)))
    assert result.status == Outcome.ACCESS_RESTRICTION
    assert result.body is None
    assert result.blocked is not None and result.blocked.kind == "challenge_unsolved"


def test_a_source_outside_the_allowed_hosts_never_reaches_a_plugin() -> None:
    called: list[str] = []

    def factory(policy):
        called.append("built")
        return Fixed()

    chain = SolverChain(POLICY, (("only", factory),))
    outside = Source(url="https://elsewhere.test/", locale="en-US")
    result = run(chain.acquire(outside, max_bytes=1_000, representation="html"))
    assert result.body is None and called == []
