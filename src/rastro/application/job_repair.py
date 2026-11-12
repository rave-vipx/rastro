"""Repair and promote a job source's recipe through measured CI (REPAIR-002-005, REC-006)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rastro.application.recipe_ci import evaluate_candidate
from rastro.application.recipe_repair import repair_candidates
from rastro.application.repair_context import RepairContext
from rastro.domain.lifecycle import RecipeState
from rastro.domain.memory import RecipeKey
from rastro.domain.models import ConfigurationError, Limits, Recipe, Source
from rastro.domain.recipe_compare import (
    CandidateEvaluation,
    RecipeComparison,
    RepairPolicy,
    RepairReport,
    SourceRepair,
)
from rastro.domain.repair import RelocationSnapshot, RepairCandidate

OVERRIDE_PREFIX = "gates overridden: "
type Evaluate = Callable[[RepairCandidate], CandidateEvaluation]


def repair_job_source(
    path: str | Path,
    source: int,
    *,
    min_records: int | None = None,
    min_completeness: float | None = None,
    auto_promote: bool | None = None,
    allow_absolute_paths: bool = False,
) -> RepairReport:
    """Propose, evaluate and store repair candidates for one source (1-based index).

    Candidates are saved as versions; one that passes every gate becomes active when
    automatic promotion is enabled. Keyword thresholds override the job's ``[repair]``.
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
        path, (source,), allow_absolute_paths=allow_absolute_paths, **overrides
    )


def repair_job_sources(
    path: str | Path,
    sources: Sequence[int] | None,
    *,
    allow_absolute_paths: bool = False,
    **overrides: Any,
) -> RepairReport:
    """Repair several sources, or all of them when ``sources`` is ``None``."""
    context = RepairContext.load(Path(path), allow_absolute_paths)
    policy = _policy(context, overrides)
    indexes = sources if sources is not None else range(1, len(context.job.sources) + 1)

    async def run() -> RepairReport:
        items = [await _repair_source(context, index, policy) for index in indexes]
        return RepairReport(run_id=str(uuid4()), sources=tuple(items))

    return _run(run())


def promote_job_recipe(
    path: str | Path,
    source: int,
    version: int,
    *,
    reason: str,
    override_gates: bool = False,
    allow_absolute_paths: bool = False,
) -> RepairReport:
    """Re-evaluate a stored candidate and activate it when every gate passes.

    ``override_gates=True`` lets a person activate a quarantined candidate; the lifecycle
    event records ``reason`` with a ``"gates overridden: "`` prefix. A rejected candidate
    (failed validation or regression) is never activated.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ConfigurationError("Promotion requires a non-empty reason")
    context = RepairContext.load(Path(path), allow_absolute_paths)
    # Explicit approval enables promotion, but cannot bypass the CI gates on its own.
    policy = _policy(context, {"auto_promote": True})

    async def run() -> RepairReport:
        item = await _promote(context, source, version, policy, reason, override_gates)
        return RepairReport(run_id=str(uuid4()), sources=(item,))

    return _run(run())


def _policy(context: RepairContext, overrides: dict[str, Any]) -> RepairPolicy:
    base = context.job.repair or RepairPolicy()
    if not overrides:
        return base
    try:
        return RepairPolicy.model_validate({**base.model_dump(), **overrides})
    except ValueError:
        raise ConfigurationError("Invalid repair threshold override") from None


def _run(coroutine: Coroutine[Any, Any, RepairReport]) -> RepairReport:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coroutine)


@dataclass(frozen=True)
class _Evidence:
    source: Source
    key: RecipeKey
    previous: Recipe
    document: str
    snapshot: RelocationSnapshot | None
    evaluate: Evaluate


async def _evidence(context: RepairContext, index: int, policy: RepairPolicy) -> _Evidence:
    from rastro.adapters.scrapling import ScraplingParser
    from rastro.adapters.structured import JsonParser

    source, key, previous = context.source(index)
    document = await context.document(index, source)
    snapshot, fixtures = context.fixtures(source, previous)
    parser = JsonParser() if previous.representation == "json" else ScraplingParser()
    history = context.history()  # before any dataset write; repair never applies snapshots
    now = datetime.now(UTC)

    def evaluate(candidate: RepairCandidate) -> CandidateEvaluation:
        return evaluate_candidate(
            candidate,
            document,
            source=source,
            schema=context.schema,
            parser=parser,
            limits=context.job.limits or Limits(),
            observed_at=now,
            fixtures=fixtures,
            rules=context.job.consistency,
            history=history,
            policy=policy,
        )

    return _Evidence(source, key, previous, document, snapshot, evaluate)


async def _promote(
    context: RepairContext,
    index: int,
    version: int,
    policy: RepairPolicy,
    reason: str,
    override_gates: bool,
) -> SourceRepair:
    evidence = await _evidence(context, index, policy)
    stored = context.repository.get(evidence.key, version=version)
    if stored is None or stored.metadata.state != RecipeState.CANDIDATE:
        raise ConfigurationError("Only a stored candidate may be promoted")
    evaluation = evidence.evaluate(
        RepairCandidate(recipe=stored.recipe, origin="discovery", rationale="Operator review")
    )
    promoted = evaluation.decision == "promote" or (
        override_gates and evaluation.decision == "quarantine"
    )
    if promoted:
        note = reason if evaluation.decision == "promote" else OVERRIDE_PREFIX + reason
        context.repository.transition(
            evidence.key, version, RecipeState.ACTIVE, reason=note, at=datetime.now(UTC)
        )
    return SourceRepair(
        source_index=index,
        comparisons=(RecipeComparison.between(evidence.previous, stored.recipe, evaluation),),
        promoted_version=version if promoted else None,
    )


async def _repair_source(context: RepairContext, index: int, policy: RepairPolicy) -> SourceRepair:
    from rastro.adapters.discovery import ScraplingDiscovery
    from rastro.adapters.scrapling_relocation import ScraplingRelocation
    from rastro.adapters.structured import JsonDiscovery
    from rastro.application.repair_candidates import RepairCandidateGenerator

    evidence = await _evidence(context, index, policy)
    previous = evidence.previous
    model = None
    if context.job.model is not None:
        from rastro.application.job_loader import _build_model_provider

        model = _build_model_provider(context.job.model)
    generator = RepairCandidateGenerator(
        discovery=JsonDiscovery() if previous.representation == "json" else ScraplingDiscovery(),
        relocation=ScraplingRelocation(),
        model=model,
        policy=context.job.inference_policy,
        aliases=context.job.discovery_aliases or None,
    )
    candidates = await generator.generate(
        evidence.document,
        source=evidence.source,
        schema=context.schema,
        recipe=previous,
        limits=context.job.limits or Limits(),
        snapshot=evidence.snapshot,
        model_enabled=context.job.model is not None,
    )
    comparisons = repair_candidates(
        candidates.candidates,
        previous=previous,
        key=evidence.key,
        lifecycle=context.repository,
        evaluate=evidence.evaluate,
        at=datetime.now(UTC),
    )
    promoted = [
        c.candidate_version
        for c in comparisons
        if c.evaluation is not None and c.evaluation.decision == "promote"
    ]
    return SourceRepair(
        source_index=index,
        outcome=candidates.outcome,
        inference_calls=candidates.inference_calls,
        issues=candidates.issues,
        comparisons=comparisons,
        promoted_version=promoted[0] if promoted else None,
    )
