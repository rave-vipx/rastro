"""HUB-002: a format-1 bundle written by the 0.7 export still imports unchanged.

``tests/fixtures/bundle_format1`` was produced by the real ``rastro recipe export`` CLI at
commit 87da0f6 (before format 2), from the synthetic ``examples/uy-dance-agenda`` job.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.structured import JsonParser
from rastro.application.recipe_hub import import_bundle
from rastro.domain.lifecycle import RecipeState
from rastro.domain.models import Representation
from rastro.domain.recipe_bundle import MANIFEST_FILE, BundleManifest, sha256_hex
from rastro.ports.parser import DocumentParser

FIXTURE = Path(__file__).parents[1] / "fixtures/bundle_format1"
AT = datetime(2026, 11, 15, tzinfo=UTC)


def _parser_for(representation: Representation) -> DocumentParser:
    return JsonParser() if representation == "json" else ScraplingParser()


def test_format_1_bundle_fixture_imports_unchanged(tmp_path: Path) -> None:
    manifest = BundleManifest.model_validate_json((FIXTURE / MANIFEST_FILE).read_bytes())
    assert manifest.format_version == 1
    assert manifest.id is None
    assert all(sha256_hex((FIXTURE / name).read_bytes()) == d for name, d in manifest.files.items())

    bundle = tmp_path / "bundle"
    shutil.copytree(FIXTURE, bundle)
    repository = SQLiteRecipeRepository(tmp_path / "recipes.sqlite")
    first = import_bundle(bundle, repository, _parser_for, at=AT)
    assert (first.reused, first.stored.metadata.state) == (False, RecipeState.CANDIDATE)
    assert import_bundle(bundle, repository, _parser_for, at=AT).reused is True
    for path in FIXTURE.iterdir():  # import never rewrites the bundle
        assert (bundle / path.name).read_bytes() == path.read_bytes()
