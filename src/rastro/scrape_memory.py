"""Recipe memory, success samples and offline self-repair for ``rastro.scrape`` (REPAIR-001-005).

The recipe database holds versions and their lifecycle. A sibling samples file
(``recipes.samples.db`` next to ``recipes.db``) keeps the latest sanitized success
per page and recipe, which repair replays as a regression gate. Repair is
deterministic: relocation, declared fallbacks and exact-name discovery, gated by
recipe CI plus a value-shape check against the sample. It never calls a model
and never fetches anything. An identical stored candidate is reused, not duplicated.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.recipe_ci import evaluate_candidate
from rastro.application.repair_candidates import RepairCandidateGenerator
from rastro.application.self_repair import gate_values, store_candidates
from rastro.application.success_samples import SuccessfulSample
from rastro.domain.memory import RecipeKey
from rastro.domain.models import (
    ConfigurationError,
    CssRecipe,
    Issue,
    Limits,
    RastroError,
    Recipe,
    Source,
    schema_fingerprint,
)
from rastro.domain.recipe_compare import CandidateEvaluation, RecipeComparison, RepairPolicy
from rastro.domain.repair import RepairCandidate
from rastro.domain.results import RepairProposal, SelfRepair

SAMPLES_DATASET = "scrape"


class ScrapeMemory:
    """One recipe database plus its samples file, opened for a single scrape call."""

    def __init__(self, path: str | Path, limits: Limits) -> None:
        path = Path(path)
        if path.is_symlink():
            raise ConfigurationError("memory= must not be a symlink")
        self.repository = SQLiteRecipeRepository(path)
        # The caller chose the folder; resolve it so the store's no-symlink check applies
        # to the samples file itself, not to system folders such as macOS /var.
        self.samples = path.parent.resolve() / f"{path.stem}.samples{path.suffix}"
        if self.samples.is_symlink():
            raise ConfigurationError("The recipe samples file must not be a symlink")
        self._limits = limits

    def serving(self, key: RecipeKey) -> Recipe | None:
        stored = self.repository.get(key)
        return None if stored is None else stored.validated_recipe

    def retain(self, schema: type[BaseModel], sample: SuccessfulSample) -> tuple[Issue, ...]:
        """Keep the latest success; a sample that cannot be kept is reported, not fatal."""
        from rastro.application.repair_storage import retain_sample

        try:
            skipped = retain_sample(self.samples, SAMPLES_DATASET, schema, sample)
        except RastroError:
            return (Issue(code="evidence_retention_failed"),)
        return (Issue(code="relocation_retention_skipped"),) if skipped else ()

    async def repair(
        self,
        mode: Literal["propose", "auto"],
        *,
        key: RecipeKey,
        source: Source,
        document: str,
        schema: type[BaseModel],
        previous: Recipe,
    ) -> SelfRepair:
        """Generate, gate and store candidates; promote only one that passes every gate."""
        from rastro.adapters.discovery import ScraplingDiscovery
        from rastro.adapters.scrapling import ScraplingParser
        from rastro.adapters.scrapling_relocation import ScraplingRelocation
        from rastro.adapters.sqlite_relocation import SQLiteRelocationStore
        from rastro.adapters.structured import JsonDiscovery, JsonParser

        retained = await asyncio.to_thread(
            SQLiteRelocationStore(self.samples, limits=self._limits).latest,
            SAMPLES_DATASET,
            source,
            schema_fingerprint(schema),
            previous,
        )
        snapshot, fixtures = (retained[0], (retained[1],)) if retained else (None, ())
        if not isinstance(previous, CssRecipe):
            snapshot = None
        json = previous.representation == "json"
        generated = await RepairCandidateGenerator(
            discovery=JsonDiscovery() if json else ScraplingDiscovery(),
            relocation=ScraplingRelocation(),
        ).generate(
            document,
            source=source,
            schema=schema,
            recipe=previous,
            limits=self._limits,
            snapshot=snapshot,
        )
        parser = JsonParser() if json else ScraplingParser()
        policy = RepairPolicy(auto_promote=mode == "auto")
        now = datetime.now(UTC)

        history = [record.data for fixture in fixtures for record in fixture.expected.records]

        def evaluate(candidate: RepairCandidate) -> CandidateEvaluation:
            evaluation = evaluate_candidate(
                candidate,
                document,
                source=source,
                schema=schema,
                parser=parser,
                limits=self._limits,
                observed_at=now,
                fixtures=fixtures,
                policy=policy,
            )
            return gate_values(
                evaluation,
                candidate,
                previous=previous,
                document=document,
                source=source,
                schema=schema,
                parser=parser,
                limits=self._limits,
                history=history,
            )

        def persist() -> tuple[RecipeComparison, ...]:
            evaluated = [(c, evaluate(c)) for c in generated.candidates]
            return store_candidates(
                evaluated, previous=previous, key=key, lifecycle=self.repository, at=now
            )

        comparisons = await asyncio.to_thread(persist)
        proposals = tuple(
            RepairProposal(
                version=item.candidate_version,
                decision=item.evaluation.decision,
                reasons=item.evaluation.reasons,
                changed_fields=item.changed_fields,
            )
            for item in comparisons
            if item.evaluation is not None
        )
        promoted = next((p.version for p in proposals if p.decision == "promote"), None)
        return SelfRepair(
            mode=mode,
            previous_version=previous.version,
            promoted=promoted is not None,
            promoted_version=promoted,
            candidates=proposals,
        )
