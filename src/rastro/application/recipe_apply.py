"""Write a source's serving recipe from ``recipe_memory`` back into its job file (REC-006).

The rest of the file is kept: TOML files are edited in place (see ``recipe_toml``), JSON
files are rewritten with the same data. The result is reparsed and must equal the old
job except for that one recipe, or nothing is written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rastro.application.active_recipes import source_key
from rastro.application.atomic_files import write_atomic
from rastro.application.job_loader import load_job
from rastro.application.recipe_toml import add_source_recipe, recipe_table, replace_recipe
from rastro.application.repair_context import safe_path
from rastro.domain.jobs import MAX_JOB_BYTES, Job, parse_job
from rastro.domain.models import ConfigurationError, Recipe, ResourceLimitError


@dataclass(frozen=True)
class AppliedRecipe:
    path: Path
    source_index: int
    recipe_version: int
    changed: bool


def apply_job_recipe(path: str | Path, source: int, *, allow_absolute_paths: bool = False) -> Path:
    """Write the serving recipe of ``source`` (1-based) into the job file; return its path.

    Requires ``recipe_memory`` with a serving recipe for that source. The file is replaced
    atomically and symlinks are refused. A file that already holds that recipe is left
    untouched.
    """
    return write_serving_recipe(path, source, allow_absolute_paths=allow_absolute_paths).path


def write_serving_recipe(
    path: str | Path,
    source: int,
    *,
    allow_absolute_paths: bool = False,
    expected_digest: str | None = None,
) -> AppliedRecipe:
    """``apply_job_recipe`` with the applied version and whether the file changed.

    With ``expected_digest`` (sha256 of the file the caller saw), a file changed in the
    meantime raises ``FileChangedError`` at write time and is left as it is.
    """
    file = _no_symlinks(Path(path))
    job = load_job(file, allow_absolute_paths=allow_absolute_paths)
    if not 1 <= source <= len(job.sources):
        raise ConfigurationError("Source index is outside the job sources")
    recipe = _serving(job, file.parent, source, allow_absolute_paths)
    item = job.sources[source - 1]
    declared = item.recipe or job.recipe
    if declared is None:
        raise ConfigurationError("The job file has no recipe for this source to update")
    # A job recipe shared with other sources stays for them; this source gets its own.
    shared = item.recipe is None and sum(s.recipe is None for s in job.sources) > 1
    location = source - 1 if item.recipe is not None or shared else None
    if declared == recipe:
        return AppliedRecipe(file, source, recipe.version, changed=False)
    raw = _read(file)
    text = _rewrite(raw, file.suffix.lower(), recipe, location, add=shared)
    _verify(job, text, file.suffix.lower(), recipe, location)
    write_atomic(file, text.encode("utf-8"), expected_digest=expected_digest)
    return AppliedRecipe(file, source, recipe.version, changed=True)


def _no_symlinks(path: Path) -> Path:
    """Refuse a symlink in any component between the job root and the file.

    The root is the working directory when the file lies below it, otherwise the
    file's own directory (system links such as macOS ``/tmp`` above an explicitly
    named job are the caller's choice, not something a job can plant).
    """
    absolute = path.absolute()
    root = Path.cwd().absolute()
    if not absolute.is_relative_to(root):
        root = absolute.parent
    for part in (absolute, *absolute.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ConfigurationError("Recipe command paths must not contain symlinks")
    return absolute


def _serving(job: Job, base: Path, source: int, allow: bool) -> Recipe:
    if job.recipe_memory is None:
        raise ConfigurationError("Applying a recipe requires recipe_memory in the job")
    memory = safe_path(job.recipe_memory, base, allow)
    if not memory.is_file():
        raise ConfigurationError("recipe_memory does not exist yet; run the job first")
    from rastro.adapters.sqlite import SQLiteRecipeRepository

    stored = SQLiteRecipeRepository(memory).get(source_key(job, job.sources[source - 1]))
    if stored is None:
        raise ConfigurationError("recipe_memory has no serving recipe for this source")
    return stored.recipe


def _read(file: Path) -> str:
    with file.open("rb") as handle:
        raw = handle.read(MAX_JOB_BYTES + 1)
    if len(raw) > MAX_JOB_BYTES:
        raise ResourceLimitError("Job definition exceeds 256 KiB")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationError("Job file must be valid UTF-8") from None


def _rewrite(
    text: str, suffix: str, recipe: Recipe, location: int | None, *, add: bool = False
) -> str:
    if suffix == ".toml":
        if add and location is not None:
            return add_source_recipe(text, recipe, source=location)
        return replace_recipe(text, recipe, source=location)
    data = json.loads(text)
    holder = data if location is None else data["sources"][location]
    if not isinstance(holder, dict):
        raise ConfigurationError(
            "Write this source as an object with url or path before applying its recipe"
        )
    holder["recipe"] = recipe_table(recipe)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _verify(job: Job, text: str, suffix: str, recipe: Recipe, location: int | None) -> None:
    try:
        changed = parse_job(text, format="json" if suffix == ".json" else "toml")
    except (ValueError, ConfigurationError):
        raise ConfigurationError(
            "The rewritten job is invalid; update the recipe by hand"
        ) from None
    written = changed.recipe if location is None else changed.sources[location].recipe
    if written != recipe or _without(changed, location) != _without(job, location):
        raise ConfigurationError("The rewrite changed more than the recipe; update it by hand")


def _without(job: Job, location: int | None) -> dict[str, Any]:
    data: dict[str, Any] = job.model_dump(mode="json")
    holder = data if location is None else data["sources"][location]
    holder["recipe"] = None
    return data
