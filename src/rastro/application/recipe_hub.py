"""Export and import recipe bundles for Git sharing (spec section 09).

Bundles are untrusted input: every file is size-bounded, hashed, and the recipe
must reproduce the bundled records offline before it enters the store.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError

from rastro.application.bundle_files import (
    decode_sample,
    load_json,
    read_bounded,
    read_bundle,
    write_all,
)
from rastro.application.capability_gate import model_capabilities, require
from rastro.application.engine_version import running_version
from rastro.application.extraction import extract_document, validate_recipe
from rastro.application.operator_trust import entered_from
from rastro.domain.job_fields import JobField, build_job_schema
from rastro.domain.lifecycle import RecipeState
from rastro.domain.memory import RecipeContext, RecipeKey, StoredRecipe
from rastro.domain.models import ConfigurationError, Limits, Outcome, Recipe, Representation, Source
from rastro.domain.recipe_bundle import (
    BUNDLE_FORMAT,
    EXPECTED_FILE,
    MANIFEST_FILE,
    RECIPE_FILE,
    SAMPLE_FILES,
    SCHEMA_FILE,
    BundleAuthor,
    BundleCompatibility,
    BundleIntegrityError,
    BundleManifest,
    BundleVerificationError,
    canonical_json,
    default_source_pattern,
    sha256_hex,
)
from rastro.domain.selector_policy import check_untrusted_recipe
from rastro.ports.parser import DocumentParser
from rastro.ports.recipes import RecipeLifecycle, RecipeRepository

type ParserFor = Callable[[Representation], DocumentParser]
_FIELDS = TypeAdapter(tuple[JobField, ...])
# Every bundle digest covers exact bytes, so each bundle carries a `* -text` rule that stops Git
# (core.autocrlf on Windows) from rewriting line endings after a clone.
GITATTRIBUTES = ".gitattributes"
_REUSABLE = frozenset({RecipeState.CANDIDATE, RecipeState.ACTIVE, RecipeState.DEGRADED})


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
            min_rastro=running_version(), source_patterns=(default_source_pattern(source.url),)
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
    # Hashes cover exact bytes: keep Git (core.autocrlf on Windows) from rewriting them.
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
        decode_sample(document, bundle=False),
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
    manifest, files = read_bundle(directory, limits)
    try:
        recipe = Recipe.model_validate(load_json(files[RECIPE_FILE]))
        fields = _FIELDS.validate_python(load_json(files[SCHEMA_FILE]).get("fields"))
        schema = build_job_schema(fields)
        recipe = validate_recipe(schema, recipe, manifest.representation)
    except (ValidationError, AttributeError) as error:
        raise BundleIntegrityError("Bundle recipe or schema is invalid") from error
    # A bundle proves integrity, not authenticity: refuse costly selectors before replay.
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
    """Verify a bundle offline, then store it as a candidate.

    A definition equal to a stored candidate or serving version of the same key is
    reused, not duplicated. An equal retired version is not reused: the import stores
    a new candidate version so it can be reviewed and promoted again.
    """
    with entered_from("bundle"):  # shared input: it cannot switch capabilities on (ADR 0037)
        if not isinstance(repository, RecipeLifecycle):
            raise ConfigurationError("Importing recipes requires a lifecycle-aware repository")
        manifest, _, recipe, schema = verify_bundle(directory, parser_for, limits)
        require(model_capabilities(recipe))
    key = RecipeKey.for_source(
        manifest.source, schema, manifest.context, representation=manifest.representation
    )
    for status in repository.history(key).versions:
        if status.state not in _REUSABLE:
            continue  # a retired equal definition must not block a fresh candidate
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
    sample = decode_sample(files[SAMPLE_FILES[manifest.representation]], bundle=True)
    result = extract_document(
        sample, source=manifest.source, schema=schema, recipe=recipe, parser=parser, limits=limits
    )
    actual = json.loads(json.dumps([r.data.model_dump(mode="json") for r in result.records]))
    if (
        result.status != Outcome.SUCCESS
        or result.metrics.rejected
        or actual != load_json(files[EXPECTED_FILE])
    ):
        raise BundleVerificationError("Recipe output differs from the bundled expected records")


def _with_version(recipe: Recipe, version: int) -> Recipe:
    payload = recipe.model_dump()
    payload["version"] = version
    payload["fallbacks"] = [{**item, "version": version} for item in payload["fallbacks"]]
    return Recipe.model_validate(payload)
