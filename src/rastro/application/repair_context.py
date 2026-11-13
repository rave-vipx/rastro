"""Bounded file/network inputs and adapter wiring for recipe commands."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_relocation import SQLiteRelocationStore
from rastro.application.consistency import History, history_from_store
from rastro.application.egress_trust import check_job_egress
from rastro.application.job_loader import load_job, resolve_job_path
from rastro.domain.jobs import FileJobSource, Job
from rastro.domain.memory import RecipeKey
from rastro.domain.models import ConfigurationError, Limits, Recipe, Source
from rastro.domain.repair import RelocationSnapshot
from rastro.mocklab.bundle import FixtureBundle, load_fixture


def safe_path(raw: str, base: Path, allow: bool) -> Path:
    base = base.resolve()
    # Check the un-resolved path so containment resolution cannot hide symlinks.
    path = Path(raw) if Path(raw).is_absolute() else base / raw
    for candidate in (path, *path.parents):
        if candidate == base:
            break
        if candidate.is_symlink():
            raise ConfigurationError("Recipe command paths must not contain symlinks")
    return resolve_job_path(raw, base_dir=base, allow_absolute_paths=allow)


@dataclass(frozen=True)
class RepairContext:
    job: Job
    schema: type[BaseModel]
    repository: SQLiteRecipeRepository
    dataset_path: Path
    base: Path
    allow: bool
    allow_egress: bool = False
    allow_env: tuple[str, ...] = ()

    @classmethod
    def load(
        cls, path: Path, allow: bool, *, allow_egress: bool = False, allow_env: Iterable[str] = ()
    ) -> "RepairContext":
        if path.is_symlink():
            raise ConfigurationError("Recipe command paths must not contain symlinks")
        base = path.parent.resolve()
        path = base / path.name
        job = load_job(path, allow_absolute_paths=allow)
        if job.dataset is None or job.recipe_memory is None:
            raise ConfigurationError("Recipe commands require dataset and recipe_memory")
        memory = safe_path(job.recipe_memory, base, allow)
        dataset = safe_path(job.dataset.path, base, allow)
        return cls(
            job,
            job.build_schema(),
            SQLiteRecipeRepository(memory),
            dataset,
            base,
            allow,
            allow_egress,
            tuple(allow_env),
        )

    def source(self, index: int) -> tuple[Source, RecipeKey, Recipe]:
        source, key = self.locate(index)
        stored = self.repository.get(key)
        if stored is None:
            raise ConfigurationError("Load a successful recipe before repairing this source")
        return source, key, stored.validated_recipe

    def locate(self, index: int) -> tuple[Source, RecipeKey]:
        """The configured source and its recipe key; reads no recipe."""
        if not 1 <= index <= len(self.job.sources):
            raise ConfigurationError("Source index is outside the job sources")
        item = self.job.sources[index - 1]
        url = item.resolved_source_url() if isinstance(item, FileJobSource) else item.url
        source = (item.region or self.job.region).to_source(url)
        key = RecipeKey.for_source(
            source, self.schema, self.job.context, representation=item.representation
        )
        return source, key

    def fixtures(
        self, source: Source, recipe: Recipe
    ) -> tuple[RelocationSnapshot | None, tuple[FixtureBundle, ...]]:
        assert self.job.dataset is not None
        retained = SQLiteRelocationStore(self.dataset_path, limits=self.job.limits).latest(
            self.job.dataset.name, source, self.source_fingerprint, recipe
        )
        snapshot, fixtures = (retained[0], [retained[1]]) if retained else (None, [])
        sources = []
        for item in self.job.sources:
            url = item.resolved_source_url() if isinstance(item, FileJobSource) else item.url
            configured = (item.region or self.job.region).to_source(url)
            sources.append(_evidence_source(configured))
        for raw in self.job.repair.fixtures if self.job.repair else ():
            path = safe_path(raw, self.base, self.allow)
            if any(p.is_symlink() for p in path.iterdir()):
                raise ConfigurationError("Fixture files must not be symlinks")
            bundle = load_fixture(path)
            if sources.count(bundle.manifest.source) != 1:
                raise ConfigurationError("Each repair fixture must match exactly one job source")
            if bundle.manifest.source == _evidence_source(source):
                fixtures.append(bundle)
        return snapshot, tuple(fixtures)

    @property
    def source_fingerprint(self) -> str:
        from rastro.domain.models import schema_fingerprint

        return schema_fingerprint(self.schema)

    def history(self) -> History:
        assert self.job.dataset is not None
        if not self.dataset_path.exists():
            return {}
        return history_from_store(
            SQLiteDatasetStore(self.dataset_path, limits=self.job.limits),
            self.job.dataset.name,
            tuple(self.schema.model_fields),
        )

    async def document(self, index: int, source: Source) -> str:
        item = self.job.sources[index - 1]
        limits = self.job.limits or Limits()
        if isinstance(item, FileJobSource):
            path = safe_path(item.path, self.base, self.allow)
            from rastro.application.job_loader import _read_local_source

            return _read_local_source(
                path, limits.max_input_bytes(item.representation), item.representation
            )
        from rastro.adapters.dns import SystemResolver
        from rastro.adapters.http import CurlTransport
        from rastro.application.acquisition import WebAcquirer

        assert self.job.acquisition is not None
        check_job_egress(self.job, allow_egress=self.allow_egress, allow_env=self.allow_env)
        result = await WebAcquirer(
            self.job.acquisition, transport=CurlTransport(), resolver=SystemResolver()
        ).acquire(
            source,
            max_bytes=limits.max_input_bytes(item.representation),
            representation=item.representation,
        )
        if result.body is None:
            raise ConfigurationError(
                "Current evidence unavailable: " + (result.issue_code or "acquisition_failed")
            )
        return result.body


def _evidence_source(source: Source) -> Source:
    return Source.model_validate({**source.model_dump(), "url": source.evidence_url})
