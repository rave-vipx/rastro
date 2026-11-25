"""Read the records a job's dataset holds, with provenance and origins.

``read_records`` is the Python counterpart of ``rastro dataset export``: it opens the
``[dataset]`` the job file declares, read only, and returns validated models. Everything in
``records`` is third-party data; the result says so in ``data_trust``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, JsonValue

from rastro.application.job_dataset import dataset_location, open_dataset
from rastro.application.job_loader import load_job
from rastro.domain.dataset import DatasetNotFoundError, StoredRecord
from rastro.domain.models import ConfigurationError, Evidence
from rastro.domain.sources import Contract


class ReadRecord(Contract):
    """One stored record: merged fields, per-field provenance and every source it came from."""

    key: str
    fields: dict[str, JsonValue]
    provenance: dict[str, Evidence]
    origins: tuple[str, ...]
    first_observed_at: AwareDatetime
    last_observed_at: AwareDatetime
    uncertain_match: bool


class DatasetRead(Contract):
    schema_version: Literal[1] = 1
    data_trust: Literal["untrusted_source_data"] = "untrusted_source_data"
    dataset: str
    records: tuple[ReadRecord, ...]


def read_records(
    job: str | Path,
    *,
    dataset: str | None = None,
    since: datetime | timedelta | None = None,
    fields: Sequence[str] | None = None,
    allow_absolute_paths: bool = False,
    allow_remote: bool = False,
    allow_env: Iterable[str] = (),
) -> DatasetRead:
    """Records of the job file's ``[dataset]``, optionally newer and narrowed to ``fields``.

    ``since`` keeps records observed at or after a timezone-aware datetime, or within a
    timedelta of now. ``fields`` keeps only those fields (and their provenance). ``dataset``
    must name the job's dataset when given. Raises ``ConfigurationError`` for a job without a
    ``[dataset]``, an unknown dataset or field name, a naive or negative ``since``, or a
    dataset that was never written. ``allow_*`` are the operator opt-ins of ``load_job_file``.
    """
    file = Path(job)
    parsed = load_job(file, allow_absolute_paths=allow_absolute_paths)
    spec = parsed.dataset
    if spec is None:
        raise ConfigurationError("read_records requires a [dataset] block in the job file")
    if dataset is not None and dataset != spec.name:
        raise ConfigurationError("dataset is not the job's dataset", location="dataset")
    selected = _selected(fields, tuple(field.name for field in parsed.fields))
    cutoff = _cutoff(since)
    path = dataset_location(file, parsed, allow_absolute_paths=allow_absolute_paths)
    with open_dataset(parsed, path, allow_remote=allow_remote, allow_env=tuple(allow_env)) as store:
        try:
            stored = store.records(spec.name)
        except DatasetNotFoundError:
            raise ConfigurationError(
                "The job's dataset has no records yet",
                hint="Create it with `rastro job load <job file>`",
            ) from None
    kept = (r for r in stored if cutoff is None or r.last_observed_at >= cutoff)
    return DatasetRead(dataset=spec.name, records=tuple(_read(r, selected) for r in kept))


def _selected(fields: Sequence[str] | None, declared: tuple[str, ...]) -> tuple[str, ...] | None:
    if fields is None:
        return None
    if isinstance(fields, str) or not fields:
        raise ConfigurationError("fields must be a non-empty list of field names")
    unknown = [name for name in fields if name not in declared]
    if unknown:
        raise ConfigurationError(
            f"Field {unknown[0]!r} is not declared by the job (declared: {', '.join(declared)})",
            location="fields",
        )
    return tuple(fields)


def _cutoff(since: datetime | timedelta | None) -> datetime | None:
    if since is None:
        return None
    if isinstance(since, timedelta):
        if since < timedelta(0):
            raise ConfigurationError("since must not be a negative timedelta")
        return datetime.now(UTC) - since
    if since.tzinfo is None or since.utcoffset() is None:
        raise ConfigurationError("since must be a timezone-aware datetime")
    return since


def _read(record: StoredRecord, selected: tuple[str, ...] | None) -> ReadRecord:
    names = [n for n in record.fields if selected is None or n in selected]
    return ReadRecord(
        key=record.key.model_dump_json(),
        fields={name: record.fields[name] for name in names},
        provenance={name: record.provenance[name] for name in names},
        origins=tuple(sorted(record.origins)),
        first_observed_at=record.first_observed_at,
        last_observed_at=record.last_observed_at,
        uncertain_match=bool(record.uncertain_match),
    )
