"""Offline repair candidates preserve evidence and activation gates (REPAIR-001, SCR-002)."""

from pydantic import BaseModel

from rastro import CssRecipe, Limits, Outcome, Source
from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.application.extraction import extract_document


class Event(BaseModel):
    title: str


SOURCE = Source(url="https://example.test/events")
RECIPE = CssRecipe(record_selector="article", fields={"title": "h2.old::text"})
BEFORE = '<article><h2 class="old">Synthetic dance</h2><p>Other</p></article>'
AFTER = '<article><section><h2 class="new">Synthetic dance</h2></section><p>Other</p></article>'


def successful_snapshot(html=BEFORE, recipe=RECIPE):
    result = extract_document(
        html, source=SOURCE, schema=Event, recipe=recipe, parser=ScraplingParser(), limits=Limits()
    )
    assert result.status == Outcome.SUCCESS
    return ScraplingRelocation().capture(
        html, source=SOURCE, recipe=recipe, result=result, limits=Limits()
    )


def test_relocation_recovers_moved_node_without_fetching_or_persisting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    adapter = ScraplingRelocation()
    snapshot = successful_snapshot()
    candidates = adapter.relocate(AFTER, RECIPE, snapshot, Limits())
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.origin == "scrapling_relocation"
    assert candidate.recipe.require_evidence
    recovered = extract_document(
        AFTER,
        source=SOURCE,
        schema=Event,
        recipe=candidate.recipe,
        parser=ScraplingParser(),
        limits=Limits(),
    )
    assert recovered.status == Outcome.SUCCESS
    assert recovered.records[0].data.title == "Synthetic dance"
    assert recovered.records[0].evidence["title"].locator
    assert not list(tmp_path.iterdir())
    assert adapter.relocate(AFTER, RECIPE, snapshot, Limits()) == candidates


def test_unchanged_template_yields_no_relocation_candidate():
    assert ScraplingRelocation().relocate(BEFORE, RECIPE, successful_snapshot(), Limits()) == ()


def test_tied_matches_are_not_silently_selected():
    html = "<article>" + '<h2 class="new">Synthetic dance</h2>' * 2 + "</article>"
    (candidate,) = ScraplingRelocation().relocate(html, RECIPE, successful_snapshot(), Limits())
    assert candidate.ambiguous
    assert candidate.evidence[0].match_count == 2
    result = extract_document(
        html,
        source=SOURCE,
        schema=Event,
        recipe=candidate.recipe,
        parser=ScraplingParser(),
        limits=Limits(),
    )
    assert result.status == Outcome.INSUFFICIENT_EVIDENCE
    assert not result.records


def test_fingerprints_and_debug_logging_do_not_retain_source_content(caplog):
    import logging

    from rastro.domain.repair import RelocationSnapshot

    html = (
        '<article data-private="secret-cookie"><!--private-comment-->'
        '<h2 class="old" data-private-key="secret-token">Synthetic dance</h2></article>'
    )
    with caplog.at_level(logging.DEBUG):
        snapshot = successful_snapshot(html)
        ScraplingRelocation().relocate(AFTER, RECIPE, snapshot, Limits())
    payload = snapshot.model_dump_json()
    assert RelocationSnapshot.model_validate_json(payload) == snapshot
    for value in (
        "secret-cookie",
        "secret-token",
        "data-private-key",
        "Synthetic dance",
        "private-comment",
    ):
        assert value not in payload + caplog.text


def test_capture_rejects_failed_or_mismatched_runs():
    import pytest

    from rastro import ConfigurationError

    result = extract_document(
        AFTER, source=SOURCE, schema=Event, recipe=RECIPE, parser=ScraplingParser(), limits=Limits()
    )
    with pytest.raises(ConfigurationError, match="successful"):
        ScraplingRelocation().capture(
            AFTER, source=SOURCE, recipe=RECIPE, result=result, limits=Limits()
        )
    result = extract_document(
        BEFORE,
        source=SOURCE,
        schema=Event,
        recipe=RECIPE,
        parser=ScraplingParser(),
        limits=Limits(),
    )
    for html, source in ((BEFORE + BEFORE, SOURCE), (BEFORE, Source(url="https://other.test"))):
        with pytest.raises(ConfigurationError):
            ScraplingRelocation().capture(
                html, source=source, recipe=RECIPE, result=result, limits=Limits()
            )


def test_relocation_checks_mutated_snapshot_and_recipe_identity():
    import pytest

    from rastro import ConfigurationError

    snapshot = successful_snapshot()
    with pytest.raises(ConfigurationError, match="match the recipe"):
        ScraplingRelocation().relocate(
            AFTER, RECIPE.model_copy(update={"version": 2}), snapshot, Limits()
        )
    snapshot.fields["title"].attributes["private-attribute"] = "private-value"
    with pytest.raises(ConfigurationError, match="Invalid relocation snapshot"):
        ScraplingRelocation().relocate(AFTER, RECIPE, snapshot, Limits())


def test_relocation_enforces_byte_record_node_depth_and_utf8_limits():
    import pytest

    from rastro import ConfigurationError, ResourceLimitError

    snapshot = successful_snapshot()
    adapter = ScraplingRelocation()
    for html, limits in (
        (AFTER, Limits(max_html_bytes=1)),
        (AFTER + AFTER, Limits(max_records=1)),
        ("<p/>" * 4097, Limits()),
        ("<div>" * 129 + "x" + "</div>" * 129, Limits()),
        ("<p " + " ".join(f'a{i}="x"' for i in range(101)) + "/>", Limits()),
    ):
        with pytest.raises(ResourceLimitError):
            adapter.relocate(html, RECIPE, snapshot, limits)
    with pytest.raises(ConfigurationError, match="UTF-8"):
        adapter.relocate("\ud800", RECIPE, snapshot, Limits())
    assert adapter.relocate("", RECIPE, snapshot, Limits()) == ()


def test_invalid_and_mixed_selector_capture_fails_with_safe_error():
    import pytest

    from rastro import ConfigurationError

    for expression in ("[::text", "h2::text, h3::attr(title)"):
        recipe = CssRecipe(record_selector="article", fields={"title": expression})
        result = extract_document(
            BEFORE,
            source=SOURCE,
            schema=Event,
            recipe=RECIPE,
            parser=ScraplingParser(),
            limits=Limits(),
        )
        with pytest.raises(ConfigurationError):
            ScraplingRelocation().capture(
                BEFORE, source=SOURCE, recipe=recipe, result=result, limits=Limits()
            )


def test_relocated_recipe_keeps_historical_fixture_and_can_capture_next_success():
    adapter = ScraplingRelocation()
    (candidate,) = adapter.relocate(AFTER, RECIPE, successful_snapshot(), Limits())
    for html in (BEFORE, AFTER):
        result = extract_document(
            html,
            source=SOURCE,
            schema=Event,
            recipe=candidate.recipe,
            parser=ScraplingParser(),
            limits=Limits(),
        )
        assert result.status == Outcome.SUCCESS
        assert result.records[0].data.title == "Synthetic dance"
        snapshot = adapter.capture(
            html, source=SOURCE, recipe=candidate.recipe, result=result, limits=Limits()
        )
        assert snapshot.fields["title"]


def test_empty_and_unparsable_html_never_exposes_parser_exceptions():
    import pytest

    from rastro import ConfigurationError

    adapter, snapshot = ScraplingRelocation(), successful_snapshot()
    for html in ("   ", "<!--private-comment-->"):
        assert adapter.relocate(html, RECIPE, snapshot, Limits()) == ()
    with pytest.raises(ConfigurationError, match="Invalid relocation HTML"):
        adapter.relocate(
            '<?xml version="1.0" encoding="UTF-8"?><p>private</p>', RECIPE, snapshot, Limits()
        )


def test_success_without_observed_fields_cannot_be_fingerprinted():
    """SCR-002: an inconsistent success report cannot create an empty snapshot."""
    import pytest

    from rastro import ConfigurationError

    result = extract_document(
        BEFORE,
        source=SOURCE,
        schema=Event,
        recipe=RECIPE,
        parser=ScraplingParser(),
        limits=Limits(),
    )
    result = result.model_copy(
        update={"records": (result.records[0].model_copy(update={"evidence": {}}),)}
    )
    with pytest.raises(ConfigurationError, match="observed fields"):
        ScraplingRelocation().capture(
            BEFORE, source=SOURCE, recipe=RECIPE, result=result, limits=Limits()
        )
