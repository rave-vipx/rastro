"""Regressions from the PR #48 review, each with the failing input it reported (MOCK-002)."""

import json

import pytest
from lxml import html as lxml_html  # type: ignore[import-untyped]
from pydantic import ValidationError

from rastro.domain.sources import ResourceLimitError
from rastro.mocklab import MockLabError
from rastro.mocklab.mutations import (
    HtmlChangeClasses,
    HtmlChangeLabel,
    HtmlDuplicateElement,
    HtmlEmptyContainer,
    HtmlRemoveField,
    HtmlWrapElement,
    JsonDuplicateItem,
    JsonEmptyContainer,
    JsonReorder,
    JsonWrapValue,
    MutationPlan,
    MutationStatus,
)
from rastro.mocklab.seeds import DeterministicStream

LIST = "<ul><li>a</li><li>b</li><li>c</li></ul>"
SPANS = "<div><span>a</span><span>b</span><span>c</span></div>"


def stream() -> DeterministicStream:
    return DeterministicStream(1, "review")


def paths(changes, attribute: str) -> list[str | None]:
    return [getattr(change, attribute) for change in changes]


def test_removing_sibling_targets_reports_their_input_paths():
    changes = HtmlRemoveField(selector="li").apply(LIST, stream()).changes
    assert paths(changes, "path_before") == [f"/html/body/ul/li[{i}]" for i in (1, 2, 3)]
    assert paths(changes, "path_after") == [None, None, None]


def test_duplicating_sibling_targets_reports_input_and_output_paths():
    changes = HtmlDuplicateElement(selector="li").apply(LIST, stream()).changes
    assert paths(changes, "path_before") == [f"/html/body/ul/li[{i}]" for i in (1, 2, 3)]
    assert paths(changes, "path_after") == [f"/html/body/ul/li[{i}]" for i in (1, 3, 5)]
    assert paths(changes, "after") == [f"copies=/html/body/ul/li[{i}]" for i in (2, 4, 6)]


def test_wrapping_sibling_targets_reports_final_wrapper_paths():
    changes = HtmlWrapElement(selector="span").apply(SPANS, stream()).changes
    assert paths(changes, "path_before") == [f"/html/body/div/span[{i}]" for i in (1, 2, 3)]
    assert paths(changes, "path_after") == [f"/html/body/div/div[{i}]/span" for i in (1, 2, 3)]


def test_sampled_targets_point_at_the_nodes_they_describe():
    page = "<ol>" + "".join(f"<li>item {i}</li>" for i in range(8)) + "</ol>"
    result = HtmlChangeLabel(selector="li", text="x", max_targets=3).apply(page, stream())
    before = lxml_html.document_fromstring(page).getroottree()
    after = lxml_html.document_fromstring(result.document).getroottree()
    assert len(result.changes) == 3
    for change in result.changes:
        (original,) = before.xpath(change.path_before)
        (mutated,) = after.xpath(change.path_after)
        assert (original.text, mutated.text) == (change.before, "x")


@pytest.mark.parametrize("tag", ["p", "a", "li", "td", "DIV"])
def test_wrappers_that_parsers_would_undo_are_rejected(tag):
    with pytest.raises(ValidationError):
        HtmlWrapElement(selector="div.k", wrapper_tag=tag)


def test_elements_with_a_prescribed_parent_are_not_wrapped():
    table = "<table><tr><td>a</td></tr></table>"
    assert HtmlWrapElement(selector="td").apply(table, stream()).reason == "no_eligible_target"


def test_an_edit_libxml2_would_repair_is_reported_unstable():
    document = "<p>x<span>a</span></p>"
    result = HtmlWrapElement(selector="p > span").apply(document, stream())
    assert result.status is MutationStatus.NO_APPLICABLE_TARGET
    assert (result.reason, result.document) == ("unstable_markup", document)


def test_xhtml_xml_declaration_is_accepted():
    document = '<?xml version="1.0" encoding="utf-8"?><p>a</p>'
    assert HtmlEmptyContainer(selector="p").apply(document, stream()).changes


def test_unparsable_encoding_declarations_raise_mock_lab_error():
    document = '<?xml version="1.0"?><?xml version="1.0" encoding="utf-8"?><p>a</p>'
    with pytest.raises(MockLabError):
        HtmlEmptyContainer(selector="p").apply(document, stream())


@pytest.mark.parametrize(
    ("operator", "document"),
    [
        (HtmlEmptyContainer(selector="p"), "<p>\ud800</p>"),
        (JsonEmptyContainer(), '{"a": "\ud800"}'),
    ],
)
def test_lone_surrogates_in_input_raise_mock_lab_error(operator, document):
    with pytest.raises(MockLabError):
        operator.apply(document, stream())


def test_json_escapes_that_decode_to_lone_surrogates_raise_mock_lab_error():
    plan = MutationPlan(seed=1, operators=(JsonReorder(pointer="/a"),))
    with pytest.raises(MockLabError):
        plan.apply('{"a":["\\ud800",1]}')


def test_output_beyond_the_limits_is_refused():
    plan = MutationPlan(
        seed=1, operators=(JsonDuplicateItem(pointer="/x", copies=20, max_targets=None),)
    )
    with pytest.raises(ResourceLimitError):
        plan.apply(json.dumps({"x": list(range(50_000))}))


def test_html_output_beyond_the_byte_limit_is_refused():
    page = "<div>" + "<p>" + "x" * 99_000 + "</p></div>"
    plan = MutationPlan(seed=1, operators=(HtmlDuplicateElement(selector="p", copies=20),))
    with pytest.raises(ResourceLimitError):
        plan.apply(page)


def test_leading_and_trailing_comments_are_kept():
    document = "<!-- lead --><p>a</p><!-- tail -->"
    output = HtmlEmptyContainer(selector="p").apply(document, stream()).document
    assert output.startswith("<!-- lead -->") and "<!-- tail -->" in output


def test_emptying_text_only_elements_reports_the_text():
    (change,) = HtmlEmptyContainer(selector="p").apply("<p>hello</p>", stream()).changes
    assert (change.before, change.after) == ("children=0,text=True", "children=0,text=False")


def test_unrelated_class_renames_leave_duplicate_tokens_alone():
    operator = HtmlChangeClasses(selector="p", rename={"q": "r"})
    assert operator.apply('<p class="x x">a</p>', stream()).reason == "no_eligible_target"


def test_uppercase_attribute_names_are_rejected():
    with pytest.raises(ValidationError):
        HtmlRemoveField(selector="p", attribute="ID")


def test_json_wrap_records_the_wrapper_object():
    (change,) = JsonWrapValue(field="/a").apply('{"a":1}', stream()).changes
    assert (change.before, change.after) == ("1", '{"value":1}')


@pytest.mark.parametrize(
    "config",
    [
        {"operator": "json_rename_key", "field": "/a", "new_key": "__rastro_synthetic__"},
        {"operator": "json_wrap_value", "field": "/a", "wrapper_key": "__rastro_synthetic__"},
    ],
)
def test_the_reserved_label_key_is_rejected_at_configuration(config):
    with pytest.raises(ValidationError):
        MutationPlan.model_validate_json(json.dumps({"seed": 1, "operators": [config]}))


def test_no_label_when_nothing_applied():
    result = MutationPlan(seed=1, operators=(HtmlEmptyContainer(selector="table"),)).apply(LIST)
    assert result.status is MutationStatus.NO_APPLICABLE_TARGET
    assert (result.label, result.document) == (None, LIST)


def test_plan_contracts_are_exported_from_mocklab():
    import rastro.mocklab
    import rastro.mocklab.mutations

    for name in ("MutationChange", "MutationPlan", "MutationPlanResult", "MutationResult"):
        assert getattr(rastro.mocklab, name) is getattr(rastro.mocklab.mutations, name)
        assert name in rastro.mocklab.__all__
