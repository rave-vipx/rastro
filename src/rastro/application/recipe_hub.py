"""Export and import recipe bundles for Git sharing (HUB-001; spec section 09).

Bundles are untrusted input: every file is size-bounded, hashed, and the recipe
must reproduce the bundled records offline before it enters the store.
"""

import json
import os
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from rastro.application.extraction import extract_document, validate_recipe
from rastro.domain.job_fields import JobField, build_job_schema
from rastro.domain.memory import RecipeContext, RecipeKey, StoredRecipe
from rastro.domain.models import ConfigurationError, Limits, Outcome, Recipe, Representation, Source
from rastro.domain.recipe_bundle import (
    BUNDLE_FORMAT,
    EXPECTED_FILE,
    MANIFEST_FILE,
    MAX_METADATA_BYTES,
    RECIPE_FILE,
    SAMPLE_FILES,
    SCHEMA_FILE,
    BundleIntegrityError,
    BundleManifest,
    BundleVerificationError,
    BundleVersionError,
    canonical_json,
    sha256_hex,
)
from rastro.ports.parser import DocumentParser
from rastro.ports.recipes import RecipeLifecycle, RecipeRepository

type ParserFor = Callable[[Representation], DocumentParser]
_FIELDS = TypeAdapter(tuple[JobField, ...])


def export_bundle(
    directory: Path,
    *,
    recipe: Recipe,
    source: Source,
    context: RecipeContext,
    fields: Sequence[JobField],
    sample: Path,
    parser: DocumentParser,
    verified_on: date,
    limits: Limits | None = None,
) -> BundleManifest:
    """Write a deterministic bundle after proving the recipe on the supplied sample."""
    limits = limits or Limits()
    if "?" in source.url or "#" in source.url:
        raise ConfigurationError("Shared sources must not carry a query string or fragment")
    schema = build_job_schema(fields)
    recipe = validate_recipe(schema, recipe, recipe.representation)
    document = _read_bounded(sample, limits.max_input_bytes(recipe.representation))
    result = extract_document(
        _decode_sample(document, bundle=False),
        source=source,
        schema=schema,
        recipe=recipe,
        parser=parser,
        limits=limits,
    )
    if result.status != Outcome.SUCCESS or not result.records or result.metrics.rejected:
        raise ConfigurationError("The recipe must extract the sample cleanly before export")
    sample_name = SAMPLE_FILES[recipe.representation]
    contents = {
        RECIPE_FILE: canonical_json(recipe.model_dump(mode="json")),
        SCHEMA_FILE: canonical_json(
            {
                "fields": _FIELDS.dump_python(tuple(fields), mode="json"),
                "json_schema": schema.model_json_schema(),
            }
        ),
        EXPECTED_FILE: canonical_json([r.data.model_dump(mode="json") for r in result.records]),
        sample_name: document,
    }
    manifest = BundleManifest(
        source=source,
        context=context,
        representation=recipe.representation,
        last_verified=verified_on,
        files={name: sha256_hex(data) for name, data in contents.items()},
    )
    contents[MANIFEST_FILE] = canonical_json(manifest.model_dump(mode="json"))
    # Hashes cover exact bytes: keep Git (core.autocrlf on Windows) from rewriting them.
    contents[GITATTRIBUTES] = b"* -text\n"
    _write_all(directory, contents)
    return manifest


@dataclass(frozen=True)
class ImportedRecipe:
    """The stored version an import produced, and whether it already existed."""

    stored: StoredRecipe
    reused: bool


def import_bundle(
    directory: Path,
    repository: RecipeRepository,
    parser_for: ParserFor,
    *,
    at: datetime,
    limits: Limits | None = None,
) -> ImportedRecipe:
    """Verify a bundle offline, then store it as a candidate.

    A definition equal to a version already stored for the same key is reused, not
    duplicated, whatever that version's lifecycle state.
    """
    if not isinstance(repository, RecipeLifecycle):
        raise ConfigurationError("Importing recipes requires a lifecycle-aware repository")
    limits = limits or Limits()
    manifest, files = _read_bundle(directory, limits)
    try:
        recipe = Recipe.model_validate(_load_json(files[RECIPE_FILE]))
        fields = _FIELDS.validate_python(_load_json(files[SCHEMA_FILE]).get("fields"))
        schema = build_job_schema(fields)
        recipe = validate_recipe(schema, recipe, manifest.representation)
    except (ValidationError, AttributeError) as error:
        raise BundleIntegrityError("Bundle recipe or schema is invalid") from error
    _verify_replay(manifest, files, recipe, schema, parser_for(manifest.representation), limits)
    key = RecipeKey.for_source(
        manifest.source, schema, manifest.context, representation=manifest.representation
    )
    for status in repository.history(key).versions:
        existing = repository.get(key, version=status.version)
        if existing is not None and existing.recipe == _with_version(recipe, status.version):
            return ImportedRecipe(existing, reused=True)
    version = repository.next_version(key)
    stored = repository.save_candidate(key, _with_version(recipe, version), selected_index=0, at=at)
    return ImportedRecipe(stored, reused=False)


def _verify_replay(
    manifest: BundleManifest,
    files: dict[str, bytes],
    recipe: Recipe,
    schema: type[BaseModel],
    parser: DocumentParser,
    limits: Limits,
) -> None:
    sample = files[SAMPLE_FILES[manifest.representation]]
    result = extract_document(
        _decode_sample(sample, bundle=True),
        source=manifest.source,
        schema=schema,
        recipe=recipe,
        parser=parser,
        limits=limits,
    )
    actual = json.loads(json.dumps([r.data.model_dump(mode="json") for r in result.records]))
    if (
        result.status != Outcome.SUCCESS
        or result.metrics.rejected
        or actual != _load_json(files[EXPECTED_FILE])
    ):
        raise BundleVerificationError("Recipe output differs from the bundled expected records")


def _with_version(recipe: Recipe, version: int) -> Recipe:
    payload = recipe.model_dump()
    payload["version"] = version
    payload["fallbacks"] = [{**item, "version": version} for item in payload["fallbacks"]]
    return Recipe.model_validate(payload)


GITATTRIBUTES = ".gitattributes"


def _read_bundle(directory: Path, limits: Limits) -> tuple[BundleManifest, dict[str, bytes]]:
    if directory.is_symlink() or not directory.is_dir():
        raise BundleIntegrityError("Bundle path must be a real directory")
    raw = _load_json(_read_bounded(directory / MANIFEST_FILE, MAX_METADATA_BYTES, bundle=True))
    if not isinstance(raw, dict):
        raise BundleIntegrityError("Bundle manifest must be an object")
    if raw.get("format_version") != BUNDLE_FORMAT:
        raise BundleVersionError(f"Unsupported bundle format; this release reads {BUNDLE_FORMAT}")
    try:
        manifest = BundleManifest.model_validate(raw)
    except ValidationError as error:
        raise BundleIntegrityError("Bundle manifest is invalid") from error
    if set(manifest.files) != manifest.expected_names():
        raise BundleIntegrityError("Bundle lists unexpected files")
    sample_name = SAMPLE_FILES[manifest.representation]
    files: dict[str, bytes] = {}
    for name, digest in manifest.files.items():
        cap = limits.max_input_bytes(manifest.representation) if name == sample_name else None
        data = _read_bounded(directory / name, cap or MAX_METADATA_BYTES, bundle=True)
        if sha256_hex(data) != digest:
            raise BundleIntegrityError("Bundle file does not match its recorded digest")
        files[name] = data
    return manifest, files


def _read_bounded(path: Path, cap: int, *, bundle: bool = False) -> bytes:
    error = BundleIntegrityError if bundle else ConfigurationError
    if path.is_symlink():  # O_NOFOLLOW does not exist on Windows
        raise error("File is missing, linked or unreadable")
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError as cause:
        raise error("File is missing, linked or unreadable") from cause
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise error("File must be a regular file")
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise error("File exceeds its size limit")
    return data


def _decode_sample(data: bytes, *, bundle: bool) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise (BundleIntegrityError if bundle else ConfigurationError)(
            "The sample file is not valid UTF-8"
        ) from error


def _load_json(data: bytes) -> Any:
    try:
        return json.loads(data)
    except (ValueError, RecursionError) as error:
        raise BundleIntegrityError("Bundle file is not valid JSON") from error


def _write_all(directory: Path, contents: dict[str, bytes]) -> None:
    if directory.is_symlink():
        raise ConfigurationError("Bundle directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    for name, data in contents.items():
        try:
            descriptor = os.open(directory / name, flags, 0o644)
        except OSError as cause:
            raise ConfigurationError(
                "Bundle file cannot be written (symlink or permissions)"
            ) from cause
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
