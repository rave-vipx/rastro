"""Versioned load report: DATA-001/003, SYNC-008; values are untrusted source data."""

from collections.abc import Iterable
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from rastro.domain.consistency import ConsistencyReport
from rastro.domain.dataset import ChangeKind, DatasetRecordKey, SourceStatus
from rastro.domain.drift import DriftAssessment
from rastro.domain.health import OpenDrift
from rastro.domain.job_results import JobExitCode, JobRunStatus, JobSummary
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
    recipe_origin: Literal["job", "memory"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    recipe_version: int | None = Field(default=None, exclude_if=lambda v: v is None)
    reason: str | None = Field(default=None, exclude_if=lambda v: v is None)
    """Outcome detail of a failed source, such as ``robots_denied``, ``http_403``, ``timeout``."""

    @property
    def inconsistent(self) -> bool:
        return self.consistency is not None and self.consistency.verdict == "inconsistent"


type LoadStatus = Literal["success", "degraded", "partial_success", "failed", "storage_error"]
_EXIT_CODES: dict[LoadStatus, JobExitCode] = {
    "success": JobExitCode.SUCCESS,
    "degraded": JobExitCode.NEEDS_ATTENTION,
    "partial_success": JobExitCode.PARTIAL_SUCCESS,
    "failed": JobExitCode.FAILED,
    "storage_error": JobExitCode.STORAGE_ERROR,
}


def load_status(job: JobRunStatus, sources: Iterable[LoadedSource], open_drift: bool) -> LoadStatus:
    """Failure outranks partial success, which outranks open drift (``degraded``)."""
    if job == "failed":
        return "failed"
    if job == "partial_success" or any(source.inconsistent for source in sources):
        return "partial_success"
    return "degraded" if open_drift else "success"


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
    open_drift: tuple[OpenDrift, ...] = ()
    """Unacknowledged elevated or high drift of this job's sources, with or without ``--drift``."""
    status: LoadStatus = "success"
    """Derived from ``job.status``, consistency and ``open_drift`` unless ``storage_error``."""

    @model_validator(mode="before")
    @classmethod
    def derive_status(cls, data: Any) -> Any:
        if isinstance(data, dict) and "status" not in data and "job" in data:
            data = {**data, "status": cls._expected(data)}
        return data

    @staticmethod
    def _expected(data: dict[str, Any]) -> LoadStatus:
        return load_status(
            JobSummary.model_validate(data["job"]).status,
            [LoadedSource.model_validate(item) for item in data.get("sources", ())],
            bool(data.get("open_drift")),
        )

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        expected = load_status(self.job.status, self.sources, bool(self.open_drift))
        if self.status not in (expected, "storage_error"):
            raise ValueError("Load status does not match the job outcome and open drift")
        return self

    @property
    def exit_code(self) -> JobExitCode:
        return _EXIT_CODES[self.status]


def job_load_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://rave-vipx.github.io/rastro/schemas/job-load.schema.json",
        **JobLoadResult.model_json_schema(),
    }
