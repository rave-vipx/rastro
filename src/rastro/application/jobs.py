"""Execute declarative jobs synchronously or asynchronously through the public Rastro SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rastro.application.active_recipes import active_job
from rastro.application.auth_headers import check_job_auth
from rastro.application.egress_trust import check_job_egress
from rastro.application.job_loader import (
    PreparedJob,
    load_job,
    prepare_job,
    resolve_job_path,
    validate_job_paths,
)
from rastro.application.model_budget import model_cost_override
from rastro.application.operator_trust import operator_trust
from rastro.application.pagination_records import deduplicate_pages
from rastro.application.robots_cache import robots_cache
from rastro.application.run_log import log_failures
from rastro.domain.job_results import JobRunResult
from rastro.domain.jobs import FileJobSource, Job, UrlJobSource
from rastro.domain.models import ExtractionResult

if TYPE_CHECKING:
    from rastro.ports.acquisition import AcquisitionPort
    from rastro.ports.model import ModelPort


async def _extract_one_async(
    job: Job, prepared: PreparedJob, index: int, item: FileJobSource | UrlJobSource
) -> ExtractionResult[Any]:
    recipe = item.recipe or job.recipe
    if isinstance(item, FileJobSource):
        src = (item.region or job.region).to_source(item.resolved_source_url())
        extractor = (
            prepared.client.extract_json_async
            if item.representation == "json"
            else prepared.client.extract_html_async
        )
        return await extractor(
            prepared.bodies[index],
            source=src,
            schema=prepared.schema,
            recipe=recipe,
            context=job.context,
            recipe_version=job.recipe_version,
        )
    return await prepared.client.extract(
        source=(item.region or job.region).to_source(item.url),
        schema=prepared.schema,
        recipe=recipe,
        context=job.context,
        recipe_version=job.recipe_version,
        representation=item.representation,
    )


async def run_job_async(
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
) -> JobRunResult[Any]:
    """Execute a validated job asynchronously through the public Rastro SDK.

    With ``recipe_memory``, a stored serving recipe newer than the job file's is used;
    ``JobRunResult.recipes`` reports the recipe each source used. The operator, not the
    job file, allows a remote model endpoint (``allow_remote``), a job-declared egress
    proxy (``allow_egress``), extra secret variable names (``allow_env``), private-network
    destinations (``allow_private_network``), extra ports (``allow_ports``) and hosts whose
    robots.txt is not enforced (``ignore_robots``). A job file can never grant any of them
    or make Rastro start a model command. ``max_model_cost`` (USD) caps every model
    call and needs a price, ``model_cost_per_call`` or the job's ``[model] cost_per_call``.
    """
    allow_env = tuple(allow_env)
    with (
        operator_trust(
            allow_remote=allow_remote,
            allow_stealth=allow_stealth,
            allow_env=allow_env,
            allow_private_network=allow_private_network,
            allow_ports=allow_ports,
            ignore_robots=ignore_robots,
        ),
        model_cost_override(max_model_cost, model_cost_per_call),
        robots_cache(),
    ):
        check_job_egress(job, allow_egress=allow_egress, allow_env=allow_env)
        check_job_auth(job, allow_env=allow_env)
        active, selections = active_job(
            job, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
        )
        report = await _run_active_async(
            active,
            base_dir=base_dir,
            allow_absolute_paths=allow_absolute_paths,
            acquisition=acquisition,
            model=model,
        )
        log_failures(report)
    return report.model_copy(update={"recipes": selections})


async def _run_active_async(
    job: Job,
    *,
    base_dir: Path | None,
    allow_absolute_paths: bool,
    acquisition: AcquisitionPort | None,
    model: ModelPort | None,
) -> JobRunResult[Any]:
    prepared = prepare_job(
        job,
        base_dir=base_dir,
        allow_absolute_paths=allow_absolute_paths,
        acquisition=acquisition,
        model=model,
    )
    if (
        all(isinstance(item, UrlJobSource) and item.recipe is None for item in job.sources)
        and len({item.representation for item in job.sources}) == 1
    ):
        batch = [
            (item.region or job.region).to_source(item.url)
            for item in job.sources
            if isinstance(item, UrlJobSource)
        ]
        results = await prepared.client.extract_many(
            batch,
            schema=prepared.schema,
            recipe=job.recipe,
            context=job.context,
            recipe_version=job.recipe_version,
            representation=job.sources[0].representation,
        )
        return JobRunResult.from_results(
            schema_fingerprint=prepared.fingerprint,
            results=[deduplicate_pages(result, job.dataset) for result in results],
        )
    collected = [
        await _extract_one_async(job, prepared, idx, item) for idx, item in enumerate(job.sources)
    ]
    return JobRunResult.from_results(
        schema_fingerprint=prepared.fingerprint,
        results=[deduplicate_pages(result, job.dataset) for result in collected],
    )


def run_job(
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
) -> JobRunResult[Any]:
    """Run a validated job; uses synchronous extraction when offline and model-free.

    With ``recipe_memory``, a stored serving recipe newer than the job file's is used;
    ``JobRunResult.recipes`` reports the recipe each source used. The operator, not the
    job file, allows a remote model endpoint (``allow_remote``), a job-declared egress
    proxy (``allow_egress``), extra secret variable names (``allow_env``), private-network
    destinations (``allow_private_network``), extra ports (``allow_ports``) and hosts whose
    robots.txt is not enforced (``ignore_robots``). A job file can never grant any of them
    or make Rastro start a model command. ``max_model_cost`` (USD) caps every model
    call and needs a price, ``model_cost_per_call`` or the job's ``[model] cost_per_call``.
    """
    allow_env = tuple(allow_env)
    with (
        operator_trust(
            allow_remote=allow_remote,
            allow_stealth=allow_stealth,
            allow_env=allow_env,
            allow_private_network=allow_private_network,
            allow_ports=allow_ports,
            ignore_robots=ignore_robots,
        ),
        model_cost_override(max_model_cost, model_cost_per_call),
        robots_cache(),
    ):
        check_job_egress(job, allow_egress=allow_egress, allow_env=allow_env)
        check_job_auth(job, allow_env=allow_env)
        active, selections = active_job(
            job, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
        )
        report = _run_active(
            active,
            base_dir=base_dir,
            allow_absolute_paths=allow_absolute_paths,
            acquisition=acquisition,
            model=model,
        )
        log_failures(report)
    return report.model_copy(update={"recipes": selections})


def _run_active(
    job: Job,
    *,
    base_dir: Path | None,
    allow_absolute_paths: bool,
    acquisition: AcquisitionPort | None,
    model: ModelPort | None,
) -> JobRunResult[Any]:
    if (
        any(isinstance(item, UrlJobSource) for item in job.sources)
        or job.discover_recipes
        or job.model is not None
        or model is not None
    ):
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(
                _run_active_async(
                    job,
                    base_dir=base_dir,
                    allow_absolute_paths=allow_absolute_paths,
                    acquisition=acquisition,
                    model=model,
                )
            )
    prepared = prepare_job(
        job,
        base_dir=base_dir,
        allow_absolute_paths=allow_absolute_paths,
        acquisition=None,
        model=None,
    )
    collected: list[ExtractionResult[Any]] = []
    for idx, item in enumerate(job.sources):
        assert isinstance(item, FileJobSource)
        extractor = (
            prepared.client.extract_json
            if item.representation == "json"
            else prepared.client.extract_html
        )
        collected.append(
            extractor(
                prepared.bodies[idx],
                source=(item.region or job.region).to_source(item.resolved_source_url()),
                schema=prepared.schema,
                recipe=item.recipe or job.recipe,
                context=job.context,
                recipe_version=job.recipe_version,
            )
        )
    return JobRunResult.from_results(
        schema_fingerprint=prepared.fingerprint,
        results=[deduplicate_pages(result, job.dataset) for result in collected],
    )


def run_job_file(
    path: str | Path,
    *,
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
) -> JobRunResult[Any]:
    """Load and execute a declarative job file; see ``run_job`` for the ``allow_*`` options."""
    file_path = Path(path)
    job = load_job(file_path, allow_absolute_paths=allow_absolute_paths)
    return run_job(
        job,
        base_dir=file_path.parent,
        allow_absolute_paths=allow_absolute_paths,
        acquisition=acquisition,
        model=model,
        allow_remote=allow_remote,
        allow_egress=allow_egress,
        allow_stealth=allow_stealth,
        allow_env=allow_env,
        allow_private_network=allow_private_network,
        allow_ports=allow_ports,
        ignore_robots=ignore_robots,
        max_model_cost=max_model_cost,
        model_cost_per_call=model_cost_per_call,
    )


__all__ = [
    "load_job",
    "resolve_job_path",
    "run_job",
    "run_job_async",
    "run_job_file",
    "validate_job_paths",
]
