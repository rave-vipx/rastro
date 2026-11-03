"""Result construction and metric stamping across provided, HTTP, and learning paths."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.models import (
    ExtractionResult,
    Issue,
    Metrics,
    Outcome,
    Recipe,
    RecipeAttempt,
    Representation,
    Source,
    schema_fingerprint,
)

_PROVIDED: dict[Representation, Literal["provided_html", "provided_json"]] = {
    "html": "provided_html",
    "json": "provided_json",
}


def failure_result[T: BaseModel](
    *,
    status: Outcome,
    issue_code: str,
    source: Source,
    schema: type[T],
    started_at: datetime,
    source_url: str | None = None,
    input_bytes: int = 0,
    storage_seconds: float = 0,
) -> ExtractionResult[T]:
    return ExtractionResult[T](
        job_id=str(uuid4()),
        status=status,
        source_url=source.evidence_url if source_url is None else source_url,
        locale=source.locale,
        timezone=source.timezone,
        schema_fingerprint=schema_fingerprint(schema),
        started_at=started_at,
        completed_at=datetime.now(UTC),
        records=(),
        issues=(Issue(code=issue_code),),
        metrics=Metrics(
            egress=source.egress,
            storage_seconds=storage_seconds,
            input_bytes=input_bytes,
            candidates=0,
            accepted=0,
            rejected=0,
            extraction_seconds=0,
            validation_seconds=0,
            total_seconds=0,
        ),
    )


def rollup_attempt_metrics[T: BaseModel](
    base: Metrics,
    results: Sequence[ExtractionResult[T]],
    *,
    storage_seconds: float,
    started: float,
    inference_calls: int = 0,
    inference_seconds: float = 0.0,
) -> Metrics:
    return base.model_copy(
        update={
            "storage_seconds": storage_seconds,
            "inference_calls": inference_calls,
            "inference_seconds": inference_seconds,
            "candidates": sum(item.metrics.candidates for item in results),
            "rejected": sum(item.metrics.rejected for item in results),
            "extraction_seconds": sum(item.metrics.extraction_seconds for item in results),
            "validation_seconds": sum(item.metrics.validation_seconds for item in results),
            "total_seconds": perf_counter() - started,
        }
    )


def stamp_provided[T: BaseModel](
    result: ExtractionResult[T], representation: Representation
) -> ExtractionResult[T]:
    metrics = result.metrics.model_copy(update={"acquisition_method": _PROVIDED[representation]})
    return result.model_copy(update={"metrics": metrics})


def stamp_acquired[T: BaseModel](
    result: ExtractionResult[T],
    *,
    source: Source,
    fetched: AcquisitionResult,
    started: float,
    started_at: datetime,
) -> ExtractionResult[T]:
    metrics = result.metrics.model_copy(
        update={
            "acquisition_method": "http",
            "egress": fetched.egress,
            "acquisition_seconds": fetched.acquisition_seconds,
            "network_seconds": fetched.network_seconds,
            "request_count": fetched.requests,
            "retry_count": fetched.retries,
            "redirect_count": fetched.redirects,
            "bytes_received": fetched.bytes_received,
            "total_seconds": perf_counter() - started,
        }
    )
    return result.model_copy(
        update={
            "started_at": started_at,
            "completed_at": datetime.now(UTC),
            "metrics": metrics,
            "requested_url": source.evidence_url,
            "http_status": fetched.http_status,
            "retry_after_seconds": fetched.retry_after_seconds,
        }
    )


def stamp_learning[T: BaseModel](
    *,
    results: Sequence[ExtractionResult[T]],
    attempts: Sequence[RecipeAttempt],
    selected: Recipe | None,
    complete: bool,
    failure: str,
    source: Source,
    schema: type[T],
    input_bytes: int,
    started: float,
    started_at: datetime,
    calls: int,
    inference_seconds: float,
    storage_seconds: float,
) -> ExtractionResult[T]:
    if results and complete:
        base = results[-1]
    else:
        status = (
            Outcome.TRANSIENT_FAILURE
            if failure in {"model_unavailable", "model_timeout"}
            else Outcome.INSUFFICIENT_EVIDENCE
        )
        base = failure_result(
            status=status,
            issue_code=failure,
            source=source,
            schema=schema,
            started_at=started_at,
            input_bytes=input_bytes,
        )
    metrics = rollup_attempt_metrics(
        base.metrics,
        results,
        storage_seconds=storage_seconds,
        started=started,
        inference_calls=calls,
        inference_seconds=inference_seconds,
    )
    return base.model_copy(
        update={
            "learned_recipe": selected,
            "recipe_version": selected.version if selected else None,
            "recipe_attempts": tuple(attempts),
            "metrics": metrics,
            "started_at": started_at,
            "completed_at": datetime.now(UTC),
        }
    )
