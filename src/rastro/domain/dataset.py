"""Dataset identity and observations (DATA-001/004/005, SYNC-002/003/005/006)."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from math import isfinite
from typing import Self
from unicodedata import normalize

from pydantic import (
    AwareDatetime,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from rastro.domain.memory import utc_timestamp
from rastro.domain.results import Evidence
from rastro.domain.sources import Contract, RastroError, Source


class DatasetStorageError(RastroError):
    """Invalid dataset input or storage failure; diagnostics contain no payloads."""


class DatasetNotFoundError(DatasetStorageError):
    """The dataset file does not exist; history has not been established."""


type IdentityValue = str | StrictInt | StrictFloat | StrictBool


def identity_value(value: object) -> IdentityValue:
    if isinstance(value, datetime):
        return utc_timestamp(value)
    if isinstance(value, (int, bool)) or (isinstance(value, float) and isfinite(value)):
        return value
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise DatasetStorageError(
            "Identity requires bounded nonblank strings, finite scalars or aware datetimes"
        )
    value = " ".join(value.split())
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return normalize("NFC", value.casefold())
    return utc_timestamp(parsed) if parsed.tzinfo is not None else value.casefold()


class DatasetRecordKey(Contract):
    dataset: str = Field(min_length=1, max_length=128, pattern=r"^[\w.-]+$")
    identity: tuple[IdentityValue, ...] = Field(min_length=1, max_length=32)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, DatasetRecordKey)
            and self.model_dump_json() == other.model_dump_json()
        )

    def __hash__(self) -> int:
        return hash(self.model_dump_json())

    @field_validator("identity", mode="before")
    @classmethod
    def normalized(cls, values: tuple[object, ...]) -> tuple[IdentityValue, ...]:
        if not isinstance(values, (tuple, list)):
            raise DatasetStorageError("Identity must be a tuple of values")
        return tuple(identity_value(value) for value in values)

    @classmethod
    def from_fields(
        cls, dataset: str, fields: Mapping[str, object], identity_fields: tuple[str, ...]
    ) -> Self:
        try:
            return cls(
                dataset=dataset, identity=tuple(identity_value(fields[f]) for f in identity_fields)
            )
        except (KeyError, ValueError, TypeError, OverflowError):
            raise DatasetStorageError("Invalid dataset identity") from None


class ObservedRecord(Contract):
    key: DatasetRecordKey
    source_url: str
    observed_at: AwareDatetime
    fields: dict[str, JsonValue] = Field(min_length=1, max_length=256)
    provenance: dict[str, Evidence]

    @model_validator(mode="after")
    def observed_fields(self) -> Self:
        if Source(url=self.source_url).evidence_url != self.source_url:
            raise ValueError("Dataset sources must be canonical evidence URLs")
        if self.fields.keys() != self.provenance.keys() or any(
            e.source_url != self.source_url
            or e.observed_at.tzinfo is None
            or e.observed_at > self.observed_at
            for e in self.provenance.values()
        ):
            raise ValueError("Every approved field requires evidence from this source observation")
        return self


class ChangeKind(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    MISSING = "missing"
    UNCERTAIN = "uncertain"


class SnapshotReport(Contract):
    keys: dict[ChangeKind, tuple[DatasetRecordKey, ...]]

    @property
    def counts(self) -> dict[ChangeKind, int]:
        return {kind: len(self.keys.get(kind, ())) for kind in ChangeKind}


class RecordOrigin(ObservedRecord):
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime


class UncertainMatch(Contract):
    keys: tuple[DatasetRecordKey, DatasetRecordKey]
    origins: tuple[str, str]


class StoredRecord(RecordOrigin):
    origins: dict[str, RecordOrigin]
    uncertain_match: tuple[UncertainMatch, ...] = ()


class SourceStatus(Contract):
    source_url: str
    last_success: AwareDatetime | None
    last_attempt: AwareDatetime
    stale: bool
    reason: str | None = None


def near_match(
    left: ObservedRecord, right: ObservedRecord, names: tuple[str, str, str], tolerance: timedelta
) -> bool:
    """Compare configured labels and an aware time; no domain-specific field inference."""
    if (
        left.key.model_dump_json() == right.key.model_dump_json()
        or left.source_url == right.source_url
    ):
        return False
    try:
        a, b = left.fields, right.fields
        labels_equal = all(identity_value(a[n]) == identity_value(b[n]) for n in names[:2])
        start_a, start_b = (
            datetime.fromisoformat(str(a[names[2]])),
            datetime.fromisoformat(str(b[names[2]])),
        )
        return (
            labels_equal
            and abs(start_a - start_b) <= tolerance
            and start_a.tzinfo is not None
            and start_b.tzinfo is not None
        )
    except (KeyError, ValueError, TypeError, DatasetStorageError):
        return False
