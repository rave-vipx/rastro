"""Machine-readable job execution report, exit codes, and output JSON Schema."""

from __future__ import annotations

from collections.abc import Sequence
from enum import IntEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rastro.domain.models import Contract, ExtractionResult, Outcome

JOB_RUN_SCHEMA_ID = "https://rave-vipx.github.io/rastro/schemas/job-run.schema.json"
type JobRunStatus = Literal["success", "partial_success", "failed"]


class JobExitCode(IntEnum):
    """CLI exit codes distinguishing full success, partial success, failure, and config errors."""

    SUCCESS = 0
    FAILED = 1
    CONFIG_ERROR = 2
    PARTIAL_SUCCESS = 3


class JobRecordData(BaseModel):
    """Open object shape representing dynamically validated record payloads in JSON Schema."""

    model_config = ConfigDict(extra="allow", frozen=True)


class JobSummary(Contract):
    """Aggregate execution metadata across all sources in one job run."""

    format_version: Literal[1] = 1
    schema_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_count: int = Field(ge=1)
    succeeded: int = Field(ge=0)
    partial: int = Field(ge=0)
    failed: int = Field(ge=0)
    records_accepted: int = Field(ge=0)
    records_rejected: int = Field(ge=0)
    status: JobRunStatus

    @model_validator(mode="after")
    def check_counts(self) -> Self:
        if self.succeeded + self.partial + self.failed != self.source_count:
            raise ValueError("Source outcome counts must sum to source_count")
        return self


class RecipeSelection(Contract):
    """Which recipe one job source used: the job file's, or the serving one in recipe memory.

    ``memory`` covers a stored serving recipe newer than the job file's, a pinned
    ``recipe_version``, and sources without a recipe in the job file (memory lookup or
    learning). ``recipe_version`` is ``None`` only when no recipe existed before the run.
    """

    source_index: int = Field(ge=1)
    recipe_origin: Literal["job", "memory"]
    recipe_version: int | None = Field(default=None, ge=1)


class JobRunResult[T: BaseModel](Contract):
    """Versioned machine-readable output of a job run with provenance and untrusted-data label."""

    schema_version: Literal[1] = 1
    data_trust: Literal["untrusted_source_data"] = "untrusted_source_data"
    job: JobSummary
    results: tuple[ExtractionResult[T], ...] = Field(min_length=1)
    recipes: tuple[RecipeSelection, ...] = Field(default=(), exclude_if=lambda v: not v)

    @property
    def exit_code(self) -> JobExitCode:
        if self.job.status == "success":
            return JobExitCode.SUCCESS
        if self.job.status == "partial_success":
            return JobExitCode.PARTIAL_SUCCESS
        return JobExitCode.FAILED

    @classmethod
    def from_results(
        cls,
        *,
        schema_fingerprint: str,
        results: Sequence[ExtractionResult[T]],
    ) -> JobRunResult[T]:
        items = tuple(results)
        succeeded = sum(1 for item in items if item.status == Outcome.SUCCESS)
        partial = sum(1 for item in items if item.status == Outcome.PARTIAL_SUCCESS)
        failed = len(items) - succeeded - partial
        status: JobRunStatus = (
            "success"
            if succeeded == len(items)
            else ("partial_success" if succeeded or partial else "failed")
        )
        return cls(
            job=JobSummary(
                schema_fingerprint=schema_fingerprint,
                source_count=len(items),
                succeeded=succeeded,
                partial=partial,
                failed=failed,
                records_accepted=sum(len(item.records) for item in items),
                records_rejected=sum(item.metrics.rejected for item in items),
                status=status,
            ),
            results=items,
        )


def job_run_json_schema() -> dict[str, Any]:
    """Return the published JSON Schema (Draft 2020-12) for job run output."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": JOB_RUN_SCHEMA_ID,
        **JobRunResult[JobRecordData].model_json_schema(),
    }
