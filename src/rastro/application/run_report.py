"""Build the content-free run report from a job run or load.

The report holds hosts, paths, counts, outcome codes and timings. Records, page bodies,
URL queries and secrets never enter it; source URLs are reduced to their provenance form.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from rastro.application import run_report_facts as facts
from rastro.domain.capability_grants import CAPABILITIES
from rastro.domain.job_load import JobLoadResult, LoadedSource
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import ExtractionResult, Outcome
from rastro.domain.run_report import RunReport, RunTotals, SourceReport, SourceTiming
from rastro.domain.source_notes import CODE_PATTERN, CrawlNote

__all__ = ["named", "report_for_load", "report_for_run", "with_report"]


def report_for_run(job_name: str, result: JobRunResult[Any]) -> RunReport:
    """The report of one job run; ``job_name`` is the job file's name, never a path."""
    sources = tuple(_source(i, item) for i, item in enumerate(result.results, start=1))
    return _report("run", job_name, None, result.results, sources, result.job.status, result, 0)


def report_for_load(job_name: str, run: JobRunResult[Any], load: JobLoadResult) -> RunReport:
    """The report of one load: the run's sources plus what each stored."""
    results = run.results
    sources = tuple(
        _source(i, results[i - 1], loaded) if i <= len(results) else _unrun(i, loaded)
        for i, loaded in enumerate(load.sources, start=1)
    )
    return _report(
        "load", job_name, load.dataset, results, sources, load.status, load, len(load.open_drift)
    )


def with_report[R: JobRunResult[Any]](result: R, selections: tuple[Any, ...]) -> R:
    """``result`` with its recipe selections and its run report (named for a file later)."""
    result = result.model_copy(update={"recipes": selections})
    return result.model_copy(update={"report": report_for_run("job", result)})


def named[R: JobRunResult[Any]](result: R, job_name: str) -> R:
    """``result`` with its report renamed to the job file that was run."""
    if result.report is None:
        return result
    return result.model_copy(update={"report": result.report.model_copy(update={"job": job_name})})


def _report(
    kind: Any,
    job_name: str,
    dataset: str | None,
    results: Sequence[ExtractionResult[Any]],
    sources: tuple[SourceReport, ...],
    status: Any,
    outcome: Any,
    open_drift: int,
) -> RunReport:
    times = [(facts.aware(r.started_at), facts.aware(r.completed_at)) for r in results]
    started = min((t[0] for t in times), default=datetime.now(UTC))
    completed = max((t[1] for t in times), default=started)
    used = {c for r in results if r.notes for c in r.notes.capabilities_used}
    return RunReport(
        kind=kind,
        job=job_name,
        dataset=dataset,
        started_at=started,
        completed_at=max(completed, started),
        status=status,
        exit_code=int(outcome.exit_code),
        sources=sources,
        totals=_totals(results, sources),
        capabilities_used=tuple(c for c in CAPABILITIES if c in used),
        open_drift=open_drift,
    )


def _totals(
    results: Iterable[ExtractionResult[Any]], sources: tuple[SourceReport, ...]
) -> RunTotals:
    ok = sum(s.outcome in facts.OK for s in sources)
    partial = sum(s.outcome == Outcome.PARTIAL_SUCCESS for s in sources)
    repairs = [r.repair for r in results if r.repair]
    return RunTotals(
        sources=len(sources),
        succeeded=ok,
        partial=partial,
        failed=len(sources) - ok - partial,
        blocked=sum(s.reason == "blocked" or s.blocked is not None for s in sources),
        pages=sum(s.pages for s in sources),
        records_accepted=sum(s.records_accepted for s in sources),
        records_rejected=sum(s.records_rejected for s in sources),
        model_calls=sum(r.metrics.inference_calls for r in results),
        repairs_without_model=sum(r.promoted and r.model_calls == 0 for r in repairs),
    )


def _source(
    index: int, result: ExtractionResult[Any], loaded: LoadedSource | None = None
) -> SourceReport:
    outcome = loaded.status if loaded else result.status
    host, path = facts.location(result.source_url)
    notes = result.notes
    pages = facts.pages(result)
    reason = facts.reason(result, outcome)
    crawl = notes.crawl if notes else None
    if crawl is None and result.spider is not None:
        stop = reason if reason in {"page_cap", "depth_cap", "request_budget"} else "complete"
        crawl = CrawlNote(mode="bounded", pages_fetched=pages, stop=stop)
    detail = (loaded.reason if loaded else None) or facts.detail(result)
    metrics = result.metrics
    return SourceReport(
        source_index=index,
        host=host,
        path=path,
        outcome=outcome,
        reason=reason,
        detail=detail if detail and re.fullmatch(CODE_PATTERN, detail) else None,
        pages=pages,
        records_accepted=len(result.records),
        records_rejected=metrics.rejected,
        rejected_by_code=facts.rejected_by_code(result),
        fields_nulled=dict(notes.fields_nulled) if notes else {},
        stored=dict(loaded.counts) if loaded else None,
        acquisition=notes.acquisition if notes and notes.acquisition else facts.acquisition(result),
        discovery=notes.discovery if notes else None,
        crawl=crawl,
        repair=notes.repair if notes else None,
        blocked=notes.blocked if notes else None,
        seconds=SourceTiming(
            acquisition=metrics.acquisition_seconds,
            extraction=metrics.extraction_seconds,
            validation=metrics.validation_seconds,
            total=metrics.total_seconds,
        ),
    )


def _unrun(index: int, loaded: LoadedSource) -> SourceReport:
    """A loaded source whose run result is gone (for example a stopped load)."""
    host, path = facts.location(loaded.source_url)
    detail = loaded.reason if loaded.reason and re.fullmatch(CODE_PATTERN, loaded.reason) else None
    return SourceReport(
        source_index=index,
        host=host,
        path=path,
        outcome=loaded.status,
        reason="complete" if loaded.status in facts.OK else "failed",
        detail=detail,
        stored=dict(loaded.counts),
    )
