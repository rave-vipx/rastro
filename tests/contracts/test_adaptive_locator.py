"""The engine's adaptive tools find records, stable selectors and labelled values offline.

Every page is synthetic. The locator only proposes selectors; recipe CI decides.
"""

import pytest
from scrapling.parser import Selector

from rastro.adapters.scrapling_adaptive import ScraplingAdaptive
from rastro.domain.models import ConfigurationError, Limits, ResourceLimitError
from rastro.ports.engine import AdaptiveLocator

CARDS = """<html><body><nav><a href="/">Home</a></nav><div class="grid">
<article class="tile-v2"><h3 class="name">Alpha lamp</h3>
<p class="meta"><span class="lbl">Price:</span> <b>12.50</b></p></article>
<article class="tile-v2"><h3 class="name">Beta chair</h3>
<p class="meta"><span class="lbl">Price:</span> <b>30.00</b></p></article>
<article class="tile-v2"><h3 class="name">Gamma desk</h3>
<p class="meta"><span class="lbl">Price:</span> <b>99.00</b></p></article>
</div></body></html>"""
TERMS = """<html><body><ul>
<li class="row"><h4>Alpha lamp</h4><dl><dt>Price</dt><dd>12.50</dd></dl></li>
<li class="row"><h4>Beta chair</h4><dl><dt>Price</dt><dd>30.00</dd></dl></li>
</ul></body></html>"""


def values(html: str, record: str, locator: str) -> list[str]:
    page = Selector(html, adaptive=False)
    return [str(node.css(locator + "::text").get()).strip() for node in page.css(record)]


def test_adapter_satisfies_the_port() -> None:
    locator: AdaptiveLocator = ScraplingAdaptive()
    assert locator.similar_records(CARDS, "nothing like this", limits=Limits()) == ()


def test_similar_records_from_a_known_value() -> None:
    (group,) = ScraplingAdaptive().similar_records(CARDS, "Alpha lamp", limits=Limits())
    assert (group.record_selector, group.count) == ("article.tile-v2", 3)
    locator = group.field_locators["Alpha lamp"]
    assert values(CARDS, group.record_selector, locator) == [
        "Alpha lamp",
        "Beta chair",
        "Gamma desk",
    ]


def test_similar_records_from_a_partial_value() -> None:
    (group,) = ScraplingAdaptive().similar_records(CARDS, "30.0", limits=Limits())
    assert group.record_selector == "article.tile-v2"
    assert values(CARDS, group.record_selector, group.field_locators["30.0"])[1] == "30.00"


def test_find_by_label_anchor() -> None:
    """A field is found next to its visible "Price:" label in every record."""
    adaptive = ScraplingAdaptive()
    locator = adaptive.anchor_by_label(CARDS, "article.tile-v2", "Price", limits=Limits())
    assert locator is not None
    assert values(CARDS, "article.tile-v2", locator) == ["12.50", "30.00", "99.00"]
    term = adaptive.anchor_by_label(TERMS, "li.row", "price", limits=Limits())
    assert term is not None and values(TERMS, "li.row", term) == ["12.50", "30.00"]
    assert adaptive.anchor_by_label(CARDS, "article.tile-v2", "Colour", limits=Limits()) is None


def test_stable_selector_prefers_classes_over_positions() -> None:
    adaptive = ScraplingAdaptive()
    assert adaptive.stable_selector(CARDS, "body > div", limits=Limits()) == "div.grid"
    # Three cards share one shape: only a position names the first one.
    first = adaptive.stable_selector(CARDS, "div > article", limits=Limits())
    assert first is not None
    assert values(CARDS, first, "h3") == ["Alpha lamp"]
    assert adaptive.stable_selector(CARDS, "table", limits=Limits()) is None


def test_invalid_selector_and_budgets_fail_closed() -> None:
    adaptive = ScraplingAdaptive()
    with pytest.raises(ConfigurationError):
        adaptive.stable_selector(CARDS, "div[", limits=Limits())
    with pytest.raises(ResourceLimitError):
        adaptive.similar_records(CARDS, "Alpha lamp", limits=Limits(max_html_bytes=10))
