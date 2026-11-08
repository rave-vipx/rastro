"""Opt-in `document:` CSS field expressions and blank text handling (SCR-003 neutral, DATA-004)."""

import pytest
from pydantic import ValidationError

from rastro import CssRecipe, Limits
from rastro.adapters.drift_probe import ScraplingDriftProbe
from rastro.adapters.scrapling import ScraplingParser

PAGE = """<html><head><meta property="og:title" content="Sala Uno"></head><body>
<footer><strong>Sala Uno</strong></footer>
<ul><li><b>Obra A</b></li><li><b>Obra B</b></li></ul></body></html>"""


def _recipe(expression: str) -> CssRecipe:
    return CssRecipe(record_selector="li", fields={"title": "b::text", "venue": expression})


def _parse(html: str, expression: str):
    return ScraplingParser().parse(html, _recipe(expression), Limits())


def test_document_field_is_shared_by_every_record_and_keeps_full_locator():
    expression = "document:footer strong::text"
    candidates = _parse(PAGE, expression).candidates
    assert [c.values["venue"] for c in candidates] == ["Sala Uno", "Sala Uno"]
    assert candidates[1].locators["venue"] == f"li [match 1] >> {expression}"
    assert candidates[0].issues == ()


def test_document_attribute_expression():
    candidates = _parse(PAGE, 'document:meta[property="og:title"]::attr(content)').candidates
    assert candidates[0].values["venue"] == "Sala Uno"


def test_document_field_without_match_is_absent():
    candidate = _parse(PAGE, "document:footer em::text").candidates[0]
    assert "venue" not in candidate.values and "venue" not in candidate.locators
    assert candidate.issues == ()


def test_document_field_with_several_matches_is_ambiguous():
    html = PAGE.replace("</footer>", "<strong>Otra</strong></footer>")
    candidate = _parse(html, "document:footer strong::text").candidates[0]
    assert "venue" not in candidate.values
    assert [(i.code, i.field) for i in candidate.issues] == [("ambiguous_match", "venue")]


def test_plain_expression_still_scoped_to_record():
    assert "venue" not in _parse(PAGE, "footer strong::text").candidates[0].values


def test_document_prefix_still_needs_a_value_suffix():
    with pytest.raises(ValidationError, match="::text or ::attr"):
        _recipe("document:footer strong")


def test_blank_text_nodes_are_not_matches():
    html = "<p class='r'><b>\n  Sab 4 DIC\n  <i>x</i>\n </b></p>"
    recipe = CssRecipe(record_selector="p.r", fields={"date": "b::text"})
    candidate = ScraplingParser().parse(html, recipe, Limits()).candidates[0]
    assert candidate.values["date"] == "Sab 4 DIC"
    assert candidate.issues == ()


def test_drift_probe_accepts_document_fields():
    recipe = _recipe("document:footer strong::text")
    structure = ScraplingDriftProbe().measure(PAGE, recipe, Limits())
    assert structure["venue"].selector_match_count == 2


def test_relocation_capture_refuses_document_fields():
    from rastro.adapters.scrapling_relocation import _expression
    from rastro.domain.models import ConfigurationError

    with pytest.raises(ConfigurationError, match="document-scoped"):
        _expression("document:footer strong::text")
