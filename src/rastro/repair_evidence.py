"""Retain one sanitized success sample and its relocation snapshot (ADR 0019, REPAIR-001).

Composition edge shared by job runs and ``rastro.scrape``: it wires the fixture
builder, the Scrapling relocation adapter and the SQLite evidence store.
"""

from pathlib import Path

from pydantic import BaseModel

from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.adapters.sqlite_relocation import SQLiteRelocationStore
from rastro.application.success_samples import SuccessfulSample
from rastro.domain.models import ConfigurationError, CssRecipe, Issue, ResourceLimitError, Source
from rastro.mocklab.fixtures import build_fixture


def retain_sample(
    path: Path, dataset: str, schema: type[BaseModel], sample: SuccessfulSample
) -> tuple[Issue, ...]:
    """Store the latest success for replay; the fixture carries a neutral origin URL."""
    fixture_source = Source.model_validate(
        {**sample.source.model_dump(), "url": "https://fixture.invalid/"}
    )
    fixture_result = sample.result.model_copy(
        update={
            "source_url": fixture_source.evidence_url,
            "records": tuple(
                record.model_copy(
                    update={
                        "evidence": {
                            name: evidence.model_copy(
                                update={"source_url": fixture_source.evidence_url}
                            )
                            for name, evidence in record.evidence.items()
                        }
                    }
                )
                for record in sample.result.records
            ),
        }
    )
    fixture = build_fixture(
        sample.body,
        source=fixture_source,
        schema=schema,
        recipe=sample.recipe,
        result=fixture_result,
        limits=sample.limits,
        synthetic_count=0,
    )
    snapshot = None
    issues: tuple[Issue, ...] = ()
    if isinstance(sample.recipe, CssRecipe):
        try:
            snapshot = ScraplingRelocation().capture(
                sample.body,
                source=sample.source,
                recipe=sample.recipe,
                result=sample.result,
                limits=sample.limits,
            )
        except (ConfigurationError, ResourceLimitError):
            issues = (Issue(code="relocation_retention_skipped"),)
    SQLiteRelocationStore(path, limits=sample.limits).save(
        dataset, sample.source, fixture, snapshot, observed_at=sample.result.completed_at
    )
    return issues
