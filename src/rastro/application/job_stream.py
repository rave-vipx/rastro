"""Stream a job's per-source results while the run is still going.

``stream_job_async`` runs the same trust, egress, auth and recipe checks as ``run_job_async``,
then extracts the sources one after another and yields each result as soon as it exists, so
Studio can show the first records of a long crawl immediately. Sources run sequentially here;
use ``run_job_async`` for the concurrent batch of plain URL sources.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rastro.application.active_recipes import active_job
from rastro.application.auth_headers import check_job_auth
from rastro.application.crawl_checkpoint import crawl_key, crawl_state
from rastro.application.egress_trust import check_job_egress
from rastro.application.job_execution import extract_one_async
from rastro.application.job_loader import prepare_job
from rastro.application.model_budget import model_cost_override
from rastro.application.operator_trust import operator_trust
from rastro.application.pagination_records import deduplicate_pages
from rastro.application.robots_cache import robots_cache
from rastro.domain.jobs import Job
from rastro.domain.models import ExtractionResult

if TYPE_CHECKING:
    from rastro.ports.acquisition import AcquisitionPort
    from rastro.ports.model import ModelPort

_DONE = object()


async def _produce(
    queue: asyncio.Queue[Any],
    job: Job,
    *,
    base_dir: Path | None,
    allow_absolute_paths: bool,
    acquisition: AcquisitionPort | None,
    model: ModelPort | None,
    allow_egress: bool,
    allow_env: tuple[str, ...],
    trust: dict[str, Any],
    max_model_cost: float | None,
    model_cost_per_call: float | None,
) -> None:
    # One task owns every context variable scope, so the consumer may run anywhere.
    try:
        with ExitStack() as scopes:
            scopes.enter_context(operator_trust(allow_env=allow_env, **trust))
            scopes.enter_context(model_cost_override(max_model_cost, model_cost_per_call))
            scopes.enter_context(robots_cache())
            scopes.enter_context(crawl_state(base_dir, crawl_key(job)))
            check_job_egress(job, allow_egress=allow_egress, allow_env=allow_env)
            check_job_auth(job, allow_env=allow_env)
            active, _ = active_job(
                job, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
            )
            prepared = prepare_job(
                active,
                base_dir=base_dir,
                allow_absolute_paths=allow_absolute_paths,
                acquisition=acquisition,
                model=model,
            )
            for index, item in enumerate(active.sources):
                result = await extract_one_async(active, prepared, index, item)
                await queue.put(deduplicate_pages(result, active.dataset))
    except Exception as error:
        await queue.put(error)
    finally:
        await queue.put(_DONE)


async def stream_job_async(
    job: Job,
    *,
    base_dir: Path | None = None,
    allow_absolute_paths: bool = False,
    acquisition: AcquisitionPort | None = None,
    model: ModelPort | None = None,
    allow_remote: bool = False,
    allow_egress: bool = False,
    allow_stealth: bool = False,
    allow_env: Iterable[str] = (),
    allow_private_network: bool = False,
    allow_ports: Iterable[int] = (),
    ignore_robots: Iterable[str] = (),
    max_model_cost: float | None = None,
    model_cost_per_call: float | None = None,
) -> AsyncIterator[ExtractionResult[Any]]:
    """Yield one ``ExtractionResult`` per source as it finishes; see ``run_job_async``.

    A configuration refusal (egress, auth, paths) is raised from the first ``anext``.
    Closing the iterator early cancels the sources not yet fetched.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
    producer = asyncio.create_task(
        _produce(
            queue,
            job,
            base_dir=base_dir,
            allow_absolute_paths=allow_absolute_paths,
            acquisition=acquisition,
            model=model,
            allow_egress=allow_egress,
            allow_env=tuple(allow_env),
            trust={
                "allow_remote": allow_remote,
                "allow_stealth": allow_stealth,
                "allow_private_network": allow_private_network,
                "allow_ports": allow_ports,
                "ignore_robots": ignore_robots,
            },
            max_model_cost=max_model_cost,
            model_cost_per_call=model_cost_per_call,
        )
    )
    try:
        while (item := await queue.get()) is not _DONE:
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


__all__ = ["stream_job_async"]
