import json

import pytest

from rastro import Limits, ResourceLimitError
from rastro.adapters.discovery import ScraplingDiscovery


def discover(html, *, fields=("name", "price"), limits=None, maximum=8):
    return ScraplingDiscovery().discover(html, fields, limits or Limits(), max_candidates=maximum)


def block(value):
    return '<script type="application/ld+json">' + json.dumps(value) + "</script>"


def test_nested_paths_require_unique_field_names_and_escape_tokens():
    candidates = discover(
        block(
            {"name": "Book", "offers": {"price": 3}, "nested": {"name": "Ambiguous", "a/b~c": 4}}
        ),
        fields=("name", "price", "a/b~c"),
    )
    assert candidates[0].fields == {"price": "/offers/price", "a/b~c": "/nested/a~1b~0c"}


def test_jsonld_candidates_are_deduplicated_ordered_and_capped():
    html = block(
        [
            {"@type": "Book", "name": "one"},
            {"@type": "Book", "name": "two"},
            {"@type": "Product", "name": "three"},
        ]
    )
    assert len(discover(html)) == 2
    assert [recipe.item_type for recipe in discover(html, maximum=1)] == ["Book"]


def test_no_candidates_for_empty_invalid_or_unrelated_content():
    assert discover("") == ()
    assert discover('<script type="application/ld+json">not JSON</script>') == ()
    assert discover(block({"unrelated": True})) == ()
    assert discover(block({"@type": "x" * 257, "name": "Book"})) == ()


def test_microdata_requires_safe_names_and_unambiguous_matches():
    html = (
        '<div itemscope><a itemprop="name" href="/book">Book</a>'
        '<b itemprop="price">2</b><b itemprop="price">3</b></div>'
    )
    candidates = discover(html, fields=("name", "price", 'unsafe"name'), maximum=1)
    assert candidates[0].fields == {"name": '[itemprop~="name"]::attr(href)'}
    assert discover("<div itemscope><p>No properties</p></div>") == ()


@pytest.mark.parametrize(
    ("html", "limits"),
    [
        ("content", Limits(max_html_bytes=1)),
        (block({}) * 2, Limits(max_jsonld_blocks=1)),
        (block([{}, {}]), Limits(max_records=1)),
        ("<div itemscope></div>" * 2, Limits(max_records=1)),
    ],
)
def test_document_budgets_apply_to_discovery(html, limits):
    with pytest.raises(ResourceLimitError):
        discover(html, limits=limits)


def test_deep_or_wide_jsonld_fails_with_explicit_traversal_budget():
    value = {"name": "Book"}
    for _ in range(10):
        value = {"nested": value}
    for payload in (value, {str(i): i for i in range(4097)}):
        with pytest.raises(ResourceLimitError, match="traversal budget"):
            discover(block(payload))
