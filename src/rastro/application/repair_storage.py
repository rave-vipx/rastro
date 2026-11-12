"""Retain successful extractions as repair evidence: fixtures and relocation snapshots."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.adapters.sqlite_relocation import SQLiteRelocationStore
from rastro.application.success_samples import SuccessfulSample
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import (
    ConfigurationError,
    CssRecipe,
    Issue,
    Outcome,
    ResourceLimitError,
    Source,
)
from rastro.mocklab.fixtures import build_fixture


def retain_successes(
    path: Path,
    dataset: str,
    schema: type[BaseModel],
    report: JobRunResult[Any],
    samples: dict[str, SuccessfulSample],
) -> dict[str, tuple[Issue, ...]]:
    issues: dict[str, tuple[Issue, ...]] = {}
    for result in report.results:
        sample = samples.get(result.job_id)
        if sample is None or result.status != Outcome.SUCCESS:
            continue
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
                issues[result.source_url] = (Issue(code="relocation_retention_skipped"),)
        SQLiteRelocationStore(path, limits=sample.limits).save(
            dataset, sample.source, fixture, snapshot, observed_at=sample.result.completed_at
        )
    return issues
