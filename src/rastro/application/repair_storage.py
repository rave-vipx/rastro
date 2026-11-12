"""Retain successful extractions as repair evidence: fixtures and relocation snapshots."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.adapters.sqlite_relocation import SQLiteRelocationStore
from rastro.application.engine import Rastro
from rastro.application.success_samples import SuccessfulSample
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import (
    ConfigurationError,
    CssRecipe,
    ExtractionResult,
    Issue,
    Outcome,
    RastroError,
    ResourceLimitError,
    Source,
)
from rastro.mocklab.errors import FixtureMismatchError
from rastro.mocklab.fixtures import build_fixture

FIXTURE_ORIGIN = "https://fixture.invalid/"


def retain_successes(
    path: Path,
    dataset: str,
    schema: type[BaseModel],
    report: JobRunResult[Any],
    samples: dict[str, SuccessfulSample],
) -> dict[str, tuple[Issue, ...]]:
    """Retain repair evidence after the dataset commit; failures become source warnings.

    Rows are already committed, so a refused or unwritable sample is reported as
    ``evidence_retention_failed`` on that source instead of failing the load (ADR 0019).
    """
    issues: dict[str, tuple[Issue, ...]] = {}
    for result in report.results:
        sample = samples.get(result.job_id)
        if sample is None or result.status != Outcome.SUCCESS:
            continue
        try:
            skipped = _retain(path, dataset, schema, sample)
        except RastroError:
            issues[result.source_url] = (Issue(code="evidence_retention_failed"),)
            continue
        if skipped:
            issues[result.source_url] = (Issue(code="relocation_retention_skipped"),)
    return issues


def _retain(path: Path, dataset: str, schema: type[BaseModel], sample: SuccessfulSample) -> bool:
    fixture_source = Source.model_validate({**sample.source.model_dump(), "url": FIXTURE_ORIGIN})
    fixture = build_fixture(
        sample.body,
        source=fixture_source,
        schema=schema,
        recipe=sample.recipe,
        result=_relabeled_result(sample, fixture_source, schema),
        limits=sample.limits,
        synthetic_count=0,
    )
    snapshot, skipped = None, False
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
            skipped = True
    SQLiteRelocationStore(path, limits=sample.limits).save(
        dataset, sample.source, fixture, snapshot, observed_at=sample.result.completed_at
    )
    return skipped


def _relabeled_result(
    sample: SuccessfulSample, fixture_source: Source, schema: type[BaseModel]
) -> ExtractionResult[Any]:
    """Re-extract the original body at the relabeled origin.

    Relative URLs resolve against the source URL, so the expected values must be those
    the page yields at ``fixture.invalid``; replay then resolves exactly like this
    extraction. The relabeled run must keep the original outcome and record shape.
    """
    engine = Rastro(limits=sample.limits)
    extract = engine.extract_html if sample.recipe.representation == "html" else engine.extract_json
    relabeled = extract(sample.body, source=fixture_source, schema=schema, recipe=sample.recipe)
    original = sample.result
    same_shape = relabeled.status == original.status and [
        set(r.evidence) for r in relabeled.records
    ] == [set(r.evidence) for r in original.records]
    if not same_shape:
        raise FixtureMismatchError("Relabeled extraction does not match the successful run")
    return relabeled
