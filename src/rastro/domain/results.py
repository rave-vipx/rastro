"""Execution outcomes, field provenance, operational metrics, and extraction results."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

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
