"""Capabilities and grants: which input origin may switch on which acquisition capability.

The origin of a job is decided by its entry point, never by the job itself: the CLI and the
Python API run the operator's jobs; agent tools, registry recipes, shared bundles and model
output are untrusted. Operator jobs get every capability except the grant-only ones;
untrusted origins get only what the operator granted to that origin.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, get_args

from rastro.domain.sources import ConfigurationError

__all__ = [
    "CAPABILITIES",
    "GRANT_ONLY",
    "INPUT_ORIGINS",
    "OPERATOR_DEFAULT",
    "Capability",
    "CapabilityDecision",
    "CapabilityGrants",
    "InputOrigin",
    "decide",
    "default_grants",
]

type InputOrigin = Literal["operator", "mcp", "bundle", "registry", "model"]
"""Where a job or recipe came from: the operator, an agent tool, a shared bundle, a registry
recipe or model output."""
type Capability = Literal[
    "stealth",
    "solve_challenges",
    "proxy_rotation",
    "impersonation",
    "http3",
    "unbounded_crawl",
    "robots_ignore",
    "page_actions",
    "network_capture",
    "screenshots",
    "persistent_session",
    "real_chrome",
    "cdp",
    "solver_plugins",
]
"""An acquisition feature a job can request and a grant can allow."""

INPUT_ORIGINS: tuple[InputOrigin, ...] = get_args(InputOrigin.__value__)
CAPABILITIES: tuple[Capability, ...] = get_args(Capability.__value__)
GRANT_ONLY: frozenset[Capability] = frozenset(
    {"persistent_session", "real_chrome", "cdp", "solver_plugins"}
)
"""Capabilities that touch the operator's own browser, profile or code: granted explicitly."""
OPERATOR_DEFAULT: frozenset[Capability] = frozenset(CAPABILITIES) - GRANT_ONLY
"""What an operator job may use without any grant."""

_EMPTY: frozenset[Capability] = frozenset()


def _known(capabilities: frozenset[Capability]) -> frozenset[Capability]:
    if not capabilities <= frozenset(CAPABILITIES):
        raise ConfigurationError(
            "Unknown capability name", hint=f"Use one of: {', '.join(CAPABILITIES)}."
        )
    return frozenset(capabilities)


@dataclass(frozen=True)
class CapabilityGrants:
    """The capabilities allowed for each origin; a missing origin is allowed nothing."""

    by_origin: Mapping[InputOrigin, frozenset[Capability]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        frozen = {origin: frozenset(caps) for origin, caps in self.by_origin.items()}
        object.__setattr__(self, "by_origin", MappingProxyType(frozen))

    def allows(self, capability: Capability, origin: InputOrigin) -> bool:
        """Whether a job from ``origin`` may use ``capability``."""
        return capability in self.by_origin.get(origin, _EMPTY)


def default_grants(
    *,
    operator_grants: frozenset[Capability] = _EMPTY,
    untrusted: Mapping[InputOrigin, frozenset[Capability]] | None = None,
) -> CapabilityGrants:
    """Operator origin gets ``OPERATOR_DEFAULT | operator_grants``; others only ``untrusted``.

    Raises ``ConfigurationError`` for an unknown capability or origin, and when ``untrusted``
    names the operator origin.
    """
    untrusted = untrusted or {}
    if "operator" in untrusted or not set(untrusted) <= set(INPUT_ORIGINS):
        raise ConfigurationError(
            "Untrusted grants must name an untrusted origin",
            hint="Use one of: mcp, bundle, registry, model.",
        )
    by_origin: dict[InputOrigin, frozenset[Capability]] = {
        origin: _known(untrusted.get(origin, _EMPTY)) for origin in INPUT_ORIGINS[1:]
    }
    by_origin["operator"] = OPERATOR_DEFAULT | _known(operator_grants)
    return CapabilityGrants(by_origin=by_origin)


@dataclass(frozen=True)
class CapabilityDecision:
    """What was requested from one origin, and what was granted or refused."""

    origin: InputOrigin
    requested: frozenset[Capability]
    granted: frozenset[Capability]
    refused: frozenset[Capability]

    def require(self) -> None:
        """Raise ``ConfigurationError`` naming each refused capability and the flag to grant it.

        Only known capability names are printed; unknown requested names are counted, never
        echoed, because they come from untrusted input.
        """
        if not self.refused:
            return
        names = sorted(c for c in self.refused if c in CAPABILITIES)
        unknown = len(self.refused) - len(names)
        listed = ", ".join([*names, *([f"{unknown} unknown"] if unknown else [])])
        raise ConfigurationError(
            f"Capabilities not granted for {self.origin} input: {listed}",
            location="capabilities",
            hint=self._hint(names),
        )

    def _hint(self, names: list[Capability]) -> str | None:
        if not names:
            return None
        if self.origin == "operator":
            flags = " ".join(f"--allow-capability {name}" for name in names)
            return f"Pass {flags} to grant {'it' if len(names) == 1 else 'them'}."
        flags = " ".join(f"--allow-untrusted {self.origin}:{name}" for name in names)
        return f"Pass {flags} to grant {'it' if len(names) == 1 else 'them'}."


def decide(
    requested: frozenset[Capability], origin: InputOrigin, grants: CapabilityGrants
) -> CapabilityDecision:
    """Split ``requested`` into granted and refused for ``origin``; unknown names are refused."""
    requested = frozenset(requested)
    granted = frozenset(c for c in requested if grants.allows(c, origin))
    return CapabilityDecision(origin, requested, granted, requested - granted)
