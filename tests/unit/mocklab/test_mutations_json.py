"""JSON mutation operators on synthetic API responses (MOCK-002)."""

import json

import pytest
from pydantic import ValidationError

from rastro.domain.sources import ResourceLimitError
from rastro.mocklab.mutations import (
    JsonChangeType,
    JsonDuplicateItem,
    JsonEmptyContainer,
    JsonModifyPagination,
    JsonRemoveField,
    JsonRenameKey,
    JsonReorder,
    JsonWrapValue,
    MutationPlan,
    MutationStatus,
)
from rastro.mocklab.seeds import DeterministicStream

API = json.dumps(
    {
        "data": {
            "items": [
                {"type": "product", "name": "Alpha", "offer": {"price": "1.00"}},
                {"type": "product", "name": "Beta", "offer": {"price": None}},
                {"type": "bundle", "name": "Gamma"},
                "not-a-record",
            ]
        },
        "meta": {"page": 1, "next": "abc", "done": True},
    }
)


def stream() -> DeterministicStream:
    return DeterministicStream(7, "test")


def apply(operator, document: str = API):
    result = operator.apply(document, stream())
    return result, json.loads(result.document)


def names(payload) -> list[str]:
    return [item["name"] for item in payload["data"]["items"] if isinstance(item, dict)]


def test_reorder_permutes_arrays_and_object_keys():
    result, payload = apply(JsonReorder(pointer="/data/items"))
    assert sorted(names(payload)) == ["Alpha", "Beta", "Gamma"]
    assert payload["data"]["items"] != json.loads(API)["data"]["items"]
    assert result.changes[0].before == "items=4"
    result, payload = apply(JsonReorder(pointer="/meta"))
    assert list(payload["meta"]) != ["page", "next", "done"]
    assert result.changes[0].before == "page,next,done"


def test_change_type_maps_discriminators():
    result, payload = apply(
        JsonChangeType(records="/data/items", field="/type", mapping={"product": "item"})
    )
    assert [item.get("type") for item in payload["data"]["items"][:3]] == ["item", "item", "bundle"]
    assert [c.path_before for c in result.changes] == ["/data/items/0/type", "/data/items/1/type"]
    assert result.changes[0].before == '"product"' and result.changes[0].after == '"item"'


def test_wrap_value_nests_the_field():
    result, payload = apply(JsonWrapValue(records="/data/items", field="/name", max_targets=1))
    (change,) = result.changes
    wrapped = [item for item in payload["data"]["items"][:3] if isinstance(item["name"], dict)]
    assert len(wrapped) == 1 and set(wrapped[0]["name"]) == {"value"}
    assert change.path_after == change.path_before + "/value"


def test_remove_field_deletes_or_nulls_nested_fields():
    result, payload = apply(JsonRemoveField(records="/data/items", field="/offer/price"))
    assert payload["data"]["items"][0]["offer"] == {}
    assert [c.path_after for c in result.changes] == [None, None]
    assert result.changes[0].after is None
    result, payload = apply(
        JsonRemoveField(records="/data/items", field="/offer/price", mode="null")
    )
    assert [c.path_before for c in result.changes] == ["/data/items/0/offer/price"]
    assert result.changes[0].after == "null"


def test_rename_key_keeps_key_order_and_skips_collisions():
    result, payload = apply(JsonRenameKey(records="/data/items", field="/name", new_key="title"))
    assert list(payload["data"]["items"][0]) == ["type", "title", "offer"]
    assert result.changes[0].path_after == "/data/items/0/title"
    collision = JsonRenameKey(records="/data/items", field="/name", new_key="type")
    assert collision.apply(API, stream()).reason == "no_eligible_target"


@pytest.mark.parametrize(
    ("operator", "field", "expected"),
    [
        (
            JsonModifyPagination(field="/page", records="/meta", mode="shift_page", offset=2),
            "page",
            3,
        ),
        (JsonModifyPagination(field="/next", records="/meta", mode="end"), "next", None),
        (
            JsonModifyPagination(field="/next", records="/meta", cursor_template="c-{token}"),
            "next",
            "c-",
        ),
    ],
)
def test_modify_pagination_modes(operator, field, expected):
    result, payload = apply(operator)
    value = payload["meta"][field]
    if isinstance(expected, str):
        assert value.startswith(expected) and len(value) == len(expected) + 8
    else:
        assert value == expected
    assert result.changes[0].path_before == f"/meta/{field}"


@pytest.mark.parametrize(
    "operator",
    [
        JsonModifyPagination(records="/meta", field="/next", mode="shift_page"),
        JsonModifyPagination(records="/meta", field="/done", mode="shift_page"),
        JsonModifyPagination(records="/meta", field="/next", cursor_template="abc"),
        JsonModifyPagination(records="/meta", field="/next", mode="shift_page", offset=0),
    ],
)
def test_pagination_without_a_real_change_is_explicit(operator):
    assert operator.apply(API, stream()).reason == "no_eligible_target"


def test_duplicate_item_copies_sampled_items():
    result, payload = apply(JsonDuplicateItem(pointer="/data/items", copies=2, max_targets=2))
    assert len(payload["data"]["items"]) == 8
    assert [c.after for c in result.changes] == ["count=3", "count=3"]
    assert JsonDuplicateItem(pointer="/meta").apply(API, stream()).reason == "no_eligible_target"


def test_empty_container_clears_and_then_reports_nothing_to_do():
    result, payload = apply(JsonEmptyContainer(pointer="/data/items"))
    assert payload["data"]["items"] == []
    assert result.changes[0].before == "size=4"
    again = JsonEmptyContainer(pointer="/data/items").apply(result.document, stream())
    assert again.reason == "no_eligible_target"


@pytest.mark.parametrize(
    ("operator", "document", "reason"),
    [
        (JsonEmptyContainer(pointer="/meta"), "", "empty_document"),
        (JsonEmptyContainer(pointer="/meta"), "{bad", "invalid_document"),
        (JsonEmptyContainer(pointer="/meta"), '{"a":NaN}', "invalid_document"),
        (JsonEmptyContainer(pointer="/missing"), API, "target_not_found"),
        (JsonEmptyContainer(pointer="/meta/page"), API, "target_not_found"),
        (JsonRemoveField(records="/data/items", field="/sku"), API, "target_not_found"),
        (JsonRemoveField(field="/sku"), "nope", "invalid_document"),
        (JsonRemoveField(records="/meta/page", field="/x"), API, "target_not_found"),
        (JsonReorder(pointer="/data/items/0/offer"), API, "no_eligible_target"),
    ],
)
def test_nothing_to_mutate_is_explicit(operator, document, reason):
    result = operator.apply(document, stream())
    assert result.status is MutationStatus.NO_APPLICABLE_TARGET
    assert (result.reason, result.document, result.changes) == (reason, document, ())


@pytest.mark.parametrize(
    "document",
    [
        '{"a":' * 40 + "1" + "}" * 40,
        "[" * 100_000 + "]" * 100_000,
        json.dumps(list(range(100_001))),
        json.dumps(["x" * 2_000_001]),
    ],
)
def test_oversized_json_is_refused(document):
    with pytest.raises(ResourceLimitError):
        JsonEmptyContainer().apply(document, stream())


@pytest.mark.parametrize(
    "config",
    [
        {"operator": "json_remove_field", "field": ""},
        {"operator": "json_remove_field", "field": "name"},
        {"operator": "json_remove_field", "field": "/a~2"},
        {"operator": "json_empty_container", "pointer": "data"},
        {"operator": "json_change_type", "field": "/type", "mapping": {}},
        {"operator": "json_rename_key", "field": "/a", "new_key": ""},
    ],
)
def test_invalid_configs_are_rejected(config):
    with pytest.raises(ValidationError):
        MutationPlan.model_validate_json(json.dumps({"seed": 1, "operators": [config]}))


def test_a_valid_config_passes_the_same_validation():
    valid = {"operator": "json_remove_field", "field": "/name"}
    plan = MutationPlan.model_validate_json(json.dumps({"seed": 1, "operators": [valid]}))
    assert plan.operators[0].operator == valid["operator"]
