"""Generate and replay reproducible fixtures from successful extractions (MOCK-001, MOCK-006).

``build_fixture`` sanitizes the acquired body, records the expected extraction and
synthesizes schema-valid records, then proves the bundle clean and replayable
before returning it. ``replay_fixture`` runs a bundle through Rastro offline.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel, JsonValue

from rastro.domain.models import (
    ExtractionResult,
    Limits,
    Outcome,
    Recipe,
    Representation,
    Source,
    schema_fingerprint,
)
from rastro.mocklab.bundle import ExpectedExtraction, FixtureBundle, FixtureManifest
from rastro.mocklab.detectors import sensitive_segment
from rastro.mocklab.errors import FixtureMismatchError, MockLabError, SanitizationError
from rastro.mocklab.labels import MAX_SEED, SyntheticLabel
from rastro.mocklab.records import synthesize_records
from rastro.mocklab.retention import build_plan
from rastro.mocklab.sanitize import allowed_headers, assert_clean, sanitize_body
from rastro.ports.parser import DocumentParser

_SUCCESSFUL = frozenset({Outcome.SUCCESS, Outcome.PARTIAL_SUCCESS})


def build_fixture[T: BaseModel](
    body: str,
    *,
    source: Source,
    schema: type[T],
    recipe: Recipe,
    result: ExtractionResult[T],
    seed: int = 0,
    headers: Mapping[str, str] | None = None,
    synthetic_count: int = 3,
    limits: Limits | None = None,
) -> FixtureBundle:
    """Build a sanitized, labeled fixture bundle; equal inputs give byte-identical output.

    Raises ``MockLabError`` when the result is unsuccessful or belongs to another
    schema, ``SanitizationError`` when an artifact cannot be proven free of sensitive
    data, and ``FixtureMismatchError`` when the sanitized body no longer reproduces
    ``result``.
    """
    if result.status not in _SUCCESSFUL or not result.records:
        raise MockLabError("Fixtures require a successful extraction with records")
    fingerprint = schema_fingerprint(schema)
    if result.schema_fingerprint != fingerprint:
        raise MockLabError("The extraction result was produced for a different schema")
    if not 0 <= synthetic_count <= 1_000:
        raise MockLabError("synthetic_count must be between 0 and 1000")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
        raise MockLabError(f"seed must be an integer between 0 and {MAX_SEED}")
    clean_source = _query_free_source(source)
    label = SyntheticLabel(kind="fixture", seed=seed)
    budget = limits or Limits()
    plan = build_plan(body, recipe, _parser(recipe.representation), budget)
    expected = ExpectedExtraction.from_result(result)
    observed: list[dict[str, JsonValue]] = [record.data for record in expected.records]
    bundle = FixtureBundle(
        manifest=FixtureManifest(
            label=label,
            representation=recipe.representation,
            source=clean_source,
            recipe=recipe,
            schema_fingerprint=fingerprint,
            response_headers=allowed_headers(headers),
        ),
        body=sanitize_body(body, recipe.representation, plan, seed),
        expected=expected,
        records=synthesize_records(schema, observed, count=synthetic_count, seed=seed),
    )
    assert_clean(bundle.scan_view())
    verify_fixture(bundle, schema, limits=budget)
    return bundle


def replay_fixture[T: BaseModel](
    bundle: FixtureBundle, schema: type[T], *, limits: Limits | None = None
) -> ExtractionResult[T]:
    """Extract the labeled fixture body offline with the bundle's recipe and source."""
    from rastro.application.engine import Rastro

    manifest = bundle.manifest
    if schema_fingerprint(schema) != manifest.schema_fingerprint:
        raise MockLabError("The fixture was generated for a different schema")
    engine = Rastro(limits=limits)
    extract = engine.extract_html if manifest.representation == "html" else engine.extract_json
    return extract(
        bundle.labeled_body, source=manifest.source, schema=schema, recipe=manifest.recipe
    )


def verify_fixture[T: BaseModel](
    bundle: FixtureBundle, schema: type[T], *, limits: Limits | None = None
) -> ExtractionResult[T]:
    """Replay the bundle and raise ``FixtureMismatchError`` unless it matches expectations."""
    replayed = replay_fixture(bundle, schema, limits=limits)
    if ExpectedExtraction.from_result(replayed) != bundle.expected:
        raise FixtureMismatchError("Fixture replay does not reproduce the expected extraction")
    return replayed


def _query_free_source(source: Source) -> Source:
    """Drop query and fragment; refuse paths whose segments look like tokens or personal data.

    Evidence records this URL, so it cannot be rewritten without breaking replay.
    """
    path = urlsplit(source.evidence_url).path
    if any(sensitive_segment(segment) for segment in path.split("/") if segment):
        raise SanitizationError(
            "The source URL path looks like it contains a token or personal data"
        )
    return Source.model_validate({**source.model_dump(), "url": source.evidence_url})


def _parser(representation: Representation) -> DocumentParser:
    # Composition edge: adapters are wired here, as in the public facade.
    if representation == "json":
        from rastro.adapters.structured import JsonParser

        return JsonParser()
    from rastro.adapters.scrapling import ScraplingParser

    return ScraplingParser()
