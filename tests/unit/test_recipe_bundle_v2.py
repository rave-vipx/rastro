"""HUB-002: Bundle format 2 metadata (id, version, author, license, visibility, compatibility)."""

from datetime import date
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from rastro.domain.memory import RecipeContext
from rastro.domain.models import Source
from rastro.domain.recipe_bundle import (
    BUNDLE_FORMAT,
    BundleAuthor,
    BundleCompatibility,
    BundleManifest,
    default_source_pattern,
)


def _files() -> dict[str, str]:
    return {
        "recipe.json": "0" * 64,
        "schema.json": "1" * 64,
        "expected.json": "2" * 64,
        "sample.html": "3" * 64,
    }


def test_bundle_format_default_is_2() -> None:
    """HUB-002: new bundles default to format version 2 and private visibility."""
    assert BUNDLE_FORMAT == 2
    manifest = BundleManifest(
        source=Source(url="https://sodre.gub.uy/espectaculos/"),
        context=RecipeContext(),
        representation="html",
        last_verified=date(2026, 11, 15),
        files=_files(),
    )
    assert manifest.format_version == 2
    assert manifest.visibility == "private"
    assert manifest.id is None
    assert manifest.version is None
    assert manifest.author is None
    assert manifest.license is None
    assert manifest.compatibility is None


def test_format_1_manifest_validates_unchanged() -> None:
    """HUB-002: format 1 manifests validate with no format-2 fields."""
    raw = {
        "format_version": 1,
        "source": {"url": "https://sodre.gub.uy/espectaculos/"},
        "context": {},
        "representation": "html",
        "last_verified": "2026-01-01",
        "files": _files(),
    }
    manifest = BundleManifest.model_validate(raw)
    assert manifest.format_version == 1
    assert manifest.expected_names() == frozenset(
        {"recipe.json", "schema.json", "expected.json", "sample.html"}
    )


def test_format_1_rejects_format_2_metadata() -> None:
    """HUB-002: format 1 manifests cannot carry format 2 fields or public visibility."""
    with pytest.raises(ValidationError):
        BundleManifest(
            format_version=1,
            source=Source(url="https://sodre.gub.uy/espectaculos/"),
            context=RecipeContext(),
            representation="html",
            last_verified=date(2026, 1, 1),
            files=_files(),
            id="sodre-agenda",
        )


def test_format_2_full_metadata_round_trip() -> None:
    """HUB-002: format 2 carries id, version, author, license, visibility and compatibility."""
    manifest = BundleManifest(
        format_version=2,
        id="uy-sodre-agenda",
        version="1.2.3",
        author=BundleAuthor(name="Rastro Contributors", url="https://example.org/team"),
        license="Apache-2.0",
        visibility="public",
        compatibility=BundleCompatibility(
            min_rastro="0.7.0",
            source_patterns=("sodre.gub.uy/espectaculos", "tickantel.com.uy/ng"),
        ),
        source=Source(url="https://sodre.gub.uy/espectaculos/"),
        context=RecipeContext(),
        representation="html",
        last_verified=date(2026, 11, 15),
        files=_files(),
    )
    dumped = manifest.model_dump(mode="json")
    restored = BundleManifest.model_validate(dumped)
    assert restored == manifest
    assert restored.author == BundleAuthor(
        name="Rastro Contributors", url="https://example.org/team"
    )


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "a",
        "-bad",
        "Bad_Case",
        "with space",
        "../traversal",
        "a/b",
        "a" * 65,
    ],
)
def test_invalid_bundle_id_is_rejected(bad_id: str) -> None:
    """HUB-002: recipe id must match ^[a-z0-9][a-z0-9-]{1,63}$."""
    with pytest.raises(ValidationError):
        BundleManifest(
            source=Source(url="https://sodre.gub.uy/espectaculos/"),
            context=RecipeContext(),
            representation="html",
            last_verified=date(2026, 11, 15),
            files=_files(),
            id=bad_id,
        )


@pytest.mark.parametrize(
    "bad_version",
    ["", "1", "1.0", "v1.0.0", "01.0.0", "1.0.0-alpha", "../1.0.0", "1.0.0+build"],
)
def test_invalid_semver_is_rejected(bad_version: str) -> None:
    """HUB-002: version must be strict MAJOR.MINOR.PATCH semver."""
    with pytest.raises(ValidationError):
        BundleManifest(
            source=Source(url="https://sodre.gub.uy/espectaculos/"),
            context=RecipeContext(),
            representation="html",
            last_verified=date(2026, 11, 15),
            files=_files(),
            version=bad_version,
        )


@pytest.mark.parametrize(
    "bad_pattern",
    [
        "",
        "https://sodre.gub.uy/espectaculos",
        "sodre.gub.uy?token=1",
        "sodre.gub.uy#frag",
        "user@sodre.gub.uy/path",
        "/leading-slash",
    ],
)
def test_invalid_source_pattern_is_rejected(bad_pattern: str) -> None:
    """HUB-002: source_patterns must be host or host/path-prefix without scheme or query."""
    with pytest.raises(ValidationError):
        BundleCompatibility(min_rastro="0.7.0", source_patterns=(bad_pattern,))


def test_author_rejects_credentials_in_url_and_control_chars_in_name() -> None:
    """HUB-002: author name rejects control characters and url rejects credentials."""
    with pytest.raises(ValidationError):
        BundleAuthor(name="Bad\x1b[31mName")
    with pytest.raises(ValidationError):
        BundleAuthor(name="Alice", url="https://user:secret@example.com/")


def test_default_source_pattern_derives_host_and_path() -> None:
    """HUB-002: default_source_pattern strips scheme and trailing slash."""
    url = "https://sodre.gub.uy/espectaculos/"
    assert default_source_pattern(url) == "sodre.gub.uy/espectaculos"
    assert default_source_pattern("https://sodre.gub.uy/") == "sodre.gub.uy"
    parsed = urlsplit(url)
    assert parsed.hostname in default_source_pattern(url)
