"""Record one metrics row per source after a load (OBS-003, N5); SQLite datasets only."""

from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from rastro.domain.dataset import DatasetStorageError
from rastro.domain.jobs import Job
from rastro.domain.metrics import metric_from_result, reached_cap
from rastro.domain.models import ExtractionResult, Limits
from rastro.domain.recipes import Recipe
from rastro.ports.metrics import RunMetricsRecorder


def record_run_metrics(
    store: object,
    dataset: str,
    results: Sequence[ExtractionResult[Any]],
    required: Sequence[str],
    *,
    inconsistent: Sequence[bool] = (),
    job: Job | None = None,
) -> bool:
    """Whether recording failed; stores without metrics (Postgres) record nothing.

    ``inconsistent`` lines up with ``results``; ``job`` gives a reached cap its value.
    """
    if not isinstance(store, RunMetricsRecorder):
        return False
    run_id = uuid4().hex
    refused = [*inconsistent, *[False] * (len(results) - len(inconsistent))]
    try:
        store.record_run_metrics(
            dataset,
            [
                metric_from_result(
                    run_id, r, required, inconsistent=bad, cap_value=cap_value(job, r)
                )
                for r, bad in zip(results, refused, strict=True)
            ],
        )
    except DatasetStorageError:
        return True
    return False


def cap_value(job: Job | None, result: ExtractionResult[Any]) -> int | None:
    """The configured limit behind the result's reached cap; None when it is not known."""
    cap = reached_cap(result)
    if job is None or cap is None:
        return None
    if cap.endswith("_record_limit"):
        return (job.limits or Limits()).max_records
    if cap.endswith("_budget"):
        return job.acquisition.max_requests if job.acquisition else None
    if cap == "scroll_max_steps":
        return job.scroll.max_steps if job.scroll else None
    if cap.startswith("spider_"):
        spider = job.spider
        if spider is None:
            return None
        if cap == "spider_page_limit":  # an unbounded crawl has no page cap
            return spider.max_pages if isinstance(spider.max_pages, int) else None
        return spider.max_depth
    recipe = result.learned_recipe or _source_recipe(job, result) or job.recipe
    # shortcut: a recipe served from memory without learned_recipe has no known page cap.
    return recipe.pagination.max_pages if recipe and recipe.pagination else None


def _source_recipe(job: Job, result: ExtractionResult[Any]) -> Recipe | None:
    urls = {result.source_url, result.requested_url}
    for source in job.sources:
        url = source.url if source.kind == "url" else source.resolved_source_url()
        if url in urls and source.recipe is not None:
            return source.recipe
    return None
