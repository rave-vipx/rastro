"""Execute declarative jobs synchronously or asynchronously through the public Rastro SDK."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rastro.application.active_recipes import active_job
from rastro.application.auth_headers import check_job_auth
from rastro.application.egress_trust import check_job_egress
from rastro.application.job_execution import run_active, run_active_async
from rastro.application.job_loader import (
    load_job,
    resolve_job_path,
    validate_job_paths,
)
from rastro.application.model_budget import model_cost_override
from rastro.application.operator_trust import operator_trust
from rastro.application.robots_cache import robots_cache
from rastro.application.run_log import log_failures
from rastro.application.run_report import named, with_report
from rastro.domain.job_results import JobRunResult
from rastro.domain.jobs import Job

if TYPE_CHECKING:
    from rastro.ports.acquisition import AcquisitionPort
    from rastro.ports.model import ModelPort


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
        report = await run_active_async(
            active,
            base_dir=base_dir,
            allow_absolute_paths=allow_absolute_paths,
            acquisition=acquisition,
            model=model,
        )
        log_failures(report)
    return with_report(report, selections)


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
        report = run_active(
            active,
            base_dir=base_dir,
            allow_absolute_paths=allow_absolute_paths,
            acquisition=acquisition,
            model=model,
        )
        log_failures(report)
    return with_report(report, selections)


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
    result = run_job(
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
    return named(result, file_path.name)


__all__ = [
    "load_job",
    "resolve_job_path",
    "run_job",
    "run_job_async",
    "run_job_file",
    "validate_job_paths",
]
