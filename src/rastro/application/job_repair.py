"""Repair and promote a job source's recipe through measured CI (REPAIR-002-005, REC-006)."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rastro.application.job_repair_source import _assist, _repair_source
from rastro.application.model_budget import model_cost_override
from rastro.application.model_choice import ModelChoice
from rastro.application.repair_context import RepairContext
from rastro.application.repair_evidence import gather_evidence
from rastro.application.robots_cache import robots_cache
from rastro.domain.lifecycle import RecipeState
from rastro.domain.models import ConfigurationError
from rastro.domain.recipe_compare import (
    CandidateEvaluation,
    RecipeComparison,
    RepairPolicy,
    RepairReport,
    SourceRepair,
)
from rastro.domain.repair import RepairCandidate

OVERRIDE_PREFIX = "gates overridden: "


def repair_job_source(
    path: str | Path,
    source: int,
    *,
    min_records: int | None = None,
    min_completeness: float | None = None,
    auto_promote: bool | None = None,
    allow_absolute_paths: bool = False,
    model: ModelChoice = None,
    allow_remote: bool = False,
    allow_egress: bool = False,
    allow_stealth: bool = False,
    allow_env: Iterable[str] = (),
    max_model_cost: float | None = None,
    model_cost_per_call: float | None = None,
) -> RepairReport:
    """Propose, evaluate and store repair candidates for one source (1-based index).

    Candidates are saved as versions; one that passes every gate becomes active when
    automatic promotion is enabled. Keyword thresholds override the job's ``[repair]``.
    ``model`` (a ``ModelPort`` or a spec for ``rastro.model``) overrides the job's model:
    it proposes candidates when relocation finds none that passes and judges every
    would-be promotion. Remote specs need ``allow_remote=True``. Re-fetching through a
    job-declared egress proxy needs ``allow_egress=True`` and a stealth render mode
    needs ``allow_stealth=True``. ``max_model_cost`` caps the model's cost in USD and needs
    a price: ``model_cost_per_call`` or the job's ``[model] cost_per_call`` (COST-002).
    """
    overrides = {
        name: value
        for name, value in (
            ("min_records", min_records),
            ("min_completeness", min_completeness),
            ("auto_promote", auto_promote),
        )
        if value is not None
    }
    return repair_job_sources(
        path,
        (source,),
        allow_absolute_paths=allow_absolute_paths,
        model=model,
        allow_remote=allow_remote,
        allow_egress=allow_egress,
        allow_stealth=allow_stealth,
        allow_env=allow_env,
        max_model_cost=max_model_cost,
        model_cost_per_call=model_cost_per_call,
        **overrides,
    )


def repair_job_sources(
    path: str | Path,
    sources: Sequence[int] | None,
    *,
    allow_absolute_paths: bool = False,
    model: ModelChoice = None,
    allow_remote: bool = False,
    allow_egress: bool = False,
    allow_stealth: bool = False,
    allow_env: Iterable[str] = (),
    max_model_cost: float | None = None,
    model_cost_per_call: float | None = None,
    **overrides: Any,
) -> RepairReport:
    """Repair several sources, or all of them when ``sources`` is ``None``."""
    allow_env = tuple(allow_env)
    context = RepairContext.load(
        Path(path),
        allow_absolute_paths,
        allow_egress=allow_egress,
        allow_stealth=allow_stealth,
        allow_env=allow_env,
    )
    policy = job_policy(context, overrides)
    indexes = sources if sources is not None else range(1, len(context.job.sources) + 1)
    with model_cost_override(max_model_cost, model_cost_per_call):
        assist = _assist(context, model, allow_remote, allow_env)

    async def run() -> RepairReport:
        items = [await _repair_source(context, index, policy, assist) for index in indexes]
        return RepairReport(run_id=str(uuid4()), sources=tuple(items))

    return run_sync(run())


def promote_job_recipe(
    path: str | Path,
    source: int,
    version: int,
    *,
    reason: str,
    override_gates: bool = False,
    allow_absolute_paths: bool = False,
    allow_egress: bool = False,
    allow_stealth: bool = False,
    allow_env: Iterable[str] = (),
) -> RepairReport:
    """Re-evaluate a stored candidate and activate it when every gate passes.

    ``override_gates=True`` lets a person activate a quarantined candidate; the lifecycle
    event records ``reason`` with a ``"gates overridden: "`` prefix. A rejected candidate
    (failed validation or regression) is never activated.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ConfigurationError("Promotion requires a non-empty reason")
    context = RepairContext.load(
        Path(path),
        allow_absolute_paths,
        allow_egress=allow_egress,
        allow_stealth=allow_stealth,
        allow_env=allow_env,
    )
    # Explicit approval enables promotion, but cannot bypass the CI gates on its own.
    policy = job_policy(context, {"auto_promote": True})

    async def run() -> RepairReport:
        item = await _promote(context, source, version, policy, reason, override_gates)
        return RepairReport(run_id=str(uuid4()), sources=(item,))

    return run_sync(run())


def job_policy(context: RepairContext, overrides: dict[str, Any]) -> RepairPolicy:
    base = context.job.repair or RepairPolicy()
    if not overrides:
        return base
    try:
        return RepairPolicy.model_validate({**base.model_dump(), **overrides})
    except ValueError:
        raise ConfigurationError("Invalid repair threshold override") from None


def run_sync[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run one repair operation; its evidence fetches share one robots.txt answer per origin."""
    with robots_cache(), asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coroutine)


async def _promote(
    context: RepairContext,
    index: int,
    version: int,
    policy: RepairPolicy,
    reason: str,
    override_gates: bool,
) -> SourceRepair:
    _, key = context.locate(index)
    stored = context.repository.get(key, version=version)
    if stored is None or stored.metadata.state != RecipeState.CANDIDATE:
        raise ConfigurationError("Only a stored candidate may be promoted")
    serving = context.repository.get(key)
    # A fresh store (an imported bundle) has nothing serving: compare with itself.
    previous = serving.validated_recipe if serving else stored.validated_recipe
    adopting = serving is None
    if adopting:  # No history yet; the import already replayed the bundle's records.
        policy = policy.model_copy(update={"min_historical_fixtures": 0})
    evidence = await gather_evidence(context, index, policy, previous, allow_fallbacks=adopting)
    evaluation = evidence.evaluate(
        RepairCandidate(recipe=stored.recipe, origin="discovery", rationale="Operator review")
    )
    notes: tuple[str, ...] = ()
    if evaluation.decision == "reject":
        # Retire only when the page itself is usable: a consent wall, maintenance page or
        # empty shell says nothing about the candidate, which then stays a candidate.
        usable = evaluation.current_records > 0 or evidence.records(evidence.previous) > 0
        if usable:
            context.repository.transition(
                evidence.key,
                version,
                RecipeState.RETIRED,
                reason="Recipe CI: reject",
                at=datetime.now(UTC),
            )
        notes = ("candidate_retired",) if usable else ("page_not_usable",)
        if override_gates:
            outcome = (
                "the candidate was rejected and retired"
                if usable
                else "the page has no usable records, so the candidate was kept"
            )
            raise ConfigurationError(
                "--override-gates bypasses quarantine thresholds, never a validation or "
                f"regression failure; {outcome}" + _describe(evaluation)
            )
    promoted = evaluation.decision == "promote" or (
        override_gates and evaluation.decision == "quarantine"
    )
    if promoted:
        note = reason if evaluation.decision == "promote" else OVERRIDE_PREFIX + reason
        context.repository.transition(
            evidence.key, version, RecipeState.ACTIVE, reason=note, at=datetime.now(UTC)
        )
        context.serving_changed(index)
    return SourceRepair(
        source_index=index,
        comparisons=(RecipeComparison.between(evidence.previous, stored.recipe, evaluation),),
        promoted_version=version if promoted else None,
        notes=notes,
    )


def _describe(evaluation: CandidateEvaluation) -> str:
    """Blocking record issues as ``record N field: code`` text; never source values."""
    items = [
        " ".join(
            part
            for part in (
                f"record {issue.record_index}" if issue.record_index is not None else "",
                issue.field or "",
            )
            if part
        )
        + f": {issue.code}"
        for issue in evaluation.blocking
    ]
    failed = [check.name for check in evaluation.regressions if not check.passed]
    detail = "; ".join(items + failed)
    return f" ({detail})" if detail else ""
