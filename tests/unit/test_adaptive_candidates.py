"""Adaptive recipes: retained values first, labels next, the serving selector otherwise."""

from rastro.adapters.scrapling_adaptive import ScraplingAdaptive
from rastro.application.adaptive_candidates import adaptive_recipes
from rastro.domain.models import CssRecipe, Limits

PAGE = """<html><body><div class="grid">
<article class="tile"><h3 class="name">Alpha lamp</h3><a class="go" href="/a">more</a>
<p><span>Price:</span> <b>14.00</b></p></article>
<article class="tile"><h3 class="name">Beta chair</h3><a class="go" href="/b">more</a>
<p><span>Price:</span> <b>31.00</b></p></article>
</div></body></html>"""
PREVIOUS = CssRecipe(
    record_selector="li.item",
    fields={"title": "h2::text", "price": "span.cost::text", "link": "a::attr(href)"},
    version=3,
)
HISTORY = [{"title": "Alpha lamp", "price": "12.50", "link": "/a"}, {"title": "Beta chair"}]


def test_values_then_labels_then_serving_selectors() -> None:
    (recipe,) = adaptive_recipes(
        ScraplingAdaptive(), PAGE, previous=PREVIOUS, history=HISTORY, limits=Limits()
    )
    assert recipe.record_selector == "article.tile"
    assert recipe.fields["title"] == ":scope > h3.name::text"
    assert recipe.fields["price"] == ":scope > p > b::text"  # next to its "Price:" label
    assert recipe.fields["link"] == "a::attr(href)"  # attribute values are not searched
    assert recipe.version == 3


def test_no_known_value_on_the_page_gives_no_recipe() -> None:
    history = [{"title": "Gone", "price": True}, {"title": None}]
    assert (
        adaptive_recipes(
            ScraplingAdaptive(), PAGE, previous=PREVIOUS, history=history, limits=Limits()
        )
        == ()
    )
