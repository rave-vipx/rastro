"""Engine map entries: how each engine feature reaches users, or why it does not."""

import pytest
from pydantic import ValidationError

from rastro.domain.engine_map import EngineFeature, planned_features


def feature(**changes: object) -> EngineFeature:
    values: dict[str, object] = {
        "engine": "scrapling",
        "area": "browser",
        "name": "PlaywrightSession.block_ads",
        "surface": "job_key",
        "where": "[acquisition.browser] block_ads",
    }
    return EngineFeature.model_validate(values | changes)


def test_a_mapped_feature_names_where_it_is_exposed():
    assert feature().where == "[acquisition.browser] block_ads"
    with pytest.raises(ValidationError):
        feature(where="")


def test_not_exposed_requires_a_reason():
    with pytest.raises(ValidationError):
        feature(surface="not_exposed", where="")
    kept = feature(surface="not_exposed", where="", reason="Pickled checkpoints are unsafe.")
    assert kept.reason


def test_internal_and_planned_need_no_location():
    assert feature(surface="internal", where="").surface == "internal"
    assert feature(surface="planned", where="").surface == "planned"


def test_names_are_dotted_identifiers():
    assert feature(name="templates.shopify").name == "templates.shopify"
    for name in ("", "has space", "a" * 129, "1starts.with.digit"):
        with pytest.raises(ValidationError):
            feature(name=name)


def test_engine_and_area_are_closed_vocabularies():
    with pytest.raises(ValidationError):
        feature(engine="other")
    with pytest.raises(ValidationError):
        feature(area="teleport")


def test_planned_features_lists_what_blocks_a_release():
    rows = (feature(), feature(name="Spider.pause", surface="planned", where=""))
    assert [row.name for row in planned_features(rows)] == ["Spider.pause"]
