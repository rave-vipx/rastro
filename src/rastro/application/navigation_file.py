"""Explore a job file's first page and write the explored recipe as a new job file.

The operator starts it (``rastro new --explore JOB -o NEW``, or the Python API); nothing is
changed in the original job. The new file keeps the job's fields, URL sources, allowed hosts
and dataset, carries the kept page steps and renders in a browser, which the steps need.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rastro.application.job_ladder_run import job_web_acquirer
from rastro.application.job_loader import load_job
from rastro.application.job_new import write_validated_job
from rastro.application.job_render import render_job
from rastro.application.model_choice import ModelChoice
from rastro.application.navigation_explorer import ExplorationResult
from rastro.application.navigation_job import explore_job
from rastro.application.operator_trust import operator_trust
from rastro.domain.jobs import UrlJobSource
from rastro.domain.models import ConfigurationError

__all__ = ["explore_job_file"]


def explore_job_file(
    job_path: str | Path,
    output: str | Path,
    *,
    model: ModelChoice,
    expected_records: int | None = None,
    allow_remote: bool = False,
) -> ExplorationResult:
    """Explore ``job_path``'s first URL source; write the result to the new file ``output``."""
    job = load_job(Path(job_path))
    if job.acquisition is None:
        raise ConfigurationError("Navigation exploration needs a URL source", location="sources")
    target = Path(output)
    if target.exists():
        raise ConfigurationError(f"{target} already exists; choose a new job file")
    with operator_trust(allow_remote=allow_remote):
        acquisition = job_web_acquirer(job)
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            result = runner.run(
                explore_job(
                    job,
                    model=model,
                    acquisition=acquisition,
                    expected_records=expected_records,
                    allow_remote=allow_remote,
                )
            )
    dataset = job.dataset
    text = render_job(
        list(job.fields),
        result.recipe,
        tuple(s for s in job.sources if isinstance(s, UrlJobSource)),
        policy=job.acquisition,
        dataset=dataset.path if dataset is not None else None,
        identity=tuple(dataset.identity) if dataset is not None else (),
    )
    write_validated_job(target, text)
    return result
