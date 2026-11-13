"""Current page, retained evidence and a CI evaluator for one job source (REPAIR-002/004)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import JsonValue

from rastro.application.extraction import extract_document
from rastro.application.recipe_ci import evaluate_candidate
from rastro.application.repair_context import RepairContext
from rastro.domain.memory import RecipeKey
from rastro.domain.models import Limits, RastroError, Recipe, Source
from rastro.domain.recipe_compare import CandidateEvaluation, RepairPolicy
from rastro.domain.repair import RelocationSnapshot, RepairCandidate
from rastro.mocklab.bundle import FixtureBundle
from rastro.ports.parser import DocumentParser

type Evaluate = Callable[[RepairCandidate], CandidateEvaluation]


@dataclass(frozen=True)
class Evidence:
    source: Source
    key: RecipeKey
    previous: Recipe
    document: str
    snapshot: RelocationSnapshot | None
    evaluate: Evaluate
    records: Callable[[Recipe], int]
    rows: Callable[[Recipe], list[dict[str, JsonValue]]]
    history: tuple[dict[str, JsonValue], ...]
    fixtures: tuple[FixtureBundle, ...] = ()
    parser: DocumentParser | None = None


async def gather_evidence(
    context: RepairContext,
    index: int,
    policy: RepairPolicy,
    previous: Recipe | None = None,
    *,
    allow_fallbacks: bool = False,
) -> Evidence:
    """Current document, retained evidence and a CI evaluator for one source.

    ``previous`` defaults to the serving recipe; promotion passes it explicitly so a store
    with nothing serving yet (an imported bundle) can adopt its candidate.
    """
    from rastro.adapters.scrapling import ScraplingParser
    from rastro.adapters.structured import JsonParser

    if previous is None:
        source, key, previous = context.source(index)
    else:
        source, key = context.locate(index)
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
            allow_fallbacks=allow_fallbacks,
        )

    def rows(recipe: Recipe) -> list[dict[str, JsonValue]]:
        """Validated rows ``recipe`` extracts from the current page (none on any error)."""
        try:
            result = extract_document(
                document,
                source=source,
                schema=context.schema,
                recipe=recipe,
                parser=parser,
                limits=context.job.limits or Limits(),
            )
        except RastroError:
            return []
        return [record.data.model_dump(mode="json") for record in result.records]

    samples = tuple(dict(r.data) for fixture in fixtures for r in fixture.expected.records)
    return Evidence(
        source,
        key,
        previous,
        document,
        snapshot,
        evaluate,
        lambda recipe: len(rows(recipe)),
        rows,
        samples,
        fixtures,
        parser,
    )
