"""Versioned load report: DATA-001/003, SYNC-008; values are untrusted source data."""

from typing import Any, Literal

from pydantic import Field

from rastro.domain.consistency import ConsistencyReport
from rastro.domain.dataset import ChangeKind, DatasetRecordKey, SourceStatus
from rastro.domain.drift import DriftAssessment
from rastro.domain.job_results import JobExitCode, JobSummary
from rastro.domain.models import Contract, Issue, Outcome


class LoadedSource(Contract):
    source_url: str
    status: Outcome
    counts: dict[ChangeKind, int]
    consistency: ConsistencyReport | None = Field(default=None, exclude_if=lambda v: v is None)
    next_action: Literal["rastro recipe repair"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    issues: tuple[Issue, ...] = Field(default=(), exclude_if=lambda v: not v)


class StaleSource(SourceStatus):
    affected_keys: tuple[DatasetRecordKey, ...]


class JobLoadResult(Contract):
    schema_version: Literal[1] = 1
    data_trust: Literal["untrusted_source_data"] = "untrusted_source_data"
    dataset: str
    job: JobSummary
    sources: tuple[LoadedSource, ...] = Field(min_length=1)
    stale_sources: tuple[StaleSource, ...]
    drift: tuple[DriftAssessment, ...] | None = Field(default=None, exclude_if=lambda v: v is None)

    @property
    def exit_code(self) -> JobExitCode:
        if any(s.consistency and s.consistency.verdict == "inconsistent" for s in self.sources):
            return JobExitCode.PARTIAL_SUCCESS
        return {
            "success": JobExitCode.SUCCESS,
            "partial_success": JobExitCode.PARTIAL_SUCCESS,
        }.get(self.job.status, JobExitCode.FAILED)


def job_load_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://rave-vipx.github.io/rastro/schemas/job-load.schema.json",
        **JobLoadResult.model_json_schema(),
    }
