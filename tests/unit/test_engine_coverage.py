"""Engine capability map: every Scrapling feature is accounted for without optional imports."""

from __future__ import annotations

from pathlib import Path

import pytest

from rastro.domain.engine_map import EngineFeature, planned_features
from rastro.engine_coverage import (
    SCRAPLING_FEATURES,
    inspect_scrapling_features,
    render_engines_markdown,
)

ROOT = Path(__file__).resolve().parents[2]


def test_every_scrapling_feature_mapped() -> None:
    """Every public Scrapling feature found by static inspection appears in SCRAPLING_FEATURES."""
    discovered = inspect_scrapling_features()
    assert len(discovered) > 100
    names = [feature.name for feature in SCRAPLING_FEATURES]
    assert len(names) == len(set(names)), "duplicate feature names in SCRAPLING_FEATURES"
    assert set(names) == discovered


def test_not_exposed_has_reason() -> None:
    """Every not_exposed feature explains why Rastro does not expose it."""
    not_exposed = [f for f in SCRAPLING_FEATURES if f.surface == "not_exposed"]
    assert not_exposed
    for feature in not_exposed:
        assert feature.reason.strip(), feature.name
        assert not feature.where.strip(), feature.name


def test_every_docs_link_in_engine_features_exists() -> None:
    """Every docs reference on an EngineFeature points to an existing guide and heading."""
    import re

    from tests.unit.test_capabilities import _slug

    for feature in SCRAPLING_FEATURES:
        if not feature.docs:
            continue
        page, _, anchor = feature.docs.partition("#")
        text = (ROOT / page).read_text(encoding="utf-8")
        if anchor:
            headings = re.findall(r"^#+ (.+)$", text, flags=re.MULTILINE)
            assert anchor in {_slug(h) for h in headings}, f"{feature.name}: {feature.docs}"


def test_browser_evaluate_not_exposed_with_reason() -> None:
    """Arbitrary JavaScript evaluation is never exposed through MCP or recipes."""
    by_name = {feature.name: feature for feature in SCRAPLING_FEATURES}
    evaluate = by_name["mcp.browser_evaluate"]
    assert evaluate.surface == "not_exposed"
    assert "javascript" in evaluate.reason.lower() or "code" in evaluate.reason.lower()


def test_release_gate_forbids_planned(monkeypatch: pytest.MonkeyPatch) -> None:
    """At release time (RASTRO_RELEASE_GATE=1), no feature may remain planned."""
    import os

    sample = EngineFeature(
        engine="scrapling",
        area="adaptive",
        name="Selector.find_similar",
        surface="planned",
    )
    assert planned_features((sample,)) == (sample,)
    if os.environ.get("RASTRO_RELEASE_GATE") != "1":
        pytest.skip("Release gate is enabled only when RASTRO_RELEASE_GATE=1")
    assert planned_features(SCRAPLING_FEATURES) == ()


def test_engines_md_generated_matches() -> None:
    """docs/engines.md matches the rendered capability map byte for byte."""
    path = ROOT / "docs" / "engines.md"
    assert path.exists(), "docs/engines.md is missing"
    assert path.read_text(encoding="utf-8") == render_engines_markdown()
