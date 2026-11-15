"""Export and import recipe bundles for Git sharing (HUB-001, HUB-002; spec section 09)."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from rastro.application.extraction import extract_document, validate_recipe
from rastro.domain.job_fields import JobField, build_job_schema
from rastro.domain.lifecycle import RecipeState
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
    SEMVER_PATTERN,
    SUPPORTED_BUNDLE_FORMATS,
    BundleAuthor,
    BundleCompatibility,
    BundleIntegrityError,
    BundleManifest,
    BundleVerificationError,
    BundleVersionError,
    canonical_json,
    default_source_pattern,
    sha256_hex,
)
from rastro.domain.selector_policy import check_untrusted_recipe
from rastro.ports.parser import DocumentParser
from rastro.ports.recipes import RecipeLifecycle, RecipeRepository

type ParserFor = Callable[[Representation], DocumentParser]
_FIELDS = TypeAdapter(tuple[JobField, ...])
GITATTRIBUTES = ".gitattributes"
_REUSABLE = frozenset({RecipeState.CANDIDATE, RecipeState.ACTIVE, RecipeState.DEGRADED})


def _min_rastro() -> str:
    match = re.match(r"^\d+\.\d+\.\d+", pkg_version("rastro-engine"))
    return match.group(0) if match and re.fullmatch(SEMVER_PATTERN, match.group(0)) else "0.7.0"


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
    bundle_id: str | None = None,
    version: str | None = None,
    author: BundleAuthor | None = None,
    license: str | None = None,
    public: bool = False,
    compatibility: BundleCompatibility | None = None,
) -> BundleManifest:
    """Write a deterministic bundle after proving the recipe on the supplied sample."""
    limits = limits or Limits()
    if "?" in source.url or "#" in source.url:
        raise ConfigurationError("Shared sources must not carry a query string or fragment")
    contents = _bundle_contents(recipe, source, fields, sample, parser, limits)
    if compatibility is None and any(v is not None for v in (bundle_id, version, author, license)):
        compatibility = BundleCompatibility(
            min_rastro=_min_rastro(), source_patterns=(default_source_pattern(source.url),)
        )
    try:
        manifest = BundleManifest(
            format_version=BUNDLE_FORMAT,
            id=bundle_id,
            version=version,
            author=author,
            license=license,
            visibility="public" if public else "private",
            compatibility=compatibility,
            source=source,
            context=context,
            representation=recipe.representation,
            last_verified=verified_on,
            files={name: sha256_hex(data) for name, data in contents.items()},
        )
    except ValidationError as error:
        raise ConfigurationError("Bundle package metadata is invalid") from error
    contents[MANIFEST_FILE] = canonical_json(manifest.model_dump(mode="json"))
    contents[GITATTRIBUTES] = b"* -text\n"
    write_all(directory, contents)
    return manifest


def _bundle_contents(
    recipe: Recipe,
    source: Source,
    fields: Sequence[JobField],
    sample: Path,
    parser: DocumentParser,
    limits: Limits,
) -> dict[str, bytes]:
    schema = build_job_schema(fields)
    recipe = validate_recipe(schema, recipe, recipe.representation)
    document = read_bounded(sample, limits.max_input_bytes(recipe.representation))
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
    fields_dump = _FIELDS.dump_python(tuple(fields), mode="json")
    return {
        RECIPE_FILE: canonical_json(recipe.model_dump(mode="json")),
        SCHEMA_FILE: canonical_json(
            {"fields": fields_dump, "json_schema": schema.model_json_schema()}
        ),
        EXPECTED_FILE: canonical_json([r.data.model_dump(mode="json") for r in result.records]),
        SAMPLE_FILES[recipe.representation]: document,
    }


@dataclass(frozen=True)
class ImportedRecipe:
    """The stored version an import produced, and whether it already existed."""

    stored: StoredRecipe
    reused: bool


def verify_bundle(
    directory: Path, parser_for: ParserFor, limits: Limits | None = None
) -> tuple[BundleManifest, dict[str, bytes], Recipe, type[BaseModel]]:
    """Read, validate, policy-check and offline-replay a recipe bundle."""
    limits = limits or Limits()
    manifest, files = _read_bundle(directory, limits)
    try:
        recipe = Recipe.model_validate(_load_json(files[RECIPE_FILE]))
        fields = _FIELDS.validate_python(_load_json(files[SCHEMA_FILE]).get("fields"))
        schema = build_job_schema(fields)
        recipe = validate_recipe(schema, recipe, manifest.representation)
    except (ValidationError, AttributeError) as error:
        raise BundleIntegrityError("Bundle recipe or schema is invalid") from error
    check_untrusted_recipe(recipe)
    _verify_replay(manifest, files, recipe, schema, parser_for(manifest.representation), limits)
    return manifest, files, recipe, schema


def import_bundle(
    directory: Path,
    repository: RecipeRepository,
    parser_for: ParserFor,
    *,
    at: datetime,
    limits: Limits | None = None,
) -> ImportedRecipe:
    """Verify a bundle offline, then store it as a candidate."""
    if not isinstance(repository, RecipeLifecycle):
        raise ConfigurationError("Importing recipes requires a lifecycle-aware repository")
    manifest, _, recipe, schema = verify_bundle(directory, parser_for, limits)
    key = RecipeKey.for_source(
        manifest.source, schema, manifest.context, representation=manifest.representation
    )
    for status in repository.history(key).versions:
        if status.state not in _REUSABLE:
            continue
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
    sample = _decode_sample(files[SAMPLE_FILES[manifest.representation]], bundle=True)
    result = extract_document(
        sample, source=manifest.source, schema=schema, recipe=recipe, parser=parser, limits=limits
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


def _read_bundle(directory: Path, limits: Limits) -> tuple[BundleManifest, dict[str, bytes]]:
    if directory.is_symlink() or not directory.is_dir():
        raise BundleIntegrityError("Bundle path must be a real directory")
    raw = _load_json(read_bounded(directory / MANIFEST_FILE, MAX_METADATA_BYTES, bundle=True))
    if not isinstance(raw, dict):
        raise BundleIntegrityError("Bundle manifest must be an object")
    if raw.get("format_version") not in SUPPORTED_BUNDLE_FORMATS:
        raise BundleVersionError("Unsupported bundle format; this release reads formats 1 and 2")
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
        data = read_bounded(directory / name, cap or MAX_METADATA_BYTES, bundle=True)
        if sha256_hex(data) != digest:
            raise BundleIntegrityError("Bundle file does not match its recorded digest")
        files[name] = data
    return manifest, files


def read_bounded(path: Path, cap: int, *, bundle: bool = False) -> bytes:
    error = BundleIntegrityError if bundle else ConfigurationError
    if path.is_symlink():
        raise error("File is missing, linked or unreadable")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise error("File must be a regular file")
            data = handle.read(cap + 1)
    except OSError as cause:
        raise error("File is missing, linked or unreadable") from cause
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


def write_all(directory: Path, contents: dict[str, bytes]) -> None:
    if directory.is_symlink():
        raise ConfigurationError("Bundle directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    for name, data in contents.items():
        try:
            with os.fdopen(os.open(directory / name, flags, 0o644), "wb") as handle:
                handle.write(data)
        except OSError as cause:
            raise ConfigurationError("Bundle file cannot be written") from cause
