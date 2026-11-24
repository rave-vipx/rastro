"""Repair one job source: deterministic candidates, adaptive and model assist, then CI.

Also picks the model a repair may use (the operator's override, the job's ``[model]`` or
``RASTRO_MODEL`` with ``[repair] model``).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from rastro.application.model_choice import ModelChoice, resolve_model
from rastro.application.model_repair import ModelAssist, assist_repair
from rastro.application.operator_trust import operator_trust
from rastro.application.recipe_repair import repair_candidates
from rastro.application.repair_context import RepairContext
from rastro.application.repair_evidence import gather_evidence
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.models import Limits
from rastro.domain.recipe_compare import RepairPolicy, SourceRepair

__all__ = ["_assist", "_repair_source"]


def _assist(
    context: RepairContext,
    model: ModelChoice,
    allow_remote: bool,
    allow_env: Iterable[str] = (),
) -> ModelAssist | None:
    """The override, else the job's ``[model]``, else ``RASTRO_MODEL`` with ``[repair] model``.

    The override and ``RASTRO_MODEL`` come from the operator and are trusted; a model
    declared in the job file passes the operator-trust checks.
    """
    job_model, policy = context.job.model, context.job.inference_policy
    port = resolve_model(model, allow_remote=allow_remote, job_model=job_model, policy=policy)
    if port is None and context.job.model is not None:
        from rastro.application.job_loader import _build_model_provider

        # Declared by the job file: only the operator's opt-ins apply (H1).
        with operator_trust(allow_remote=allow_remote, allow_env=allow_env):
            port = _build_model_provider(context.job.model)
    if port is None and context.job.repair is not None and context.job.repair.model:
        port = resolve_model("env", allow_remote=allow_remote, job_model=job_model, policy=policy)
    return (
        None
        if port is None
        else ModelAssist(port, context.job.inference_policy or InferencePolicy())
    )


async def _repair_source(
    context: RepairContext, index: int, policy: RepairPolicy, assist: ModelAssist | None
) -> SourceRepair:
    from rastro.adapters.discovery import ScraplingDiscovery
    from rastro.adapters.scrapling_relocation import ScraplingRelocation
    from rastro.adapters.structured import JsonDiscovery
    from rastro.application.repair_candidates import RepairCandidateGenerator

    evidence = await gather_evidence(context, index, policy)
    previous = evidence.previous
    generator = RepairCandidateGenerator(
        discovery=JsonDiscovery() if previous.representation == "json" else ScraplingDiscovery(),
        relocation=ScraplingRelocation(),
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
    )
    assisted = await assist_repair(
        assist,
        [(c, evidence.evaluate(c)) for c in candidates.candidates],
        html=evidence.document,
        schema=context.schema,
        previous=previous,
        gate=evidence.evaluate,
        rows=evidence.rows,
        history=evidence.history,
        limits=context.job.limits or Limits(),
        timezone=evidence.source.timezone,
    )
    decided = {id(c): e for c, e in assisted.evaluated}
    comparisons = repair_candidates(
        [c for c, _ in assisted.evaluated],
        previous=previous,
        key=evidence.key,
        lifecycle=context.repository,
        evaluate=lambda c: decided[id(c)],
        at=datetime.now(UTC),
    )
    promoted = [
        c.candidate_version
        for c in comparisons
        if c.evaluation is not None and c.evaluation.decision == "promote"
    ]
    if promoted:
        context.serving_changed(index)
    return SourceRepair(
        source_index=index,
        outcome="candidates" if comparisons else "no_candidates",
        inference_calls=candidates.inference_calls + assisted.model_calls,
        issues=(*candidates.issues, *assisted.issues),
        comparisons=comparisons,
        promoted_version=promoted[0] if promoted else None,
        origin=assisted.origin,
    )
