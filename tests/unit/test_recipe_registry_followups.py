"""HUB-002, HUB-003, HUB-006: install compatibility, publish cleanup and quoted git commands."""

import shlex
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application import engine_version
from rastro.application.recipe_registry import install_from_registry, publish_to_registry
from rastro.domain.models import ConfigurationError
from rastro.domain.recipe_bundle import BundleCompatibility, BundleManifest
from rastro.domain.registry import RegistryError, check_compatibility, source_matches
from tests.unit.test_recipe_registry import _parser_for, _write_sample_bundle

TODAY = date(2026, 11, 15)
AT = datetime(2026, 11, 20, tzinfo=UTC)
AGENDA_HOST = "agenda.example"


def _published(tmp_path: Path, **compat: object) -> Path:
    settings = {"min_rastro": "0.7.0", "source_patterns": (AGENDA_HOST,), **compat}
    bundle = _write_sample_bundle(
        tmp_path / "bundle",
        tmp_path,
        compatibility=BundleCompatibility(**settings),  # type: ignore[arg-type]
    )
    registry = tmp_path / "registry"
    publish_to_registry(bundle, registry, _parser_for, today=TODAY)
    return registry


def _install(registry: Path, tmp_path: Path, **kwargs: object):
    repo = SQLiteRecipeRepository(tmp_path / "recipes.sqlite")
    return install_from_registry(
        registry,
        "teatro-central-agenda",
        repo,
        _parser_for,
        at=AT,
        **kwargs,  # type: ignore[arg-type]
    )


def test_install_refuses_engine_older_than_min_rastro(tmp_path: Path) -> None:
    registry = _published(tmp_path, min_rastro="0.9.0")
    with pytest.raises(ConfigurationError) as error:
        _install(registry, tmp_path, engine_version="0.7.0")
    assert "0.9.0" in str(error.value)
    assert "0.7.0" in str(error.value)
    assert not (tmp_path / "recipes.sqlite").exists()


@pytest.mark.parametrize("running", ["0.9.0", "0.9.1", "1.0.0", "0.10.0"])
def test_install_accepts_engine_at_or_above_min_rastro(tmp_path: Path, running: str) -> None:
    registry = _published(tmp_path, min_rastro="0.9.0")
    assert _install(registry, tmp_path, engine_version=running).version == "1.0.0"


def test_install_refuses_source_outside_bundle_patterns(tmp_path: Path) -> None:
    registry = _published(tmp_path, source_patterns=("other.example.com",))
    with pytest.raises(ConfigurationError, match="source_patterns"):
        _install(registry, tmp_path)
    assert not (tmp_path / "recipes.sqlite").exists()


@pytest.mark.parametrize(
    ("url", "patterns", "expected"),
    [
        ("https://agenda.example/espectaculos/", ("agenda.example",), True),
        ("https://AGENDA.example/x", ("agenda.example",), True),
        ("https://agenda.example/espectaculos/", ("agenda.example/espectaculos",), True),
        ("https://agenda.example/espectaculos/2026", ("agenda.example/espectaculos",), True),
        ("https://agenda.example/espectaculos2", ("agenda.example/espectaculos",), False),
        ("https://agenda.example/agenda", ("agenda.example/espectaculos",), False),
        ("https://evil.example/agenda.example", ("agenda.example",), False),
        ("https://agenda.example.evil.example/", ("agenda.example",), False),
        ("https://agenda.example:8443/", ("agenda.example",), False),
        ("https://agenda.example:8443/a", ("agenda.example:8443/a",), True),
        ("https://user:pw@agenda.example/", ("agenda.example",), True),
        ("https://agenda.example/", ("other.example", "agenda.example"), True),
    ],
)
def test_source_matches_host_or_path_prefix(
    url: str, patterns: tuple[str, ...], expected: bool
) -> None:
    assert source_matches(url, patterns) is expected


def test_check_compatibility_skips_manifests_without_compatibility() -> None:
    manifest = BundleManifest.model_validate(
        {
            "format_version": 1,
            "source": {"url": "https://agenda.example/"},
            "context": {},
            "representation": "html",
            "last_verified": "2026-01-01",
            "files": dict.fromkeys(
                ("recipe.json", "schema.json", "expected.json", "sample.html"), "0" * 64
            ),
        }
    )
    check_compatibility(manifest, engine_version="0.0.1")  # nothing declared, nothing to refuse


def test_engine_version_reads_the_installed_semver_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_version, "pkg_version", lambda _name: "0.8.0.dev3+local")
    assert engine_version.running_version() == "0.8.0"


def test_engine_version_without_distribution_is_a_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(engine_version, "pkg_version", missing)
    with pytest.raises(ConfigurationError, match="rastro-engine"):
        engine_version.running_version()


def test_failed_index_write_removes_only_the_new_package_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "registry"
    first = _write_sample_bundle(tmp_path / "b1", tmp_path, version="1.0.0")
    publish_to_registry(first, registry, _parser_for, today=TODAY)
    kept = registry / "recipes/teatro-central-agenda/1.0.0"
    index_before = (registry / "index.json").read_bytes()

    second = _write_sample_bundle(tmp_path / "b2", tmp_path, version="1.1.0")

    def disk_full(_path: Path, _data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("rastro.application.recipe_registry.write_index_atomic", disk_full)
    with pytest.raises(RegistryError, match="nothing was published"):
        publish_to_registry(second, registry, _parser_for, today=TODAY)
    assert not (registry / "recipes/teatro-central-agenda/1.1.0").exists()
    assert kept.is_dir()
    assert (registry / "index.json").read_bytes() == index_before

    monkeypatch.undo()
    published = publish_to_registry(second, registry, _parser_for, today=TODAY)
    assert published.package_dir.is_dir()


def test_printed_git_commands_quote_the_registry_path(tmp_path: Path) -> None:
    registry = tmp_path / "my registry; touch pwned $(id)"
    bundle = _write_sample_bundle(tmp_path / "bundle", tmp_path)
    published = publish_to_registry(bundle, registry, _parser_for, today=TODAY)
    for command in published.git_commands:
        words = shlex.split(command)
        assert words[:2] == ["git", "-C"]
        assert words[2] == str(registry)
    assert shlex.split(published.git_commands[1])[-1] == "Publish teatro-central-agenda 1.0.0"
