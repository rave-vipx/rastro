"""Job `[dataset]` block and the read-only preview contract (DATA-002, JOB-002, AGENT-001).

The block only *declares* where approved records would be stored and how they are identified.
Nothing here opens, creates or writes a dataset file; storage belongs to the dataset store.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Self

from pydantic import (
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    StrictInt,
    StrictStr,
    field_validator,
    model_serializer,
    model_validator,
)

from rastro.domain.job_contract import ENV_REFERENCE, RedactedContract
from rastro.domain.job_results import JobExitCode, JobSummary
from rastro.domain.job_sources import validate_job_path_syntax
from rastro.domain.models import ConfigurationError, Contract, Outcome
from rastro.domain.sources import LocatedValueError

JOB_PREVIEW_SCHEMA_ID = "https://rave-vipx.github.io/rastro/schemas/job-preview.schema.json"
DATASET_NAME = r"^[a-z][a-z0-9_]{0,63}$"


class DatasetSpec(RedactedContract):
    """Where approved records of a job would be stored and which fields identify a record."""

    name: StrictStr = Field(pattern=DATASET_NAME)
    path: StrictStr = Field(default="rastro-state.sqlite", min_length=1, max_length=1024)
    backend: Literal["sqlite", "postgres"] = "sqlite"
    dsn_env: StrictStr | None = Field(default=None, pattern=ENV_REFERENCE, repr=False)
    db_schema: StrictStr = Field(
        default="rastro", alias="schema", pattern=r"^[a-z_][a-z0-9_]{0,62}$"
    )

    @model_validator(mode="after")
    def storage_config(self) -> Self:
        if self.backend == "postgres":
            if self.dsn_env is None or "path" in self.model_fields_set:
                raise ValueError("PostgreSQL requires dsn_env and does not accept path")
        elif (
            "path" not in self.model_fields_set
            or self.dsn_env is not None
            or "db_schema" in self.model_fields_set
        ):
            raise ValueError("SQLite requires path and does not accept PostgreSQL settings")
        return self

    @model_serializer(mode="wrap")
    def serialize_storage(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        schema = data.pop("db_schema", data.pop("schema", self.db_schema))
        if self.backend == "postgres":
            data.pop("path", None)
            data["schema"] = schema
        else:
            data.pop("dsn_env", None)
        return data

    def require_sqlite_views(self) -> None:
        """Studio and MCP read SQLite only; a PostgreSQL job must not look empty there."""
        if self.backend != "sqlite":
            raise ConfigurationError(
                "Studio/MCP dataset views support SQLite only; query PostgreSQL with SQL"
            )

    identity: tuple[StrictStr, ...] = Field(min_length=1, max_length=8)
    approve: tuple[StrictStr, ...] | None = Field(default=None, min_length=1, max_length=100)
    match_fields: tuple[StrictStr, StrictStr, StrictStr] | None = None
    """Two labels and an aware time for uncertain cross-source matches.

    Unset, records are matched on ``identity`` only: equal identities share one record and
    no near match is flagged. No domain-specific field names are assumed.
    """
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
        if self.match_fields is not None:
            where = "dataset.match_fields"
            if len(set(self.match_fields)) != len(self.match_fields):
                raise ConfigurationError(f"{where} must not repeat a field", location=where)
            if not set(self.match_fields) <= set(declared):
                raise ConfigurationError(f"{where} must name declared fields", location=where)
            if not set(self.match_fields) <= set(self.approved_fields(declared)):
                raise ConfigurationError(f"{where} must be listed in approve", location=where)
        known = set(declared)
        approve = self.approved_fields(declared)
        for label, names in (("identity", self.identity), ("approve", approve)):
            where = f"dataset.{label}"
            if len(set(names)) != len(names):
                raise LocatedValueError(f"{where} must not repeat a field", location=where)
            if any(name not in known for name in names):
                raise LocatedValueError(f"{where} must name declared fields", location=where)
        if not set(self.identity) <= set(approve):
            raise LocatedValueError(
                "dataset.identity must be listed in approve", location="dataset.identity"
            )


class PreviewField(Contract):
    """One field of an accepted record: value, provenance, validation and load decision.

    ``not_observed`` marks a field with no source evidence (an optional default); never loaded.
    """

    name: str
    value: JsonValue
    method: Literal["jsonld", "css", "json"] | None = None
    locator: str | None = None
    source_url: str | None = None
    recipe_version: int | None = None
    validation: Literal["valid", "not_observed"]
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
