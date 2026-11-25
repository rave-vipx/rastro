"""The one check that a job's capabilities are granted for the origin it came from (ADR 0037).

A job requests capabilities through the settings it uses: ``render = "stealth"`` requests
``stealth``, ``robots = "ignore"`` requests ``robots_ignore``, page steps request
``page_actions``, ``rotate_egress`` requests ``proxy_rotation``, and any setting model with
a ``requested_capabilities()`` method adds its own. The check runs before any renderer,
client or request exists, and a refusal names the origin, each refused capability and the
flag that grants it, never a job value.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import structlog
from pydantic import BaseModel

from rastro.application.operator_trust import current_origin, current_trust
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.capability_grants import Capability, CapabilityDecision, decide
from rastro.domain.jobs import Job, UrlJobSource
from rastro.domain.model_trust import OperatorTrust
from rastro.domain.models import ConfigurationError
from rastro.domain.page_steps import (
    ClickStep,
    FollowStep,
    ScrollStep,
    SelectStep,
    TypeStep,
    WaitStep,
    steps_capabilities,
)

__all__ = [
    "CapabilityRefused",
    "RequestsCapabilities",
    "model_capabilities",
    "policy_capabilities",
    "requested_capabilities",
    "require",
    "require_capabilities",
]

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)
_STEPS = (ClickStep, TypeStep, SelectStep, ScrollStep, WaitStep, FollowStep)


class CapabilityRefused(ConfigurationError):
    """A requested capability is not granted for the job's input origin."""

    error_code = "capability_refused"

    def __init__(self, decision: CapabilityDecision) -> None:
        self.decision = decision
        try:
            decision.require()  # builds the safe message, hint and location
        except ConfigurationError as error:
            super().__init__(str(error), location=error.location, hint=error.hint)
            return
        super().__init__("Capabilities not granted", location="capabilities")


@runtime_checkable
class RequestsCapabilities(Protocol):
    """A setting model that knows which capabilities it switches on."""

    def requested_capabilities(self) -> frozenset[Capability]: ...


def model_capabilities(model: BaseModel) -> frozenset[Capability]:
    """What a job, recipe or setting model requests through its own fields."""
    requested: set[Capability] = set()
    for name in type(model).model_fields:
        value = getattr(model, name)
        if isinstance(value, RequestsCapabilities):
            requested |= value.requested_capabilities()
        elif isinstance(value, tuple) and value and all(isinstance(v, _STEPS) for v in value):
            requested |= steps_capabilities(value)
    return frozenset(requested)


def policy_capabilities(policy: AcquisitionPolicy) -> frozenset[Capability]:
    """What an acquisition policy requests by itself, rendering in its own ``render`` mode."""
    requested = set(model_capabilities(policy))
    if policy.render == "stealth":
        requested.add("stealth")
    if policy.solve_challenges:
        requested.add("solve_challenges")
    if policy.robots == "ignore":
        requested.add("robots_ignore")
    if policy.rotate_egress:
        requested.add("proxy_rotation")
    return frozenset(requested)


def _render_modes(job: Job, default: str) -> set[str]:
    modes = {
        item.render if "render" in item.model_fields_set else default
        for item in job.sources
        if isinstance(item, UrlJobSource)
    }
    if modes - {"http"}:
        modes.add(default)  # undeclared URLs, such as next pages, may render in it
    return modes


def requested_capabilities(job: Job) -> frozenset[Capability]:
    """Every capability the job's settings and its inline recipes request."""
    requested = set(model_capabilities(job))
    for recipe in (job.recipe, *(getattr(item, "recipe", None) for item in job.sources)):
        if isinstance(recipe, BaseModel):
            requested |= model_capabilities(recipe)
    if job.acquisition is not None:
        policy = job.acquisition
        render = "stealth" if "stealth" in _render_modes(job, policy.render) else "http"
        requested |= policy_capabilities(policy.model_copy(update={"render": render}))
    return frozenset(requested)


def require(requested: frozenset[Capability], *, allow_stealth: bool = False) -> CapabilityDecision:
    """Decide ``requested`` for the current origin; raise ``CapabilityRefused`` on a refusal.

    ``allow_stealth`` is the deprecated stealth switch passed in code: it grants stealth and
    challenge solving to untrusted origins and changes nothing for the operator.
    """
    trust = current_trust()
    if allow_stealth is True:
        trust = trust.merged(OperatorTrust(allow_stealth=True))
    decision = decide(frozenset(requested), current_origin(), trust.capability_grants())
    if decision.refused:
        raise CapabilityRefused(decision)
    return decision


def require_capabilities(job: Job) -> CapabilityDecision:
    """Check every capability the job requests against the grants for its origin.

    A granted ``robots = "ignore"`` is logged once here, for the whole job.
    """
    decision = require(requested_capabilities(job))
    if "robots_ignore" in decision.granted:
        _LOG.info("robots_ignored", origin=decision.origin)
    return decision
