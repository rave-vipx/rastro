"""The run report: a versioned, content-free summary of one job run or load.

A report holds names, counts, outcomes and timings. It never holds records, page bodies,
URL queries or secrets, so it can be printed, attached to failure hooks and shown in Studio.
Its JSON Schema is published and versioned on its own (``schema_version``).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from rastro.domain.capability_grants import Capability
from rastro.domain.dataset import ChangeKind
from rastro.domain.results import Outcome
from rastro.domain.source_notes import (
    CODE_PATTERN,
    AcquisitionUse,
    BlockedHint,
    CrawlNote,
    DiscoveryNote,
    FieldCounts,
    RepairNote,
    SourceReason,
)
from rastro.domain.sources import Contract

__all__ = [
    "RUN_REPORT_SCHEMA_ID",
    "RunReport",
    "RunReportStatus",
    "RunTotals",
    "SourceReport",
    "SourceTiming",
    "run_report_json_schema",
]

RUN_REPORT_SCHEMA_ID = "https://rave-vipx.github.io/rastro/schemas/run-report.schema.json"
_HOST = r"^[a-z0-9](?:[a-z0-9.:\[\]-]{0,252}[a-z0-9\]])?$"
_PATH = r"^/[^?#\s]{0,2047}$"
_FILE_NAME = r"^[^/\\\x00-\x1f]{1,255}$"

type RunReportStatus = Literal["success", "degraded", "partial_success", "failed", "storage_error"]
Code = Annotated[str, Field(min_length=1, max_length=64, pattern=CODE_PATTERN)]
Count = Annotated[int, Field(ge=0)]
Seconds = Annotated[float, Field(ge=0)]


class SourceTiming(Contract):
    """Wall-clock seconds spent per stage for one source."""

    acquisition: Seconds = 0
    extraction: Seconds = 0
    validation: Seconds = 0
    total: Seconds = 0


class SourceReport(Contract):
    """One source: where (host and path, never a query), how it ended and what it produced."""

    source_index: int = Field(ge=1)
    host: str = Field(max_length=255, pattern=_HOST)
    path: str = Field(pattern=_PATH)
    outcome: Outcome
    reason: SourceReason
    detail: Code | None = None
    pages: Count = 0
    records_accepted: Count = 0
    records_rejected: Count = 0
    rejected_by_code: dict[Code, Count] = Field(default_factory=dict, max_length=256)
    fields_nulled: FieldCounts = Field(default_factory=dict, max_length=256)
    stored: dict[ChangeKind, Count] | None = None
    acquisition: AcquisitionUse | None = None
    discovery: DiscoveryNote | None = None
    crawl: CrawlNote | None = None
    repair: RepairNote | None = None
    blocked: BlockedHint | None = None
    seconds: SourceTiming = Field(default_factory=SourceTiming)


class RunTotals(Contract):
    """Totals across every source of the run."""

    sources: Count
    succeeded: Count
    partial: Count
    failed: Count
    blocked: Count
    pages: Count
    records_accepted: Count
    records_rejected: Count
    model_calls: Count
    repairs_without_model: Count


class RunReport(Contract):
    """A content-free report of one ``run`` or ``load`` of one job file."""

    schema_version: Literal[1] = 1
    kind: Literal["run", "load"]
    content: Literal["content_free"] = "content_free"
    job: str = Field(pattern=_FILE_NAME)
    dataset: str | None = Field(
        default=None, min_length=1, max_length=128, exclude_if=lambda v: v is None
    )
    started_at: AwareDatetime
    completed_at: AwareDatetime
    status: RunReportStatus
    exit_code: int = Field(ge=0, le=255)
    sources: tuple[SourceReport, ...] = Field(max_length=10_000)
    totals: RunTotals
    capabilities_used: tuple[Capability, ...] = ()
    open_drift: Count = 0

    @field_validator("job")
    @classmethod
    def _file_name_only(cls, value: str) -> str:
        if value in {".", ".."}:
            raise ValueError("job must be a file name, not a path")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be before started_at")
        if self.totals.sources != len(self.sources):
            raise ValueError("totals.sources must count the listed sources")
        if len({s.source_index for s in self.sources}) != len(self.sources):
            raise ValueError("source_index values must be unique")
        return self


def run_report_json_schema() -> dict[str, Any]:
    """Return the published JSON Schema (Draft 2020-12) of the run report."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": RUN_REPORT_SCHEMA_ID,
        **RunReport.model_json_schema(),
    }
