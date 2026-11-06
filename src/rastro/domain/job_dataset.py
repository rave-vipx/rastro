"""Job `[dataset]` block and the read-only preview contract (DATA-002, JOB-002, AGENT-001).

The block only *declares* where approved records would be stored and how they are identified.
Nothing here opens, creates or writes a dataset file; storage belongs to the dataset store.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field, JsonValue, StrictInt, StrictStr, field_validator

from rastro.domain.job_contract import RedactedContract
from rastro.domain.job_results import JobExitCode, JobSummary
from rastro.domain.job_sources import validate_job_path_syntax
from rastro.domain.models import Contract, Outcome

JOB_PREVIEW_SCHEMA_ID = "https://rave-vipx.github.io/rastro/schemas/job-preview.schema.json"
DATASET_NAME = r"^[a-z][a-z0-9_]{0,63}$"


class DatasetSpec(RedactedContract):
    """Where approved records of a job would be stored and which fields identify a record."""

    name: StrictStr = Field(pattern=DATASET_NAME)
    path: StrictStr = Field(min_length=1, max_length=1024)
    identity: tuple[StrictStr, ...] = Field(min_length=1, max_length=8)
    approve: tuple[StrictStr, ...] | None = Field(default=None, min_length=1, max_length=100)
    match_tolerance_minutes: StrictInt | None = Field(default=None, ge=1, le=10_080)

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str) -> str:
        return validate_job_path_syntax(value)

    def approved_fields(self, declared: Sequence[str]) -> tuple[str, ...]:
        """Fields that may be loaded: ``approve`` when given, otherwise every declared field."""
        return tuple(declared) if self.approve is None else self.approve

    def check_against(self, declared: Sequence[str]) -> None:
        """Reject identity or approve entries that are not declared target fields."""
        known = set(declared)
        approve = self.approved_fields(declared)
        for label, names in (("identity", self.identity), ("approve", approve)):
            if len(set(names)) != len(names):
                raise ValueError(f"dataset.{label} must not repeat a field")
            if any(name not in known for name in names):
                raise ValueError(f"dataset.{label} must name declared fields")
        if not set(self.identity) <= set(approve):
            raise ValueError("dataset.identity must be listed in approve")


class PreviewField(Contract):
    """One field of an accepted record: value, provenance, validation and load decision."""

    name: str
    value: JsonValue
    method: Literal["jsonld", "css", "json"] | None = None
    locator: str | None = None
    source_url: str | None = None
    recipe_version: int | None = None
    validation: Literal["valid"] = "valid"
    identity: bool
    loaded: bool


class PreviewReason(Contract):
    code: str
    field: str | None = None


class PreviewRecord(Contract):
    index: int = Field(ge=0)
    status: Literal["accepted", "rejected"]
    reasons: tuple[PreviewReason, ...] = ()
    fields: tuple[PreviewField, ...] = ()


class PreviewSource(Contract):
    source_url: str
    status: Outcome
    records: tuple[PreviewRecord, ...]
    issues: tuple[PreviewReason, ...] = ()


class PreviewDataset(Contract):
    name: str
    path: str
    identity: tuple[str, ...]
    approve: tuple[str, ...]
    match_tolerance_minutes: int | None = None


class JobPreview(Contract):
    """Read-only report of what a run would load into the dataset. Nothing is written."""

    schema_version: Literal[1] = 1
    data_trust: Literal["untrusted_source_data"] = "untrusted_source_data"
    written: Literal[False] = False
    dataset: PreviewDataset
    job: JobSummary
    sources: tuple[PreviewSource, ...] = Field(min_length=1)

    @property
    def exit_code(self) -> JobExitCode:
        return {
            "success": JobExitCode.SUCCESS,
            "partial_success": JobExitCode.PARTIAL_SUCCESS,
        }.get(self.job.status, JobExitCode.FAILED)


def job_preview_json_schema() -> dict[str, Any]:
    """Return the published JSON Schema (Draft 2020-12) for `rastro job preview --output json`."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": JOB_PREVIEW_SCHEMA_ID,
        **JobPreview.model_json_schema(),
    }
