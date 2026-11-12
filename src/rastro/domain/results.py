"""Execution outcomes, field provenance, operational metrics, and extraction results."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from rastro.domain.recipes import Recipe
from rastro.domain.sources import EGRESS_NAME, Contract


class Outcome(StrEnum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    MISSING_DATA = "missing_data"
    INVALID_DATA = "invalid_data"
    ACCESS_RESTRICTION = "access_restriction"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Issue(Contract):
    """Safe diagnostics: no input values or user-supplied validation messages."""

    code: str
    record_index: int | None = None
    field: str | None = None


class Evidence(Contract):
    source_url: str
    observed_at: datetime
    method: Literal["jsonld", "css", "json"]
    locator: str
    recipe_version: int


class ExtractionRecord[T: BaseModel](Contract):
    data: T
    evidence: dict[str, Evidence]


class Metrics(Contract):
    inference_calls: int = 0
    inference_seconds: float = 0
    storage_seconds: float = 0
    acquisition_method: Literal["provided_html", "provided_json", "http"] = "provided_html"
    egress: str = Field(default="direct", pattern=EGRESS_NAME)
    acquisition_seconds: float = 0
    network_seconds: float = 0
    request_count: int = 0
    retry_count: int = 0
    redirect_count: int = 0
    bytes_received: int = 0
    input_bytes: int
    candidates: int
    accepted: int
    fields_validated: int = 0
    rejected: int
    extraction_seconds: float
    validation_seconds: float
    total_seconds: float


class RecipeAttempt(Contract):
    origin: Literal["explicit", "memory", "discovery", "model"] = "explicit"
    index: int
    status: Outcome


class RepairProposal(Contract):
    """One stored repair candidate and the gate decision recipe CI gave it."""

    version: int
    decision: Literal["promote", "quarantine", "reject"]
    reasons: tuple[str, ...]
    changed_fields: tuple[str, ...]


class SelfRepair(Contract):
    """What offline self-repair did after a remembered recipe stopped matching.

    ``promoted`` is true only when a candidate passed every gate, became the serving
    recipe and produced this result. ``candidates`` lists every stored proposal.
    """

    mode: Literal["propose", "auto"]
    previous_version: int
    promoted: bool = False
    promoted_version: int | None = None
    candidates: tuple[RepairProposal, ...] = ()


class ExtractionResult[T: BaseModel](Contract):
    learned_recipe: Recipe | None = None
    recipe_version: int | None = None
    recipe_reused: bool = False
    recipe_attempts: tuple[RecipeAttempt, ...] = ()
    requested_url: str | None = None
    http_status: int | None = None
    retry_after_seconds: float | None = None
    job_id: str
    status: Outcome
    source_url: str
    locale: str
    timezone: str
    schema_fingerprint: str
    started_at: datetime
    completed_at: datetime
    records: tuple[ExtractionRecord[T], ...]
    issues: tuple[Issue, ...]
    metrics: Metrics
    repair: SelfRepair | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_repair(self, handler: SerializerFunctionWrapHandler) -> Any:
        # Results without a repair serialize byte-for-byte as they did before the field.
        data = handler(self)
        if isinstance(data, dict) and data.get("repair") is None:
            data.pop("repair", None)
        return data

    @property
    def items(self) -> list[T]:
        """The validated model instances, in record order; empty when nothing validated."""
        return [record.data for record in self.records]
