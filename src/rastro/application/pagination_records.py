"""Keep first dataset identities across pages without hiding malformed snapshots."""

from pydantic import BaseModel

from rastro.domain.dataset import DatasetRecordKey, DatasetStorageError
from rastro.domain.job_dataset import DatasetSpec
from rastro.domain.models import ExtractionRecord, ExtractionResult, Issue, Limits, Metrics, Outcome


def deduplicate_pages[T: BaseModel](
    result: ExtractionResult[T],
    dataset: DatasetSpec | None,
) -> ExtractionResult[T]:
    """Same-page duplicates still reach the store's existing rejection boundary."""
    if dataset is None or result.pagination_stop is None:
        return result
    first: dict[DatasetRecordKey, ExtractionRecord[T]] = {}
    seen: set[tuple[DatasetRecordKey, int | None]] = set()
    conflicts = 0
    for record in result.records:
        values = record.data.model_dump(mode="json")
        try:
            key = DatasetRecordKey.from_fields(
                dataset.name,
                {name: value for name, value in values.items() if name in record.evidence},
                dataset.identity,
            )
        except DatasetStorageError:
            return result  # Storage reports invalid identities as before pagination.
        if (key, record.page_index) in seen:
            return result
        seen.add((key, record.page_index))
        if key in first:
            conflicts += first[key].data.model_dump(mode="json") != values
        else:
            first[key] = record
    records = tuple(first.values())
    return result.model_copy(
        update={
            "records": records,
            "pagination_duplicates_conflicting": conflicts,
            "metrics": result.metrics.model_copy(update={"accepted": len(records)}),
        }
    )


def combine_pages[T: BaseModel](
    pages: list[ExtractionResult[T]],
    stop: str,
    limits: Limits,
) -> ExtractionResult[T]:
    first = pages[0]
    records = tuple(
        record.model_copy(
            update={
                "page_index": page_index,
                "evidence": {
                    name: evidence.model_copy(
                        update={
                            "source_url": first.source_url,
                            "page_url": evidence.page_url or evidence.source_url,
                        }
                    )
                    if evidence.source_url != first.source_url
                    else evidence
                    for name, evidence in record.evidence.items()
                },
            }
        )
        for page_index, page in enumerate(pages, 1)
        for record in page.records
    )
    if len(records) > limits.max_records:
        records, stop = records[: limits.max_records], "record_limit"
    issues: list[Issue] = []
    offset = 0
    for page in pages:
        issues.extend(
            issue.model_copy(update={"record_index": issue.record_index + offset})
            if issue.record_index is not None
            else issue
            for issue in page.issues
        )
        offset += len(page.records) + page.metrics.rejected
    if stop != "no_next":
        issues.append(Issue(code=f"pagination_{stop}"))
    status = (
        Outcome.SUCCESS
        if stop == "no_next"
        else (Outcome.PARTIAL_SUCCESS if records else first.status)
    )
    # Keep page one's identity: drift/repair snapshots belong to that document only.
    totals = {
        name: sum(getattr(page.metrics, name) for page in pages)
        for name in Metrics.model_fields
        if name not in {"acquisition_method", "egress"}
    }
    return first.model_copy(
        update={
            "pagination_stop": stop,
            "records": records,
            "issues": tuple(issues),
            "status": status,
            "completed_at": pages[-1].completed_at,
            "metrics": first.metrics.model_copy(update={**totals, "accepted": len(records)}),
        }
    )
