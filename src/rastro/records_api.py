"""Load records an app adapter built itself into a job's dataset.

The job file supplies the dataset, the declared fields and the identity. Records go through
the same field validation, identity, complete-snapshot (``missing``) and per-field provenance
path as ``rastro job load``. Provenance marks them as adapter data, not a web extraction:
``method = "json"``, ``locator = "adapter"`` and ``recipe_version = 0`` (no recipe ran).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError

from rastro.application.dataset_load import load_stream
from rastro.application.job_dataset import dataset_location, open_dataset
from rastro.application.job_loader import load_job
from rastro.application.run_lock import dataset_lock
from rastro.domain.dataset import DatasetRecordKey, DatasetStorageError
from rastro.domain.job_load import JobLoadResult
from rastro.domain.job_results import JobSummary
from rastro.domain.jobs import Job
from rastro.domain.models import (
    ConfigurationError,
    Evidence,
    ExtractionRecord,
    ExtractionResult,
    Limits,
    Metrics,
    Outcome,
    ResourceLimitError,
    Source,
    schema_fingerprint,
)

ADAPTER_LOCATOR = "adapter"


def load_records(
    job: str | Path,
    records: Sequence[Mapping[str, object]],
    *,
    source_url: str,
    observed_at: datetime | None = None,
    allow_absolute_paths: bool = False,
    allow_remote: bool = False,
    allow_env: Iterable[str] = (),
) -> JobLoadResult:
    """Write ``records`` (dicts keyed by the job's field names) as one observation of
    ``source_url`` into the job file's ``[dataset]``.

    ``records`` is the complete current set for ``source_url``: records loaded earlier from
    that source and absent now are marked ``missing``, as in ``rastro job load``.
    ``observed_at`` (timezone-aware, not in the future) defaults to now. Every record is
    validated before anything is written; an unknown or invalid field, a missing identity
    field, a duplicate identity or an empty ``records`` raises ``ConfigurationError`` and
    writes nothing. ``allow_remote`` and ``allow_env`` are the operator opt-ins a PostgreSQL
    dataset needs, as for ``load_job_file``.
    """
    file = Path(job)
    parsed = load_job(file, allow_absolute_paths=allow_absolute_paths)
    spec = parsed.dataset
    if spec is None:
        raise ConfigurationError("load_records requires a [dataset] block in the job file")
    url = _source_url(source_url)
    moment = _observed_at(observed_at)
    result = _result(parsed, records, url, moment)
    names = [field.name for field in parsed.fields]
    required = [field.name for field in parsed.fields if field.required]
    summary = JobSummary(
        schema_fingerprint=result.schema_fingerprint,
        source_count=1,
        succeeded=1,
        partial=0,
        failed=0,
        records_accepted=len(result.records),
        records_rejected=0,
        status="success",
    )
    raw_path = dataset_location(file, parsed, allow_absolute_paths=allow_absolute_paths)
    allow_env = tuple(allow_env)
    with (
        dataset_lock(raw_path),
        open_dataset(parsed, raw_path, allow_remote=allow_remote, allow_env=allow_env) as store,
    ):
        return load_stream(
            spec,
            names,
            [result],
            store,
            summary=lambda: summary,
            rules=parsed.consistency,
            required=required,
        )


def _source_url(value: str) -> str:
    try:
        url = Source(url=value).evidence_url
    except ValidationError:
        raise ConfigurationError(
            "source_url must be an absolute http(s) URL without credentials", location="source_url"
        ) from None
    parts = urlsplit(value)
    if parts.query or parts.fragment:
        # Provenance never keeps query strings, so two queries would share one snapshot.
        raise ConfigurationError(
            "source_url must not contain a query string or fragment", location="source_url"
        )
    return url


def _observed_at(value: datetime | None) -> datetime:
    now = datetime.now(UTC)
    if value is None:
        return now
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ConfigurationError("observed_at must be a timezone-aware datetime")
    if value > now:
        raise ConfigurationError("observed_at must not be in the future")
    return value


def _result(
    job: Job, records: Sequence[Mapping[str, object]], url: str, moment: datetime
) -> ExtractionResult[BaseModel]:
    if isinstance(records, (str, bytes, Mapping)) or not isinstance(records, Sequence):
        raise ConfigurationError("records must be a list of dicts")
    if not records:
        raise ConfigurationError("records must not be empty; there is nothing to observe")
    if len(records) > (job.limits or Limits()).max_records:
        raise ResourceLimitError("records exceed the job's max_records limit")
    schema = job.build_schema()
    assert job.dataset is not None
    identity = job.dataset.identity
    keys: set[DatasetRecordKey] = set()
    built: list[ExtractionRecord[BaseModel]] = []
    for index, item in enumerate(records):
        data = _validated(schema, item, index, identity)
        values = data.model_dump(mode="json")
        try:
            data.model_dump_json()  # e.g. a lone surrogate cannot be stored as UTF-8
        except PydanticSerializationError:
            raise ConfigurationError(f"Record {index} has a value that is not valid text") from None
        try:
            key = DatasetRecordKey.from_fields(job.dataset.name, values, identity)
        except DatasetStorageError:
            raise ConfigurationError(f"Record {index} has an invalid identity") from None
        if key in keys:
            raise ConfigurationError(f"Record {index} repeats another record's identity")
        keys.add(key)
        evidence = Evidence(
            source_url=url,
            observed_at=moment,
            method="json",
            locator=ADAPTER_LOCATOR,
            recipe_version=0,
        )
        built.append(ExtractionRecord(data=data, evidence=dict.fromkeys(item, evidence)))
    count = len(built)
    return ExtractionResult[BaseModel](
        job_id=str(uuid4()),
        status=Outcome.SUCCESS,
        source_url=url,
        locale=job.region.locale,
        timezone=job.region.timezone,
        schema_fingerprint=schema_fingerprint(schema),
        started_at=moment,
        completed_at=moment,
        records=tuple(built),
        issues=(),
        metrics=Metrics(
            acquisition_method="provided_json",
            input_bytes=0,
            candidates=count,
            accepted=count,
            fields_validated=sum(len(r.evidence) for r in built),
            rejected=0,
            extraction_seconds=0,
            validation_seconds=0,
            total_seconds=0,
        ),
    )


def _validated(
    schema: type[BaseModel], item: object, index: int, identity: tuple[str, ...]
) -> BaseModel:
    """The record as the job's model; errors name the field and Pydantic's type, never a value."""
    if not isinstance(item, Mapping) or not all(isinstance(k, str) for k in item):
        raise ConfigurationError(f"Record {index} must be a dict keyed by field names")
    if not set(item) <= set(schema.model_fields):
        # Caller keys are not echoed; the declared names are the job's own.
        raise ConfigurationError(
            f"Record {index} has a field the job does not declare "
            f"(declared: {', '.join(schema.model_fields)})",
            location=f"records[{index}]",
        )
    for name in identity:
        if item.get(name) is None:
            raise ConfigurationError(
                f"Record {index} lacks identity field {name!r}",
                location=f"records[{index}].{name}",
            )
    try:
        return schema.model_validate(dict(item))
    except ValidationError as error:
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "record"
        raise ConfigurationError(
            f"Record {index} field {field!r} is invalid ({first['type']})",
            location=f"records[{index}].{field}",
        ) from None
