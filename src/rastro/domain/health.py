"""Operator health: open drift, stale sources and pending repair (DRIFT-005, REPAIR-005, OPS)."""

from typing import Any, Literal

from pydantic import AwareDatetime, Field

from rastro.domain.drift import Rate
from rastro.domain.job_results import JobExitCode
from rastro.domain.models import Contract

HEALTH_SCHEMA_ID = "https://rave-vipx.github.io/rastro/schemas/health.schema.json"


class OpenDrift(Contract):
    """An unacknowledged elevated or high drift on a successful run of one source.

    While it is open the drift baseline stays at ``baseline_run``; acknowledging it, or
    promoting a repaired recipe for the source, clears it.
    """

    source_url: str
    run_id: str
    baseline_run: str | None
    observed_at: AwareDatetime
    risk: Literal["elevated", "high"]
    score: Rate
    verdict: Literal["structural_drift", "extraction_failure", "none"]


type Attention = Literal["never_loaded", "stale", "open_drift", "quarantined_candidate"]


class SourceHealth(Contract):
    source_index: int = Field(ge=1)
    source_url: str
    last_success: AwareDatetime | None
    last_attempt: AwareDatetime | None
    stale: bool | None
    reason: str | None
    detail: str | None
    open_drift: OpenDrift | None
    quarantined_versions: tuple[int, ...]
    attention: tuple[Attention, ...]


class JobHealth(Contract):
    schema_version: Literal[1] = 1
    dataset: str
    healthy: bool
    sources: tuple[SourceHealth, ...] = Field(min_length=1)
    orphaned_sources: tuple[str, ...]
    """Dataset sources no longer in the job; never affect health (``rastro job forget-source``)."""

    @property
    def exit_code(self) -> JobExitCode:
        return JobExitCode.SUCCESS if self.healthy else JobExitCode.NEEDS_ATTENTION


def job_health_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": HEALTH_SCHEMA_ID,
        **JobHealth.model_json_schema(),
    }
