"""Read-only job preview: run the job pipeline and show what would be loaded (DATA-002)."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rastro.application.job_loader import load_job
from rastro.application.jobs import run_job
from rastro.domain.job_dataset import (
    DatasetSpec,
    JobPreview,
    PreviewDataset,
    PreviewField,
    PreviewReason,
    PreviewRecord,
    PreviewSource,
)
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import ConfigurationError, ExtractionResult

if TYPE_CHECKING:
    from rastro.ports.acquisition import AcquisitionPort
    from rastro.ports.model import ModelPort

MAX_CELL = 60


def _source_preview(result: ExtractionResult[Any], spec: DatasetSpec, names: list[str]) -> Any:
    by_record: dict[int, list[PreviewReason]] = {}
    source_issues: list[PreviewReason] = []
    for issue in result.issues:
        reason = PreviewReason(code=issue.code, field=issue.field)
        if issue.record_index is None:
            source_issues.append(reason)
        else:
            by_record.setdefault(issue.record_index, []).append(reason)
    total = max(result.metrics.candidates, len(result.records) + len(by_record))
    free = [i for i in range(total) if i not in by_record]
    approved = set(spec.approved_fields(names))
    records = _accepted_records(result, spec, free[: len(result.records)], approved)
    records += [
        PreviewRecord(index=i, status="rejected", reasons=tuple(r)) for i, r in by_record.items()
    ]
    return PreviewSource(
        source_url=result.source_url,
        status=result.status,
        records=tuple(sorted(records, key=lambda item: item.index)),
        issues=tuple(source_issues),
    )


def _accepted_records(
    result: ExtractionResult[Any], spec: DatasetSpec, indexes: list[int], approved: set[str]
) -> list[PreviewRecord]:
    records = []
    for index, record in zip(indexes, result.records, strict=True):
        fields = []
        for name, value in sorted(record.data.model_dump(mode="json").items()):
            evidence = record.evidence.get(name)
            fields.append(
                PreviewField(
                    name=name,
                    value=value,
                    method=evidence.method if evidence else None,
                    locator=evidence.locator if evidence else None,
                    source_url=evidence.source_url if evidence else None,
                    recipe_version=evidence.recipe_version if evidence else None,
                    validation="valid" if evidence else "not_observed",
                    identity=name in spec.identity,
                    loaded=evidence is not None and name in approved,
                )
            )
        records.append(PreviewRecord(index=index, status="accepted", fields=tuple(fields)))
    return records


def build_preview(spec: DatasetSpec, names: list[str], report: JobRunResult[Any]) -> JobPreview:
    """Turn a job run report into a preview; values stay untrusted source data."""
    return JobPreview(
        dataset=PreviewDataset(
            name=spec.name,
            path=spec.path,
            identity=spec.identity,
            approve=spec.approved_fields(names),
            match_tolerance_minutes=spec.match_tolerance_minutes,
        ),
        job=report.job,
        sources=tuple(_source_preview(item, spec, names) for item in report.results),
    )


def preview_job_file(
    path: str | Path,
    *,
    allow_absolute_paths: bool = False,
    acquisition: AcquisitionPort | None = None,
    model: ModelPort | None = None,
) -> JobPreview:
    """Load a job with a ``[dataset]`` block and preview a run. Writes no dataset file."""
    file_path = Path(path)
    job = load_job(file_path, allow_absolute_paths=allow_absolute_paths)
    if job.dataset is None:
        raise ConfigurationError("job preview requires a [dataset] block in the job file")
    if job.recipe_memory is not None:
        raise ConfigurationError(
            "job preview cannot run jobs with recipe_memory: recipe storage would be written"
        )
    report = run_job(
        job,
        base_dir=file_path.parent,
        allow_absolute_paths=allow_absolute_paths,
        acquisition=acquisition,
        model=model,
    )
    return build_preview(job.dataset, [item.name for item in job.fields], report)


def _cell(value: object) -> str:
    """One-line, control-free text: source values never reach the terminal raw."""
    text = "" if value is None else str(value)
    text = "".join(
        f"\\x{ord(ch):02x}" if unicodedata.category(ch)[0] == "C" or ch in "\u2028\u2029" else ch
        for ch in text
    )
    return text if len(text) <= MAX_CELL else text[: MAX_CELL - 1] + "…"


def _record_lines(record: PreviewRecord) -> list[str]:
    if record.status == "rejected":
        why = ", ".join(f"{r.code}({r.field or '-'})" for r in record.reasons)
        return [f"  Record {record.index} rejected: {_cell(why)}"]
    header = ("field", "value", "method", "locator", "validation", "loaded")
    rows = [header] + [
        (
            f.name + ("*" if f.identity else ""),
            _cell(f.value),
            f.method or "-",
            _cell(f.locator or "-"),
            f.validation,
            "yes" if f.loaded else "no",
        )
        for f in record.fields
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    table = ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) for row in rows]
    return [f"  Record {record.index} accepted"] + ["    " + line.rstrip() for line in table]


def render_preview_table(preview: JobPreview) -> str:
    """Human-readable table: per source and record, values, provenance, validation, loading."""
    ds = preview.dataset
    lines = [
        f"Dataset {_cell(ds.name)} ({_cell(ds.path)}) | identity: {', '.join(ds.identity)}"
        f" | data_trust: {preview.data_trust}",
        "Preview only: nothing was written.",
    ]
    for source in preview.sources:
        lines += ["", f"Source {_cell(source.source_url)} [{source.status}]"]
        lines += [f"  issue: {i.code}" for i in source.issues]
        for record in source.records:
            lines += _record_lines(record)
    return "\n".join([*lines, "", "* identity field"])
