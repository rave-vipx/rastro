"""Gate a recipe proposed by an outside agent like a model repair candidate (REPAIR-001-005).

The agent (an MCP client's model) only proposes a recipe definition. Each changed CSS locator
is composed with the serving one (``"<serving>, <proposed>"``, as model repair does) so old
pages still match, then validated against the job's fields and representation, evaluated by
the same recipe CI as repair and stored as a candidate (an equal stored candidate is reused).
It is never promoted here, whatever the job's ``auto_promote``: the calling agent is the
semantic judge and promotes with ``recipe_promote`` after checking meaning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from rastro.application.extraction import validate_recipe
from rastro.application.job_repair import job_policy, run_sync
from rastro.application.model_repair import with_history
from rastro.application.recipe_repair import repair_candidates
from rastro.application.repair_context import RepairContext
from rastro.application.repair_evidence import gather_evidence
from rastro.domain.models import ConfigurationError, Recipe
from rastro.domain.recipe_compare import RepairReport, SourceRepair
from rastro.domain.repair import RepairCandidate
from rastro.domain.selector_policy import check_untrusted_recipe

RATIONALE = "Agent proposal; requires recipe CI"


def propose_job_recipe(
    path: str | Path,
    source: int,
    recipe: Recipe,
    *,
    allow_absolute_paths: bool = False,
) -> RepairReport:
    """Evaluate and store an agent-proposed recipe for one source (1-based index).

    Raises ``ConfigurationError`` when the proposal does not fit the job or equals the
    serving recipe. The report's single comparison carries the decision, reasons and the
    blocking record issues; ``promoted_version`` is always ``None``. Costly selectors are
    refused before any evaluation (``rastro.domain.selector_policy``).
    """
    check_untrusted_recipe(recipe)
    context = RepairContext.load(Path(path), allow_absolute_paths)
    policy = job_policy(context, {"auto_promote": False})

    async def run() -> RepairReport:
        evidence = await gather_evidence(context, source, policy)
        candidate = normalized_candidate(recipe, evidence.previous, context.schema)
        comparisons = repair_candidates(
            (RepairCandidate(recipe=candidate, origin="model", rationale=RATIONALE),),
            previous=evidence.previous,
            key=evidence.key,
            lifecycle=context.repository,
            evaluate=evidence.evaluate,
            at=datetime.now(UTC),
        )
        item = SourceRepair(
            source_index=source,
            outcome="candidates",
            inference_calls=0,
            issues=(),
            comparisons=comparisons,
            promoted_version=None,
        )
        return RepairReport(run_id=str(uuid4()), sources=(item,))

    return run_sync(run())


def normalized_candidate(proposal: Recipe, previous: Recipe, schema: type[BaseModel]) -> Recipe:
    """The proposal as a single-definition candidate: evidence required, no fallbacks, the
    serving version (the store assigns a fresh one), each changed locator composed with the
    serving one. Refuses a no-op proposal."""
    plain = _normalize(proposal, previous, schema)
    if plain == previous.model_copy(update={"require_evidence": True, "fallbacks": ()}):
        raise ConfigurationError("The proposed recipe equals the serving recipe")
    return _normalize(with_history(proposal, previous), previous, schema)


def _normalize(proposal: Recipe, previous: Recipe, schema: type[BaseModel]) -> Recipe:
    data = proposal.model_dump()
    data.update(require_evidence=True, fallbacks=(), version=previous.version)
    try:
        return validate_recipe(schema, Recipe.model_validate(data), previous.representation)
    except (ValidationError, ValueError):
        raise ConfigurationError("The proposed recipe is not a valid Rastro recipe") from None
