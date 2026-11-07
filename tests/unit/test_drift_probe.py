"""Measured counts and content-free fingerprints (DRIFT-001, SEC-004/008)."""

import pytest

from rastro import CssRecipe, JsonRecipe, Limits, ResourceLimitError
from rastro.adapters.drift_probe import ScraplingDriftProbe

PROBE = ScraplingDriftProbe()
RECIPE = CssRecipe(record_selector="article", fields={"title": "span::text"})


def test_fingerprint_ignores_source_values_and_attributes_but_detects_moved_nodes():
    one = PROBE.measure('<article><span class="secret">Alice</span></article>', RECIPE, Limits())
    two = PROBE.measure('<article><span class="other">Bob</span></article>', RECIPE, Limits())
    moved = PROBE.measure("<article><div><span>Bob</span></div></article>", RECIPE, Limits())
    assert one == two
    assert one["title"].subtree_fingerprint != moved["title"].subtree_fingerprint
    assert one["title"].selector_match_count == 1
    assert "secret" not in one["title"].model_dump_json()


def test_attribute_self_and_selector_lists_are_measured():
    recipe = CssRecipe(
        record_selector="article",
        fields={"id": "::attr(data-id)", "title": 'span[title="::text"]::text, b::text'},
    )
    measured = PROBE.measure(
        '<article data-id="x"><span title="::text">A</span><b>B</b></article>', recipe, Limits()
    )
    assert measured["id"].selector_match_count == 1
    assert measured["title"].selector_match_count == 2
    moved = PROBE.measure(
        '<article data-id="x"><span title="::text">A</span><div><b>B</b></div></article>',
        recipe,
        Limits(),
    )
    assert moved["title"].subtree_fingerprint != measured["title"].subtree_fingerprint


def test_structured_invalid_input_is_unknown_and_has_no_fabricated_dom_hash():
    recipe = JsonRecipe(record_path="", fields={"title": "/title"})
    valid = PROBE.measure('{"title":"Hello"}', recipe, Limits())
    assert valid["title"].selector_match_count == 1
    assert valid["title"].subtree_fingerprint is None
    invalid = PROBE.measure("{", recipe, Limits())
    assert invalid["title"].selector_match_count is None


@pytest.mark.parametrize(
    "body,limits",
    [
        ("<article><span>Hello</span></article>", Limits(max_html_bytes=1)),
        ("<article></article><article></article>", Limits(max_records=1)),
        ("<article><span><i><b>Hello</b></i></span></article>", Limits(max_json_depth=1)),
        ("<article><span></span><span></span></article>", Limits(max_json_nodes=1)),
    ],
)
def test_probe_enforces_resource_limits(body, limits):
    with pytest.raises(ResourceLimitError):
        PROBE.measure(body, RECIPE, limits)
