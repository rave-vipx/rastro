"""MutationPlan serialization, composition, labels and reproducibility (MOCK-002, MOCK-006)."""

import hashlib
import json
import os
import subprocess
import sys

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from rastro.mocklab import (
    SYNTHETIC_KEY,
    MockLabError,
    SyntheticLabel,
    label_html,
    label_json,
    read_html_label,
)
from rastro.mocklab.mutations import (
    HtmlChangeClasses,
    HtmlDuplicateElement,
    HtmlEmptyContainer,
    HtmlModifyPagination,
    HtmlRemoveField,
    HtmlReorderChildren,
    HtmlWrapElement,
    JsonDuplicateItem,
    JsonModifyPagination,
    JsonRenameKey,
    JsonReorder,
    MutationPlan,
    MutationResult,
    MutationStatus,
)
from rastro.mocklab.mutations.html_dom import HtmlDom, stable
from rastro.mocklab.mutations.rng import non_identity_permutation, sample_indices
from rastro.mocklab.seeds import DeterministicStream

PAGE = (
    "<!doctype html><html><body><main>"
    + "".join(
        f"<article class='card'><h2>T{i}</h2><a href='/p{i}'>x</a></article>" for i in range(6)
    )
    + "</main><nav class='pagination'><a rel='next' href='/2'>Next</a></nav></body></html>"
)
API = json.dumps({"items": [{"id": i, "name": f"n{i}"} for i in range(6)], "next": "c1"})

HTML_PLAN = MutationPlan(
    seed=42,
    operators=(
        HtmlReorderChildren(selector="main"),
        HtmlChangeClasses(selector=".card", hash_suffix=True, max_targets=3),
        HtmlWrapElement(selector="h2", max_targets=2),
        HtmlDuplicateElement(selector="article", max_targets=1),
        HtmlModifyPagination(),
    ),
)


def test_plan_round_trips_through_canonical_json():
    text = HTML_PLAN.to_json()
    restored = MutationPlan.from_json(text)
    assert restored == HTML_PLAN
    assert restored.to_json() == text
    assert restored.fingerprint == HTML_PLAN.fingerprint
    assert json.loads(text)["format_version"] == 1


def test_fingerprint_changes_with_seed_and_operators():
    other_seed = HTML_PLAN.model_copy(update={"seed": 43})
    fewer = MutationPlan(seed=42, operators=HTML_PLAN.operators[:2])
    assert len({HTML_PLAN.fingerprint, other_seed.fingerprint, fewer.fingerprint}) == 3


def test_same_plan_and_input_give_identical_bytes_and_seeds_differ():
    first, second = HTML_PLAN.apply(PAGE), HTML_PLAN.apply(PAGE)
    assert first == second
    other = HTML_PLAN.model_copy(update={"seed": 7}).apply(PAGE)
    assert other.document != first.document


_SCRIPT = (
    "import sys, hashlib; from rastro.mocklab.mutations import MutationPlan;"
    "plan = MutationPlan.from_json(sys.argv[1]);"
    "print(hashlib.sha256(plan.apply(sys.argv[2]).document.encode()).hexdigest())"
)


JSON_PLAN = MutationPlan(
    seed=9,
    operators=(
        JsonReorder(pointer="/items"),
        JsonDuplicateItem(pointer="/items", max_targets=2),
        JsonModifyPagination(field="/next"),
    ),
)


@pytest.mark.parametrize(
    ("plan", "document", "digest"),
    [
        (HTML_PLAN, PAGE, "539e7722f38289a42216ea592334a3a08966d2ee3b8904011ccc94b790475960"),
        (JSON_PLAN, API, "0a500799b0c3eba49b50a7d777fad7fa760787f44eb927cacd1918afeb045b37"),
    ],
)
def test_golden_digests_catch_byte_changes_across_releases(plan, document, digest):
    """Recorded with lxml 6.1.3 and libxml2 2.14.6; a bump that changes bytes fails here."""
    assert hashlib.sha256(plan.apply(document).document.encode()).hexdigest() == digest


def test_output_does_not_depend_on_hash_randomization():
    digests = set()
    for hash_seed in ("0", "1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        command = [sys.executable, "-c", _SCRIPT, HTML_PLAN.to_json(), PAGE]
        output = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        digests.add(output.stdout.strip())
    assert len(digests) == 1


def test_composition_feeds_each_step_the_previous_output():
    result = HTML_PLAN.apply(PAGE)
    assert result.status is MutationStatus.APPLIED
    assert [step.operator for step in result.steps] == [op.operator for op in HTML_PLAN.operators]
    assert all(step.status is MutationStatus.APPLIED for step in result.steps)
    assert result.document.endswith(result.steps[-1].document)
    assert len(HtmlDom.parse(result.document).select("article")) == 7  # type: ignore[union-attr]


def test_editing_one_step_does_not_reseed_the_others():
    base = MutationPlan(seed=5, operators=(HtmlReorderChildren(selector="main"),))
    extended = MutationPlan(
        seed=5, operators=(HtmlReorderChildren(selector="main"), HtmlModifyPagination())
    )
    assert base.apply(PAGE).steps[0] == extended.apply(PAGE).steps[0]


def test_mutated_artifacts_carry_the_mock_lab_label():
    html = HTML_PLAN.apply(PAGE)
    assert html.label == SyntheticLabel(kind="mutation", seed=42)
    assert read_html_label(html.document) == html.label
    plan = MutationPlan(seed=3, operators=(JsonReorder(pointer="/items"),))
    payload = json.loads(plan.apply(API).document)
    assert next(iter(payload)) == SYNTHETIC_KEY
    assert payload[SYNTHETIC_KEY]["kind"] == "mutation"


def test_labeled_inputs_are_relabeled_not_mutated():
    fixture_label = SyntheticLabel(kind="fixture", seed=1)
    html = HTML_PLAN.apply(label_html(PAGE, fixture_label))
    assert html.document == HTML_PLAN.apply(PAGE).document
    rename_all = MutationPlan(
        seed=1, operators=(JsonRenameKey(records="", field=f"/{SYNTHETIC_KEY}", new_key="x"),)
    )
    labeled = json.dumps(label_json(json.loads(API), fixture_label))
    assert rename_all.apply(labeled).steps[0].reason == "target_not_found"
    reordered = MutationPlan(seed=3, operators=(JsonReorder(pointer="/items"),))
    assert reordered.apply(labeled).document == reordered.apply(API).document


def test_a_plan_without_applicable_targets_returns_the_input_unlabeled():
    plan = MutationPlan(seed=1, operators=(HtmlEmptyContainer(selector="table"),))
    result = plan.apply(PAGE)
    assert result.status is MutationStatus.NO_APPLICABLE_TARGET
    assert result.document == PAGE
    assert result.steps[0].reason == "target_not_found"
    assert MutationPlan(seed=1, operators=(JsonReorder(),)).apply("[1").document == "[1"


def test_json_array_roots_cannot_carry_a_label_and_fail_explicitly():
    plan = MutationPlan(seed=1, operators=(JsonDuplicateItem(),))
    with pytest.raises(MockLabError):
        plan.apply("[1, 2]")


def test_result_invariants_reject_silent_outcomes():
    with pytest.raises(ValidationError):
        MutationResult(
            status=MutationStatus.APPLIED,
            operator="x",
            category="missing_fields",
            representation="json",
            document="{}",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"seed": 1, "operators": []},
        {"seed": -1, "operators": [{"operator": "json_reorder"}]},
        {"seed": 2**63, "operators": [{"operator": "json_reorder"}]},
        {"seed": "1", "operators": [{"operator": "json_reorder"}]},
        {"seed": 1, "format_version": 2, "operators": [{"operator": "json_reorder"}]},
        {
            "seed": 1,
            "operators": [
                {"operator": "json_reorder"},
                {"operator": "html_wrap_element", "selector": "p"},
            ],
        },
        {"seed": 1, "operators": [{"operator": "unknown"}]},
    ],
)
def test_invalid_plans_are_rejected(payload):
    with pytest.raises(ValidationError):
        MutationPlan.model_validate_json(json.dumps(payload))


def test_seeded_helpers_are_valid_and_reproducible():
    stream = DeterministicStream(1, "helpers")
    for count in range(2, 9):
        order = non_identity_permutation(stream, count)
        assert sorted(order) == list(range(count)) and order != list(range(count))
    for seed in range(8):
        assert non_identity_permutation(DeterministicStream(seed, "pair"), 2) == [1, 0]
    assert sample_indices(stream, 5, None) == [0, 1, 2, 3, 4]
    picked = sample_indices(stream, 10, 3)
    assert len(picked) == 3 and picked == sorted(set(picked))
    with pytest.raises(ValueError):
        non_identity_permutation(stream, 1)


_OPERATORS = st.sampled_from(
    [
        HtmlReorderChildren(selector="main"),
        HtmlChangeClasses(selector="*", hash_suffix=True, max_targets=2),
        HtmlWrapElement(selector="a", max_targets=2),
        HtmlRemoveField(selector="h2", max_targets=1),
        HtmlDuplicateElement(selector="article", max_targets=2),
        HtmlEmptyContainer(selector="article", max_targets=1),
        HtmlModifyPagination(mode="remove"),
    ]
)


@settings(max_examples=40, deadline=None)
@given(seed=st.integers(0, 2**63 - 1), operators=st.lists(_OPERATORS, min_size=1, max_size=5))
def test_any_plan_output_reparses_and_reproduces(seed, operators):
    plan = MutationPlan(seed=seed, operators=tuple(operators))
    result = plan.apply(PAGE)
    assert result == plan.apply(PAGE)
    body = result.document.split("\n", 1)[1] if result.label else result.document
    assert stable(body)
    if result.status is MutationStatus.APPLIED:
        assert read_html_label(result.document).seed == seed
