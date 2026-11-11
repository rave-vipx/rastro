"""Opt-in `expand:` CSS field: one record per matched value (DATA-004/005, JOB-002)."""

import pytest
from pydantic import ValidationError

from rastro import CssRecipe, Limits
from rastro.adapters.drift_probe import ScraplingDriftProbe
from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.scrapling_relocation import _expression
from rastro.domain.models import ConfigurationError, ResourceLimitError

CARD = """<div class="c"><h2>Obra A</h2><time>1 DIC</time><time>2 DIC</time><time>3 DIC</time></div>
<div class="c"><h2>Obra B</h2><time>9 DIC</time></div><div class="c"><h2>Obra C</h2></div>"""


def _recipe(date: str = "expand:time::text") -> CssRecipe:
    return CssRecipe(record_selector="div.c", fields={"title": "h2::text", "date": date})


def test_expand_field_yields_one_candidate_per_value_with_indexed_locator():
    candidates = ScraplingParser().parse(CARD, _recipe(), Limits()).candidates
    assert [(c.values["title"], c.values.get("date")) for c in candidates] == [
        ("Obra A", "1 DIC"),
        ("Obra A", "2 DIC"),
        ("Obra A", "3 DIC"),
        ("Obra B", "9 DIC"),
        ("Obra C", None),
    ]
    assert candidates[2].locators["date"] == "div.c [match 0] >> expand:time::text [value 2]"
    assert "date" not in candidates[4].locators
    assert all(not c.issues for c in candidates)


def test_without_prefix_several_matches_stay_ambiguous():
    candidate = ScraplingParser().parse(CARD, _recipe("time::text"), Limits()).candidates[0]
    assert [i.code for i in candidate.issues] == ["ambiguous_match"]


def test_expanded_records_respect_max_records():
    with pytest.raises(ResourceLimitError):
        ScraplingParser().parse(CARD, _recipe(), Limits(max_records=4))


def test_only_one_expand_field_and_no_document_combination():
    with pytest.raises(ValidationError, match="At most one"):
        CssRecipe(
            record_selector="div.c",
            fields={"a": "expand:time::text", "b": "expand:h2::text"},
        )
    with pytest.raises(ValidationError, match="document:"):
        _recipe("expand:document:time::text")


def test_drift_probe_measures_expand_field():
    structure = ScraplingDriftProbe().measure(CARD, _recipe(), Limits())
    assert structure["date"].selector_match_count == 4


def test_relocation_refuses_expand_field():
    with pytest.raises(ConfigurationError, match="expand"):
        _expression("expand:time::text")


def test_issue_record_index_counts_expanded_records_not_cards():
    """DATA-004: after an expanded card, issues blame the right record position."""
    from rastro import CssRecipe, Limits
    from rastro.adapters.scrapling import ScraplingParser

    html = (
        "<ul><li><h2>Obra A</h2><time>1 DIC</time><time>2 DIC</time></li>"
        "<li><h2>X</h2><h2>Y</h2><time>3 DIC</time></li></ul>"
    )
    recipe = CssRecipe(
        record_selector="li", fields={"title": "h2::text", "date": "expand:time::text"}
    )
    parsed = ScraplingParser().parse(html, recipe, Limits())
    assert [c.values.get("title") for c in parsed.candidates] == ["Obra A", "Obra A", None]
    assert not parsed.candidates[1].issues
    assert [i.record_index for i in parsed.candidates[2].issues] == [2]


def test_expand_values_are_bounded_before_records_are_built():
    """DATA-004: one card with too many values fails before building candidates."""
    import pytest

    from rastro import CssRecipe, Limits, ResourceLimitError
    from rastro.adapters.scrapling import ScraplingParser

    html = "<ul><li><h2>A</h2>" + "<time>d</time>" * 6 + "</li></ul>"
    recipe = CssRecipe(record_selector="li", fields={"title": "h2::text", "d": "expand:time::text"})
    with pytest.raises(ResourceLimitError):
        ScraplingParser().parse(html, recipe, Limits(max_records=5))
