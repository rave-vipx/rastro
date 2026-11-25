"""The acquisition ladder for ``job run``: each URL source climbs like it does in ``job load``.

A job with URL sources and no caller-supplied acquisition port runs source by source through
one shared port (pacing, robots and cookies span its sources) and a :class:`JobLadder`; the
results are joined into one run, with the report built from all of them.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.job_ladder import JobLadder
from rastro.application.job_loader import recipe_memory_path
from rastro.application.model_budget import price_per_call
from rastro.application.run_report import with_report
from rastro.domain.job_results import JobRunResult
from rastro.domain.jobs import Job, UrlJobSource
from rastro.domain.models import ConfigurationError

if TYPE_CHECKING:
    from rastro.application.conditional import ConditionalRequests
    from rastro.ports.acquisition import AcquisitionPort

__all__ = ["job_memory", "job_web_acquirer", "laddered_run", "uses_ladder"]

type RunSource = Callable[[Job, AcquisitionPort | None], JobRunResult[Any]]


def uses_ladder(job: Job) -> bool:
    """Whether ``job`` fetches URL sources under its own acquisition policy."""
    return job.acquisition is not None and any(isinstance(s, UrlJobSource) for s in job.sources)


def job_memory(
    job: Job, base_dir: Path | None, allow_absolute_paths: bool
) -> SQLiteRecipeRepository | None:
    """The job's recipe memory, where the ladder keeps each recipe's start level."""
    path = recipe_memory_path(job, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths)
    return SQLiteRecipeRepository(path) if path is not None else None


def job_web_acquirer(job: Job, conditional: ConditionalRequests | None = None) -> AcquisitionPort:
    """HTTP acquisition, or the browser composition when a source renders.

    Also builds each acquisition ladder level above the job's own, from a copy of the job
    at that level.
    """
    try:
        from rastro.adapters.dns import SystemResolver
        from rastro.adapters.http import CurlTransport
        from rastro.application.acquisition import WebAcquirer
        from rastro.browser import job_acquisition

        assert job.acquisition is not None
        return job_acquisition(job, None, conditional=conditional) or WebAcquirer(
            job.acquisition,
            transport=CurlTransport(),
            resolver=SystemResolver(),
            conditional=conditional,
        )
    except ModuleNotFoundError as error:
        if error.name not in {"curl_cffi", "protego"}:
            raise
        raise ConfigurationError("Install rastro-engine[http] for HTTP acquisition") from None


def laddered_run(
    job: Job,
    run: RunSource,
    *,
    base_dir: Path | None,
    allow_absolute_paths: bool,
    scope: Callable[[], AbstractContextManager[object]],
    rotate: bool,
) -> JobRunResult[Any]:
    """Run every source of ``job`` through the ladder and join them into one result."""
    with scope():  # the render-mode consent check runs here, before any source is run
        base = job_web_acquirer(job)
    ladder = JobLadder(
        job,
        base,
        build=job_web_acquirer,
        scope=scope,
        rotate=rotate,
        memory=job_memory(job, base_dir, allow_absolute_paths),
    )
    reports = [
        ladder.run(job.model_copy(update={"sources": (source,)}), run) for source in job.sources
    ]
    results = [report.results[0] for report in reports]
    selections = tuple(
        selection.model_copy(update={"source_index": number})
        for number, report in enumerate(reports, start=1)
        for selection in report.recipes
    )
    joined = JobRunResult.from_results(
        schema_fingerprint=reports[0].job.schema_fingerprint, results=results
    )
    return with_report(joined, selections, price_per_call(job.model))
