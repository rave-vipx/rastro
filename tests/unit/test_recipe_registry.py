"""HUB-002, HUB-003, HUB-004, HUB-005, HUB-006: Git directory recipe registry and trust."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.structured import JsonParser
from rastro.application.recipe_hub import export_bundle
from rastro.application.recipe_registry import (
    install_from_registry,
    publish_to_registry,
    search_registry,
)
from rastro.domain.job_fields import StringField
from rastro.domain.memory import RecipeContext
from rastro.domain.models import ConfigurationError, CssRecipe, Representation, Source
from rastro.domain.recipe_bundle import (
    MANIFEST_FILE,
    BundleAuthor,
    BundleCompatibility,
    BundleIntegrityError,
    BundleVerificationError,
    canonical_json,
    sha256_hex,
)
from rastro.domain.registry import RegistryError, TrustIndicators, compute_trust, parse_package_spec
from rastro.ports.parser import DocumentParser


def _parser_for(representation: Representation) -> DocumentParser:
    return JsonParser() if representation == "json" else ScraplingParser()


_DEFAULT_AUTHOR = BundleAuthor(name="Rastro Team")


def _write_sample_bundle(
    directory: Path,
    tmp_path: Path,
    *,
    bundle_id: str | None = "teatro-central-agenda",
    version: str | None = "1.0.0",
    author: BundleAuthor | None = _DEFAULT_AUTHOR,
    spdx_license: str | None = "Apache-2.0",
    public: bool = True,
    compatibility: BundleCompatibility | None = None,
    verified_on: date = date(2026, 11, 10),
    sample_html: str = (
        "<html><body><article class='item'><h2>Evento</h2></article></body></html>"
    ),
) -> Path:
    sample = tmp_path / f"sample-{bundle_id}-{version}.html"
    sample.write_text(sample_html, encoding="utf-8")
    recipe = CssRecipe(record_selector="article.item", fields={"title": "h2::text"})
    export_bundle(
        directory,
        recipe=recipe,
        source=Source(url="https://agenda.example/espectaculos/"),
        context=RecipeContext(),
        fields=(StringField(name="title", min_length=1),),
        sample=sample,
        parser=ScraplingParser(),
        verified_on=verified_on,
        bundle_id=bundle_id,
        version=version,
        author=author,
        license=spdx_license,
        public=public,
        compatibility=compatibility,
    )
    return directory


def test_compute_trust_indicators() -> None:
    """HUB-006: trust indicators are computed from replay result and verification date."""
    trust = compute_trust(
        replay_verified=True,
        last_verified=date(2026, 11, 10),
        today=date(2026, 11, 15),
        has_sample=True,
        format_version=2,
    )
    assert trust == TrustIndicators(
        replay_verified=True,
        days_since_verified=5,
        has_sample=True,
        format=2,
    )


def test_publish_refuses_private_bundle(tmp_path: Path) -> None:
    """HUB-004: private bundles are never published; error hints to re-export with --public."""
    bundle = _write_sample_bundle(tmp_path / "bundle", tmp_path, public=False)
    registry = tmp_path / "registry"
    with pytest.raises(RegistryError, match="--public"):
        publish_to_registry(bundle, registry, _parser_for, today=date(2026, 11, 15))
    assert not (registry / "index.json").exists()


def test_publish_refuses_tampered_or_replay_failing_bundle(tmp_path: Path) -> None:
    """HUB-003, HUB-006: publish refuses a bundle whose digest or replay fails."""
    bundle = _write_sample_bundle(tmp_path / "bundle", tmp_path, public=True)
    registry = tmp_path / "registry"
    expected = bundle / "expected.json"
    bad_bytes = canonical_json([{"title": "Forged"}])
    expected.write_bytes(bad_bytes)
    manifest = json.loads((bundle / MANIFEST_FILE).read_text(encoding="utf-8"))
    manifest["files"]["expected.json"] = sha256_hex(bad_bytes)
    (bundle / MANIFEST_FILE).write_bytes(canonical_json(manifest))
    with pytest.raises(BundleVerificationError):
        publish_to_registry(bundle, registry, _parser_for, today=date(2026, 11, 15))
    assert not (registry / "index.json").exists()


def test_publish_refuses_secret_looking_values_without_echoing(tmp_path: Path) -> None:
    """HUB-004: secret-looking values in any bundle file are refused without echoing the secret."""
    secret_email = "leak.person@secret-domain.example.org"
    html = (
        f"<html><body><!-- {secret_email} -->"
        "<article class='item'><h2>Evento</h2></article></body></html>"
    )
    bundle = _write_sample_bundle(tmp_path / "bundle", tmp_path, public=True, sample_html=html)
    registry = tmp_path / "registry"
    with pytest.raises(RegistryError) as exc_info:
        publish_to_registry(bundle, registry, _parser_for, today=date(2026, 11, 15))
    assert secret_email not in str(exc_info.value)
    assert "email" in str(exc_info.value)
    assert not (registry / "index.json").exists()


def test_publish_refuses_duplicate_version_and_keeps_index_deterministic(tmp_path: Path) -> None:
    """HUB-003: publishing an existing id+version is refused; index.json is deterministic."""
    b1 = _write_sample_bundle(tmp_path / "b1", tmp_path, version="1.0.0")
    b2 = _write_sample_bundle(tmp_path / "b2", tmp_path, version="1.2.0")
    registry = tmp_path / "registry"
    today = date(2026, 11, 15)
    publish_to_registry(b2, registry, _parser_for, today=today)
    res = publish_to_registry(b1, registry, _parser_for, today=today)
    assert any("git" in cmd for cmd in res.git_commands)

    with pytest.raises(RegistryError, match="already exists"):
        publish_to_registry(b1, registry, _parser_for, today=today)

    index_bytes = (registry / "index.json").read_bytes()
    entries = json.loads(index_bytes)
    assert [e["version"] for e in entries] == ["1.0.0", "1.2.0"]
    assert index_bytes == canonical_json(entries)


def test_search_and_install_latest_and_pinned_recomputes_trust(tmp_path: Path) -> None:
    """HUB-003, HUB-005, HUB-006: search and install select version and recompute trust."""
    b1 = _write_sample_bundle(
        tmp_path / "b1",
        tmp_path,
        version="1.0.0",
        verified_on=date(2026, 11, 10),
        sample_html="<html><body><article class='item'><h2>V1</h2></article></body></html>",
    )
    b2 = _write_sample_bundle(
        tmp_path / "b2",
        tmp_path,
        version="1.1.0",
        verified_on=date(2026, 11, 12),
        sample_html="<html><body><article class='item'><h2>V2</h2></article></body></html>",
    )
    registry = tmp_path / "registry"
    publish_to_registry(b1, registry, _parser_for, today=date(2026, 11, 12))
    publish_to_registry(b2, registry, _parser_for, today=date(2026, 11, 12))

    hits_by_id = search_registry(registry, "teatro")
    assert [h.version for h in hits_by_id] == ["1.0.0", "1.1.0"]
    hits_by_pattern = search_registry(registry, "agenda.example/espectaculos")
    assert len(hits_by_pattern) == 2
    assert search_registry(registry, "nonexistent") == ()

    repo = SQLiteRecipeRepository(tmp_path / "recipes.sqlite")
    latest = install_from_registry(
        registry,
        "teatro-central-agenda",
        repo,
        _parser_for,
        at=datetime(2026, 11, 20, tzinfo=UTC),
    )
    assert latest.version == "1.1.0"
    assert latest.published_trust.days_since_verified == 0
    assert latest.trust.days_since_verified == 8
    assert latest.trust.replay_verified is True

    pinned = install_from_registry(
        registry,
        "teatro-central-agenda@1.0.0",
        repo,
        _parser_for,
        at=datetime(2026, 11, 20, tzinfo=UTC),
    )
    assert pinned.version == "1.0.0"
    assert pinned.published_trust.days_since_verified == 2
    assert pinned.trust.days_since_verified == 10


def test_path_traversal_and_symlinks_in_registry_are_refused(tmp_path: Path) -> None:
    """HUB-003, HUB-005: path traversal in specs or index entries and symlinks are refused."""
    with pytest.raises(RegistryError):
        parse_package_spec("../evil")
    with pytest.raises(RegistryError):
        parse_package_spec("teatro-central-agenda@../1.0.0")

    registry = tmp_path / "registry"
    registry.mkdir()
    bad_index = [
        {
            "id": "../escape",
            "version": "1.0.0",
            "author": {"name": "A", "url": None},
            "license": "MIT",
            "source_patterns": ["example.com"],
            "last_verified": "2026-11-10",
            "trust": {
                "replay_verified": True,
                "days_since_verified": 0,
                "has_sample": True,
                "format": 2,
            },
        }
    ]
    (registry / "index.json").write_bytes(canonical_json(bad_index))
    with pytest.raises((RegistryError, BundleIntegrityError, ConfigurationError)):
        search_registry(registry, "escape")

    link_reg = tmp_path / "link_reg"
    link_reg.symlink_to(registry, target_is_directory=True)
    with pytest.raises((RegistryError, BundleIntegrityError, ConfigurationError)):
        search_registry(link_reg, "escape")
