"""Open a job's dataset: its contained location and its configured backend."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.egress_trust import allowed_env_names
from rastro.application.job_loader import resolve_job_path
from rastro.application.operator_trust import current_trust
from rastro.domain.jobs import Job
from rastro.domain.models import ConfigurationError
from rastro.domain.secret_env import check_secret_env_name
from rastro.ports.dataset import DatasetStore

__all__ = ["dataset_location", "open_dataset"]


def dataset_location(file: Path, job: Job, *, allow_absolute_paths: bool) -> Path:
    """The job's dataset path, contained like every job path; symlinks are left for the adapter.

    Raises ``ConfigurationError`` without a ``[dataset]`` block or a dataset folder.
    """
    spec = job.dataset
    if spec is None:
        raise ConfigurationError("This command requires a [dataset] block in the job file")
    resolved = resolve_job_path(
        spec.path, base_dir=file.parent, allow_absolute_paths=allow_absolute_paths
    )
    if resolved == file.resolve():
        raise ConfigurationError("The dataset must be separate from the job file")
    if not resolved.parent.is_dir():
        raise ConfigurationError(
            f"Dataset folder does not exist: {spec.path} (paths are relative to the job file)",
            location="dataset.path",
        )
    # Preserve symlink components for the adapter's refusal, after containment validation.
    return Path(spec.path) if Path(spec.path).is_absolute() else file.parent.resolve() / spec.path


@contextmanager
def open_dataset(
    job: Job, local_path: Path, *, allow_remote: bool = False, allow_env: Iterable[str] = ()
) -> Iterator[DatasetStore]:
    """Open the configured dataset; operational state always uses local_path."""
    spec = job.dataset
    if spec is None:
        raise ConfigurationError("This command requires a [dataset] block")
    tolerance = timedelta(minutes=spec.match_tolerance_minutes or 30)
    if spec.backend == "sqlite":
        yield SQLiteDatasetStore(
            local_path,
            limits=job.limits,
            near_match_fields=spec.match_fields,
            near_match_tolerance=tolerance,
        )
        return
    from rastro.adapters.postgres_dataset import PostgresDatasetStore

    assert spec.dsn_env is not None
    name = check_secret_env_name(spec.dsn_env, allowed_env_names(allow_env))
    dsn = os.environ.get(name)
    if not dsn:
        raise ConfigurationError("PostgreSQL connection environment variable is missing or empty")
    store = PostgresDatasetStore(
        dsn,
        schema=spec.db_schema,
        allow_remote=allow_remote or current_trust().allow_remote,
        limits=job.limits,
        near_match_fields=spec.match_fields,
        near_match_tolerance=tolerance,
    )
    try:
        yield store
    finally:
        store.close()
