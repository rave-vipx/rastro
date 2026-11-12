"""Filesystem loading, path containment, and SDK wiring for declarative jobs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

import rastro
from rastro.domain.job_providers import JobModelProvider, OllamaProvider, OpenAICompatibleProvider
from rastro.domain.jobs import (
    MAX_JOB_BYTES,
    FileJobSource,
    Job,
    UrlJobSource,
    parse_job,
    validate_job_path_syntax,
)
from rastro.domain.models import (
    ConfigurationError,
    Limits,
    Representation,
    ResourceLimitError,
    schema_fingerprint,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

    from rastro.application.engine import Rastro
    from rastro.ports.acquisition import AcquisitionPort
    from rastro.ports.model import ModelPort


@dataclass(frozen=True)
class PreparedJob:
    schema: type[BaseModel]
    fingerprint: str
    client: Rastro
    bodies: dict[int, str]


def resolve_job_path(
    raw: str, *, base_dir: Path | None, allow_absolute_paths: bool = False
) -> Path:
    """Resolve a job path, enforcing containment inside ``base_dir`` unless explicitly allowed."""
    validate_job_path_syntax(raw)
    is_abs = (
        Path(raw).is_absolute()
        or PurePosixPath(raw).is_absolute()
        or PureWindowsPath(raw).is_absolute()
    )
    if is_abs:
        if not allow_absolute_paths:
            raise ConfigurationError("Absolute paths in jobs require --allow-absolute-paths")
        return Path(raw).resolve()
    if base_dir is None:
        raise ConfigurationError("Relative job paths require a base directory")
    root = base_dir.resolve()
    resolved = (root / raw).resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ConfigurationError("Job paths must stay within the job file directory")
    return resolved


def _same_file(left: Path, right: Path) -> bool:
    if left == right:
        return True
    return left.exists() and right.exists() and left.samefile(right)


def validate_job_paths(
    job: Job, *, base_dir: Path | None, allow_absolute_paths: bool = False
) -> tuple[dict[int, Path], Path | None]:
    memory_path = (
        resolve_job_path(
            job.recipe_memory, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
        )
        if job.recipe_memory is not None
        else None
    )
    if job.dataset is not None:
        dataset_path = resolve_job_path(
            job.dataset.path, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
        )
        if memory_path is not None and _same_file(dataset_path, memory_path):
            raise ConfigurationError("The dataset must be a separate file from recipe_memory")
    if job.repair is not None:
        for fixture in job.repair.fixtures:
            resolve_job_path(fixture, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths)
    file_paths = {
        index: resolve_job_path(
            source.path, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
        )
        for index, source in enumerate(job.sources)
        if isinstance(source, FileJobSource)
    }
    return file_paths, memory_path


def recipe_memory_path(
    job: Job, *, base_dir: Path | None, allow_absolute_paths: bool = False
) -> Path | None:
    """The contained ``recipe_memory`` path; refuses symlinks, like the dataset store."""
    if job.recipe_memory is None:
        return None
    resolved = resolve_job_path(
        job.recipe_memory, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
    )
    raw = Path(job.recipe_memory)
    if not raw.is_absolute():
        assert base_dir is not None  # resolve_job_path requires it for relative paths
        raw = base_dir.resolve() / raw
    if any(part.is_symlink() for part in (raw, *raw.parents)):
        raise ConfigurationError("recipe_memory paths must not contain symlinks")
    return resolved


def load_job(path: str | Path, *, allow_absolute_paths: bool = False) -> Job:
    """Load a `.toml` or `.json` job file and verify path containment against its directory."""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix not in {".toml", ".json"}:
        raise ConfigurationError("Job files must use a .toml or .json extension")
    if not file_path.is_file():
        raise ConfigurationError("Job file does not exist")
    with file_path.open("rb") as handle:
        raw = handle.read(MAX_JOB_BYTES + 1)
    job = parse_job(raw, format="toml" if suffix == ".toml" else "json")
    validate_job_paths(job, base_dir=file_path.parent, allow_absolute_paths=allow_absolute_paths)
    return job


def _read_local_source(path: Path, max_bytes: int, representation: Representation) -> str:
    if not path.is_file():
        raise ConfigurationError("Local source file does not exist")
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ResourceLimitError(
            f"{representation.upper()} input exceeds max_{representation}_bytes"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationError("Local source file must be valid UTF-8") from None


def _build_model_provider(spec: JobModelProvider) -> ModelPort:
    if spec.base_url_env is not None:
        base_url = os.environ.get(spec.base_url_env, "").strip()
        if not base_url:
            raise ConfigurationError(f"Set {spec.base_url_env} to a valid model endpoint URL")
    else:
        base_url = spec.base_url
    try:
        if isinstance(spec, OllamaProvider):
            ollama_cls: Any = rastro.OllamaModel
            return ollama_cls(spec.model, base_url=base_url)  # type: ignore[no-any-return]
        assert isinstance(spec, OpenAICompatibleProvider)
        openai_cls: Any = rastro.OpenAICompatibleModel
        return openai_cls(  # type: ignore[no-any-return]
            spec.model,
            base_url=base_url,
            api_key_env=spec.api_key_env,
            allow_remote=spec.allow_remote,
            response_format=spec.response_format,
            token_limit=spec.token_limit,
            temperature=spec.temperature,
        )
    except ImportError as error:
        raise ConfigurationError(str(error)) from None


def prepare_job(
    job: Job,
    *,
    base_dir: Path | None,
    allow_absolute_paths: bool,
    acquisition: AcquisitionPort | None,
    model: ModelPort | None,
) -> PreparedJob:
    file_paths, memory_path = validate_job_paths(
        job, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
    )
    limits = job.limits or Limits()
    bodies = {
        idx: _read_local_source(
            path,
            limits.max_input_bytes(job.sources[idx].representation),
            job.sources[idx].representation,
        )
        for idx, path in file_paths.items()
    }
    schema = job.build_schema()
    active_model = model or (_build_model_provider(job.model) if job.model is not None else None)
    repository = rastro.SQLiteRecipeRepository(memory_path) if memory_path is not None else None
    aliases = job.discovery_aliases or None
    if any(isinstance(item, UrlJobSource) for item in job.sources):
        assert job.acquisition is not None
        client = (
            rastro.Rastro(
                acquisition=acquisition,
                policy=job.acquisition,
                limits=limits,
                repository=repository,
                model=active_model,
                inference_policy=job.inference_policy,
                discover_recipes=job.discover_recipes,
                discovery_aliases=aliases,
            )
            if acquisition is not None
            else rastro.Rastro.web(
                job.acquisition,
                limits=limits,
                repository=repository,
                model=active_model,
                inference_policy=job.inference_policy,
                discover_recipes=job.discover_recipes,
                discovery_aliases=aliases,
            )
        )
    else:
        client = rastro.Rastro(
            limits=limits,
            repository=repository,
            model=active_model,
            inference_policy=job.inference_policy,
            discover_recipes=job.discover_recipes,
            discovery_aliases=aliases,
        )
    return PreparedJob(
        schema=schema, fingerprint=schema_fingerprint(schema), client=client, bodies=bodies
    )
