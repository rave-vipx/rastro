"""Install gates shared by the local and the URL registry (HUB-003, HUB-005, HUB-006, N10).

A package directory is untrusted bytes until every gate passes; the result is a candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rastro.application.engine_version import running_version
from rastro.application.recipe_hub import ImportedRecipe, ParserFor, import_bundle, verify_bundle
from rastro.domain.models import Limits
from rastro.domain.recipe_bundle import SAMPLE_FILES, BundleIntegrityError
from rastro.domain.registry import (
    RegistryEntry,
    TrustIndicators,
    check_compatibility,
    compute_trust,
)
from rastro.ports.recipes import RecipeRepository


@dataclass(frozen=True)
class InstalledPackage:
    """Result of installing a recipe package from a local registry directory or a URL."""

    id: str
    version: str
    imported: ImportedRecipe
    trust: TrustIndicators
    published_trust: TrustIndicators


def install_package_dir(
    package_dir: Path,
    selected: RegistryEntry,
    repository: RecipeRepository,
    parser_for: ParserFor,
    *,
    at: datetime,
    limits: Limits | None = None,
    engine_version: str | None = None,
) -> InstalledPackage:
    """Digests, selector policy, offline replay, index match and compatibility, then store a
    candidate. Nothing is promoted and no job setting is read or written."""
    manifest, files, _, _ = verify_bundle(package_dir, parser_for, limits)
    if manifest.id != selected.id or manifest.version != selected.version:
        raise BundleIntegrityError("Package manifest does not match its registry index entry")
    check_compatibility(manifest, engine_version=engine_version or running_version())
    recomputed = compute_trust(
        replay_verified=True,
        last_verified=manifest.last_verified,
        today=at.date(),
        has_sample=bool(files.get(SAMPLE_FILES[manifest.representation])),
        format_version=manifest.format_version,
    )
    imported = import_bundle(package_dir, repository, parser_for, at=at, limits=limits)
    return InstalledPackage(
        id=selected.id,
        version=selected.version,
        imported=imported,
        trust=recomputed,
        published_trust=selected.trust,
    )
