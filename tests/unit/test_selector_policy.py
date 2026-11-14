"""CSS selectors from untrusted recipes stay cheap to evaluate (security audit H3).

lxml evaluates ``a ~ b`` and ``a + b`` through ``following-sibling::``, quadratic in the
sibling count; chained, ``p ~ p ~ p ~ b`` took 105 s of CPU on a 48 KB page.
"""

import pytest

from rastro.domain.models import ConfigurationError, Recipe
from rastro.domain.selector_policy import check_untrusted_recipe, check_untrusted_selector


@pytest.mark.parametrize(
    "selector",
    [
        "div.offer",
        "ul > li a[href]",
        "li:nth-child(2n+1)",
        "li:nth-child(n + 3)",
        "a[href~='x'], a[rel|=next]",
        "p:not(.ad)",
        "[data-x='a ~ b + c']",
        "div > ul > li > a > span > b > i > em",
    ],
)
def test_ordinary_selectors_pass(selector: str) -> None:
    check_untrusted_selector(selector)


@pytest.mark.parametrize(
    ("selector", "rule"),
    [
        ("p ~ p ~ p ~ b", "sibling"),
        ("h2 + p", "sibling"),
        ("h2~p", "sibling"),
        ("p:is(a + b)", "sibling"),
        ("div:has(> p)", ":has"),
        ("div:HAS(p)", ":has"),
        ("a b c d e f g h i", "compound"),
        (", ".join(f"a{i}" for i in range(9)), "alternatives"),
        ("p:not(:is(:not(.x)))", "nesting"),
    ],
)
def test_costly_selectors_are_refused(selector: str, rule: str) -> None:
    with pytest.raises(ConfigurationError, match=rule):
        check_untrusted_selector(selector)


def test_recipe_check_covers_records_fields_and_fallbacks() -> None:
    def css(records: str, field: str, fallbacks: tuple[object, ...] = ()) -> Recipe:
        return Recipe.model_validate(
            {
                "strategy": "css",
                "record_selector": records,
                "fields": {"t": field},
                "fallbacks": fallbacks,
            }
        )

    ok = css("div", "document:h1::text")
    assert check_untrusted_recipe(ok) is ok
    check_untrusted_recipe(css("div", "::text"))
    check_untrusted_recipe(css("div", "expand:a::attr(href)"))
    for bad in (
        css("p ~ b", "::text"),
        css("div", "h2 + p::text"),
        css(
            "div",
            "::text",
            ({"strategy": "css", "record_selector": "a ~ b", "fields": {"t": "::text"}},),
        ),
    ):
        with pytest.raises(ConfigurationError, match="sibling"):
            check_untrusted_recipe(bad)
    jsonld = Recipe.model_validate({"strategy": "jsonld", "fields": {"t": "/name"}})
    assert check_untrusted_recipe(jsonld) is jsonld
