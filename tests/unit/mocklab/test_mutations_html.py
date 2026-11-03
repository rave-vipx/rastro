"""HTML mutation operators on synthetic pages (MOCK-002)."""

import json

import pytest
from pydantic import ValidationError

from rastro.domain.sources import ResourceLimitError
from rastro.mocklab.mutations import (
    HtmlChangeClasses,
    HtmlChangeLabel,
    HtmlDuplicateElement,
    HtmlEmptyContainer,
    HtmlModifyPagination,
    HtmlRemoveField,
    HtmlReorderChildren,
    HtmlWrapElement,
    MutationPlan,
    MutationStatus,
)
from rastro.mocklab.mutations.html_dom import HtmlDom
from rastro.mocklab.seeds import DeterministicStream

PAGE = (
    "<html><body><ul id='items'>"
    "<li class='item card'><span class='name'>Alpha</span><b data-price='1'>1</b></li>"
    "<li class='item'><span class='name'>Beta</span><b data-price='2'>2</b></li>"
    "<li class='item'><span class='name'>Gamma</span></li>"
    "</ul><dl><dt aria-label='Price'>Price</dt></dl>"
    "<nav class='pagination'><a rel='next' href='/page/2'>Next</a></nav></body></html>"
)


def stream() -> DeterministicStream:
    return DeterministicStream(7, "test")


def texts(document: str, selector: str) -> list[str]:
    dom = HtmlDom.parse(document)
    assert dom is not None
    return [node.text_content() for node in dom.select(selector)]


def test_reorder_moves_children_and_reports_the_permutation():
    result = HtmlReorderChildren(selector="#items").apply(PAGE, stream())
    assert result.status is MutationStatus.APPLIED
    assert sorted(texts(result.document, ".name")) == ["Alpha", "Beta", "Gamma"]
    assert texts(result.document, ".name") != ["Alpha", "Beta", "Gamma"]
    (change,) = result.changes
    assert change.path_before == change.path_after == "/html/body/ul"
    assert change.before == "0:li,1:li,2:li"
    assert change.after is not None and change.after != change.before
    assert sorted(change.after.split(",")) == ["0:li", "1:li", "2:li"]


def test_change_classes_renames_removes_adds_and_hashes():
    renamed = HtmlChangeClasses(selector="li", rename={"item": "row"}, remove=("card",))
    result = renamed.apply(PAGE, stream())
    assert [c.after for c in result.changes] == ["row", "row", "row"]
    assert result.changes[0].before == "item card"
    hashed = HtmlChangeClasses(selector=".name", hash_suffix=True, max_targets=1)
    (change,) = hashed.apply(PAGE, stream()).changes
    assert change.before == "name"
    assert change.after is not None and change.after.startswith("name-")


def test_removing_every_class_drops_the_attribute():
    result = HtmlChangeClasses(selector=".name", remove=("name",)).apply(PAGE, stream())
    assert result.changes[0].after == ""
    assert texts(result.document, "span[class]") == []


def test_change_classes_without_any_effect_is_explicit():
    result = HtmlChangeClasses(selector="b", remove=("absent",)).apply(PAGE, stream())
    assert result.status is MutationStatus.NO_APPLICABLE_TARGET
    assert result.reason == "no_eligible_target"
    assert result.document == PAGE and result.changes == ()


def test_wrap_element_nests_the_target():
    result = HtmlWrapElement(selector=".name", wrapper_class="w", max_targets=1).apply(
        PAGE, stream()
    )
    (change,) = result.changes
    assert change.path_after is not None and change.path_after.endswith("div/span")
    assert len(HtmlDom.parse(result.document).select("div.w > span.name")) == 1  # type: ignore[union-attr]


def test_wrap_refuses_document_level_elements():
    result = HtmlWrapElement(selector="body").apply(PAGE, stream())
    assert result.reason == "no_eligible_target"


def test_remove_field_drops_elements_or_attributes():
    removed = HtmlRemoveField(selector="b").apply(PAGE, stream())
    assert texts(removed.document, "b") == []
    assert all(c.path_after is None for c in removed.changes)
    stripped = HtmlRemoveField(selector="b", attribute="data-price").apply(PAGE, stream())
    assert [c.before for c in stripped.changes] == ["data-price=1", "data-price=2"]
    assert texts(stripped.document, "b[data-price]") == []


def test_remove_field_attribute_absent_is_explicit():
    result = HtmlRemoveField(selector="span", attribute="data-price").apply(PAGE, stream())
    assert result.reason == "no_eligible_target"


def test_duplicate_element_inserts_copies_after_the_original():
    result = HtmlDuplicateElement(selector="li", copies=2, max_targets=1).apply(PAGE, stream())
    assert len(texts(result.document, "li")) == 5
    (change,) = result.changes
    assert change.after is not None and change.after.count("/html/body/ul/li") == 2


def test_empty_container_removes_children_and_skips_empty_ones():
    result = HtmlEmptyContainer(selector="ul").apply(PAGE, stream())
    assert texts(result.document, "li") == []
    assert result.changes[0].before == "children=3,text=False"
    assert result.changes[0].after == "children=0,text=False"
    again = HtmlEmptyContainer(selector="ul").apply(result.document, stream())
    assert again.reason == "no_eligible_target"


def test_change_label_text_attribute_and_match():
    text = HtmlChangeLabel(selector="dt", text="Cost").apply(PAGE, stream())
    assert texts(text.document, "dt") == ["Cost"]
    attr = HtmlChangeLabel(selector="dt", attribute="aria-label", text="Cost").apply(PAGE, stream())
    assert attr.changes[0].before == "Price" and attr.changes[0].after == "Cost"
    partial = HtmlChangeLabel(selector=".name", match="ph", text="f").apply(PAGE, stream())
    assert texts(partial.document, ".name") == ["Alfa", "Beta", "Gamma"]
    missing = HtmlChangeLabel(selector="dt", attribute="title", text="x").apply(PAGE, stream())
    assert missing.reason == "no_eligible_target"


@pytest.mark.parametrize(
    ("mode", "after"),
    [("rewrite_href", "href=?cursor="), ("disable", "aria-disabled=true"), ("remove", None)],
)
def test_modify_pagination_modes(mode, after):
    result = HtmlModifyPagination(mode=mode).apply(PAGE, stream())
    (change,) = result.changes
    assert change.before == "href=/page/2"
    if after is None:
        assert change.after is None and change.path_after is None
    else:
        assert change.after is not None and change.after.startswith(after)


def test_fixed_pagination_href_equal_to_current_is_not_a_change():
    operator = HtmlModifyPagination(href_template="/page/2")
    assert operator.apply(PAGE, stream()).reason == "no_eligible_target"


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        ("", "empty_document"),
        ("  \n", "empty_document"),
        ("<!-- only a comment -->", "empty_document"),
        ('<?xml version="1.0"?>', "empty_document"),
        (PAGE, "target_not_found"),
    ],
)
def test_nothing_to_mutate_is_explicit(document, reason):
    result = HtmlEmptyContainer(selector="table").apply(document, stream())
    assert result.status is MutationStatus.NO_APPLICABLE_TARGET
    assert (result.reason, result.document) == (reason, document)


def test_elements_removed_with_an_ancestor_are_skipped():
    nested = "<div class='x'><div class='x'>a</div></div>"
    result = HtmlRemoveField(selector=".x").apply(nested, stream())
    assert len(result.changes) == 1


def test_oversized_input_is_refused():
    with pytest.raises(ResourceLimitError):
        HtmlEmptyContainer(selector="p").apply("<p>" + "x" * 2_000_001, stream())


@pytest.mark.parametrize(
    "config",
    [
        {"operator": "html_empty_container", "selector": "  "},
        {"operator": "html_empty_container", "selector": "a[["},
        {"operator": "html_empty_container", "selector": "p", "max_targets": 0},
        {"operator": "html_change_classes", "selector": "p"},
        {"operator": "html_change_classes", "selector": "p", "add": ["two words"]},
        {"operator": "html_wrap_element", "selector": "p", "wrapper_tag": "DIV>"},
        {"operator": "html_remove_field", "selector": "p", "attribute": "on click"},
        {"operator": "html_empty_container", "selector": "p", "unknown": 1},
    ],
)
def test_invalid_configs_are_rejected(config):
    with pytest.raises(ValidationError):
        MutationPlan.model_validate_json(json.dumps({"seed": 1, "operators": [config]}))


def test_a_valid_config_passes_the_same_validation():
    valid = {"operator": "html_empty_container", "selector": "p"}
    plan = MutationPlan.model_validate_json(json.dumps({"seed": 1, "operators": [valid]}))
    assert plan.operators[0].operator == valid["operator"]
