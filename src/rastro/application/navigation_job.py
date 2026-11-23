"""Explore a job's first page in its browser render and return a recipe with page steps.

Rules for model-origin steps (ADR 0037): exploration runs only when the operator started it
(never from an agent tool, a registry recipe, a bundle or model output) and only where the
job renders the page in a browser. The model may then use ``page_actions`` and nothing
else. Every trial is a full, fresh run of the job's first source through the same engine
path a later run takes: capability gate, request budget, network guard, robots, step
execution and extraction, so a kept step is one that already replayed without the model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rastro.application.engine import Rastro
from rastro.application.model_choice import ModelChoice, resolve_model
from rastro.application.navigation_explorer import (
    ExplorationBudget,
    ExplorationEvent,
    ExplorationResult,
    Observation,
    explore_navigation,
)
from rastro.application.operator_trust import current_grants, current_origin
from rastro.application.page_steps import StepRunningAcquisition
from rastro.application.robots_cache import robots_cache
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.capability_grants import CapabilityGrants
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.job_fields import build_job_schema
from rastro.domain.jobs import Job, UrlJobSource
from rastro.domain.models import ConfigurationError, CssRecipe, Source
from rastro.domain.page_steps import PageStep
from rastro.ports.acquisition import AcquisitionPort

__all__ = ["explore_job"]

UNBOUNDED_RECORDS = 10_000
"""Expected records when the caller names none: explore until the model or a budget stops."""


async def explore_job(
    job: Job,
    *,
    model: ModelChoice,
    acquisition: AcquisitionPort | None = None,
    expected_records: int | None = None,
    budget: ExplorationBudget | None = None,
    policy: InferencePolicy | None = None,
    allow_remote: bool = False,
    on_event: Callable[[ExplorationEvent], None] | None = None,
) -> ExplorationResult:
    """Find page steps that reveal more records on the job's first URL source.

    ``acquisition`` must render that URL in a browser; ``expected_records`` stops the
    exploration once reached. Nothing is written: the caller reviews ``result.recipe``.
    """
    recipe, source = _target(job)
    if current_origin() != "operator":
        raise ConfigurationError(
            "Only the operator can start navigation exploration",
            location="capabilities",
            hint="Run the exploration from the CLI or the Python API.",
        )
    if not (isinstance(acquisition, StepRunningAcquisition) and acquisition.runs_steps(source.url)):
        raise ConfigurationError(
            "Navigation exploration needs the page rendered in a browser",
            location="acquisition.render",
            hint='Set render = "browser" in the job\'s [acquisition] table.',
        )
    port = resolve_model(model, allow_remote=allow_remote, policy=policy)
    if port is None:
        raise ConfigurationError("Navigation exploration needs a model", location="model")
    recording = _Recording(acquisition)
    client = Rastro(acquisition=recording, policy=job.acquisition, limits=job.limits)
    schema = build_job_schema(list(job.fields))

    async def trial(steps: tuple[PageStep, ...]) -> Observation:
        stepped = CssRecipe.model_validate(
            {**recipe.model_dump(mode="json"), "steps": [s.model_dump() for s in steps]}
        )
        recording.last = None
        result = await client.extract(source=source, schema=schema, recipe=stepped)
        issue = next((i.code for i in result.issues), None)
        body = recording.last.body if recording.last is not None else None
        return Observation(result.status, len(result.records), body or "", issue)

    with robots_cache():
        return await explore_navigation(
            port,
            recipe=recipe,
            fields=tuple(field.name for field in job.fields),
            trial=trial,
            expected_records=expected_records or UNBOUNDED_RECORDS,
            budget=budget,
            policy=policy,
            grants=_model_grants(),
            on_event=on_event,
        )


def _target(job: Job) -> tuple[CssRecipe, Source]:
    first = job.sources[0] if job.sources else None
    if not isinstance(first, UrlJobSource) or job.acquisition is None:
        raise ConfigurationError("Navigation exploration needs a URL source", location="sources")
    recipe = first.recipe or job.recipe
    if not isinstance(recipe, CssRecipe):
        raise ConfigurationError("Navigation exploration needs a CSS recipe", location="recipe")
    return recipe, (first.region or job.region).to_source(first.url)


def _model_grants() -> CapabilityGrants:
    """The operator's grants, plus ``page_actions`` for the model when the operator has it."""
    grants = current_grants()
    by_origin = dict(grants.by_origin)
    if grants.allows("page_actions", "operator"):
        by_origin["model"] = by_origin.get("model", frozenset()) | {"page_actions"}
    return CapabilityGrants(by_origin=by_origin)


class _Recording:
    """Delegates to the job's acquisition and keeps the last document it returned."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.last: AcquisitionResult | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def acquire(self, source: Source, **kwargs: Any) -> AcquisitionResult:
        result: AcquisitionResult = await self._inner.acquire(source, **kwargs)
        self.last = result
        return result

    def runs_steps(self, url: str) -> bool:
        return bool(self._inner.runs_steps(url))

    async def run_steps(
        self, source: Source, steps: tuple[PageStep, ...], *, max_bytes: int
    ) -> AcquisitionResult:
        result: AcquisitionResult = await self._inner.run_steps(source, steps, max_bytes=max_bytes)
        self.last = result
        return result
