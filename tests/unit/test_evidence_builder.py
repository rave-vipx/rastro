from datetime import UTC, datetime

import pytest

from rastro.application.extraction import _evidence_builder
from rastro.domain.models import Evidence

OBSERVED = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("method", ["jsonld", "css", "json"])
def test_cloned_evidence_is_equivalent_to_validated_evidence(method):
    build = _evidence_builder(
        source_url="https://example.com/p", observed_at=OBSERVED, method=method, recipe_version=3
    )
    clone = build("/name")
    validated = Evidence(
        source_url="https://example.com/p",
        observed_at=OBSERVED,
        method=method,
        locator="/name",
        recipe_version=3,
    )
    assert clone == validated
    assert type(clone) is Evidence
    assert clone.model_fields_set == validated.model_fields_set
    assert clone.model_dump_json() == validated.model_dump_json()
    assert clone.model_copy(update={"locator": "/sku"}).locator == "/sku"
    assert hash(clone) == hash(validated)


def test_clones_do_not_share_mutable_state():
    build = _evidence_builder(
        source_url="https://example.com/p", observed_at=OBSERVED, method="css", recipe_version=1
    )
    first, second = build("a::text"), build("b::text")
    assert (first.locator, second.locator) == ("a::text", "b::text")
    assert first.model_fields_set is not second.model_fields_set


def test_non_string_locators_are_validated():
    build = _evidence_builder(
        source_url="https://example.com/p", observed_at=OBSERVED, method="css", recipe_version=1
    )
    with pytest.raises(ValueError):
        build(123)  # type: ignore[arg-type]
