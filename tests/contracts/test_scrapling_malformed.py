"""DRIFT-003/INT-003/SEC-005: malformed HTML must stay inside typed parser errors."""

import pytest

from rastro import ConfigurationError, CssRecipe, JsonLdRecipe, Limits
from rastro.adapters.discovery import ScraplingDiscovery
from rastro.adapters.drift_probe import ScraplingDriftProbe
from rastro.adapters.scrapling import ScraplingParser


@pytest.mark.parametrize("body", ["<!--comment-->", '<?xml version="1.0" encoding="UTF-8"?><p/>'])
@pytest.mark.parametrize("adapter", ["parser", "probe", "discovery"])
@pytest.mark.parametrize("strategy", ["css", "jsonld"])
def test_malformed_html_raises_typed_error(body, adapter, strategy):
    recipe = (
        CssRecipe(record_selector="article", fields={"title": "h2::text"})
        if strategy == "css"
        else JsonLdRecipe(fields={"title": "/name"})
    )
    with pytest.raises(ConfigurationError, match="Invalid HTML"):
        if adapter == "parser":
            ScraplingParser().parse(body, recipe, Limits())
        elif adapter == "probe":
            ScraplingDriftProbe().measure(body, recipe, Limits())
        else:
            ScraplingDiscovery().discover(body, ("title",), Limits(), max_candidates=1)
