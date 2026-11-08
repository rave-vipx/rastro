"""Blank text never decides uniqueness or hides validation (SCR-003, DATA-004)."""

import pytest

from rastro import CssRecipe, Limits, Outcome, Source, StringField
from rastro.adapters.drift_probe import ScraplingDriftProbe
from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.application.extraction import extract_document
from rastro.domain.job_fields import build_job_schema
from rastro.domain.models import ConfigurationError

SOURCE = Source(url="https://example.com/")


def _extract(html: str, fields: dict[str, str], optional: tuple[str, ...]):
    recipe = CssRecipe(record_selector="li", fields=fields, require_evidence=True)
    schema = build_job_schema(
        [StringField(name=name, required=name not in optional, min_length=1) for name in fields]
    )
    result = extract_document(
        html,
        source=SOURCE,
        schema=schema,
        recipe=recipe,
        parser=ScraplingParser(),
        limits=Limits(),
    )
    return recipe, result


def test_blank_sibling_element_still_makes_the_match_ambiguous():
    html = "<ul><li><b>Obra</b><p>   </p><p>Autor</p></li></ul>"
    parsed = ScraplingParser().parse(
        html, CssRecipe(record_selector="li", fields={"performers": "p::text"}), Limits()
    )
    assert [i.code for i in parsed.candidates[0].issues] == ["ambiguous_match"]
    recipe, result = _extract(html, {"title": "b::text", "performers": "p::text"}, ("performers",))
    assert result.status != Outcome.SUCCESS
    with pytest.raises(ConfigurationError):
        ScraplingRelocation().capture(
            html, source=SOURCE, recipe=recipe, result=result, limits=Limits()
        )


@pytest.mark.parametrize(
    ("markup", "selector"),
    [("<p>   </p>", "p::text"), ('<img alt="">', "img::attr(alt)")],
)
def test_blank_value_of_one_element_fails_min_length_instead_of_becoming_none(markup, selector):
    html = f"<ul><li><b>Obra</b>{markup}</li></ul>"
    _, result = _extract(html, {"title": "b::text", "extra": selector}, ("extra",))
    assert result.status != Outcome.SUCCESS


def test_whitespace_around_nested_markup_is_not_evidence_and_probe_agrees():
    html = "<p class='r'><b>\n  Sab 4 DIC\n  <i>x</i>\n </b></p>"
    recipe = CssRecipe(record_selector="p.r", fields={"date": "b::text"})
    parsed = ScraplingParser().parse(html, recipe, Limits())
    assert parsed.candidates[0].values == {"date": "Sab 4 DIC"}
    assert ScraplingDriftProbe().measure(html, recipe, Limits())["date"].selector_match_count == 1


def test_relocation_capture_rejects_document_field_even_when_unobserved():
    html = "<ul><li><b>Obra A</b></li></ul>"
    recipe, result = _extract(
        html, {"title": "b::text", "venue": "document:footer strong::text"}, ("venue",)
    )
    assert result.status == Outcome.SUCCESS
    with pytest.raises(ConfigurationError, match="document-scoped"):
        ScraplingRelocation().capture(
            html, source=SOURCE, recipe=recipe, result=result, limits=Limits()
        )
