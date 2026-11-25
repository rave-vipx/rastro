"""Solver plugins: operator-installed code that fetches a page the built-in levels cannot.

A plugin is a Python entry point in the ``rastro.solvers`` group of a package the operator
installed. Its object is a factory: called with the job's acquisition policy, it returns an
acquisition port. Jobs, recipes and bundles cannot name, load or configure a plugin; the
ladder reaches them last, and only when the ``solver_plugins`` capability is granted for
the job's origin (grant-only, even for the operator). See ADR 0039.

A plugin is the operator's own code and makes its own requests, so Rastro's DNS pinning
does not reach inside it. What Rastro keeps: a source outside the job's allowed hosts is
never handed to a plugin, a result that ends outside them or over the byte limit is
discarded, and a plugin that raises counts as unsolved.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import entry_points
from urllib.parse import urlsplit

import structlog

from rastro.application.blocked_hint import blocked_hint
from rastro.domain.acquisition import AcquisitionPolicy, AcquisitionResult
from rastro.domain.models import Outcome, Representation, Source
from rastro.ports.acquisition import AcquisitionPort

__all__ = [
    "GROUP",
    "SolverChain",
    "SolverFactory",
    "any_solver_installed",
    "installed_solvers",
    "solver_port",
]

GROUP = "rastro.solvers"
_OK = frozenset({Outcome.SUCCESS, Outcome.PARTIAL_SUCCESS, Outcome.UNCHANGED})
_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)

type SolverFactory = Callable[[AcquisitionPolicy], AcquisitionPort]
"""What a ``rastro.solvers`` entry point loads: policy in, acquisition port out."""


def any_solver_installed() -> bool:
    """Whether a plugin is installed; reads entry point names only, never loads one."""
    return any(True for _ in entry_points(group=GROUP))


def installed_solvers() -> tuple[tuple[str, SolverFactory], ...]:
    """Every loadable plugin in the ``rastro.solvers`` group, ordered by entry point name.

    A plugin that fails to load is skipped and logged by name only.
    """
    found: list[tuple[str, SolverFactory]] = []
    for point in sorted(entry_points(group=GROUP), key=lambda point: point.name):
        try:
            factory = point.load()
        except Exception:
            _LOG.warning("solver_plugin_unloadable", plugin=point.name[:64])
            continue
        if callable(factory):
            found.append((point.name, factory))
    return tuple(found)


def solver_port(policy: AcquisitionPolicy) -> AcquisitionPort | None:
    """A port over every installed plugin, or None when none is installed."""
    solvers = installed_solvers()
    return SolverChain(policy, solvers) if solvers else None


def _unsolved(url: str) -> AcquisitionResult:
    return AcquisitionResult(
        url=url,
        body=None,
        status=Outcome.ACCESS_RESTRICTION,
        issue_code="challenge_unsolved",
        blocked=blocked_hint("challenge_unsolved", None, None),
    )


class SolverChain:
    """Try each plugin in order; the first result inside the job's limits wins."""

    def __init__(
        self, policy: AcquisitionPolicy, solvers: tuple[tuple[str, SolverFactory], ...]
    ) -> None:
        self.policy, self.solvers = policy, solvers

    def _inside(self, url: str) -> bool:
        parts = urlsplit(url)
        scheme_ok = parts.scheme == "https" or (parts.scheme == "http" and self.policy.allow_http)
        return scheme_ok and (parts.hostname or "").lower() in self.policy.allowed_hosts

    def _accepted(self, result: object, max_bytes: int) -> bool:
        return (
            isinstance(result, AcquisitionResult)
            and result.status in _OK
            and isinstance(result.body, str)  # a plugin's bytes or None count as unsolved
            and isinstance(result.url, str)
            and self._inside(result.url)
            and len(result.body.encode("utf-8", "surrogatepass")) <= max_bytes
        )

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation
    ) -> AcquisitionResult:
        if not self._inside(source.url):
            return _unsolved(source.url)
        for name, factory in self.solvers:
            try:
                port = factory(self.policy)
                result = await port.acquire(
                    source, max_bytes=max_bytes, representation=representation
                )
            except Exception:
                _LOG.warning("solver_plugin_failed", plugin=name[:64])
                continue
            if self._accepted(result, max_bytes):
                return result
        return _unsolved(source.url)
