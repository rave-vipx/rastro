import pytest
from pydantic import ValidationError

from rastro.mocklab import (
    SYNTHETIC_KEY,
    MockLabError,
    SyntheticLabel,
    label_html,
    label_json,
    read_html_label,
    read_json_label,
)

LABEL = SyntheticLabel(kind="fixture", seed=11)


def test_html_label_round_trips_and_leads_the_document():
    body = label_html("<p>x</p>", LABEL)
    assert body.startswith("<!-- rastro-synthetic {")
    assert '"synthetic":true' in body.splitlines()[0]
    assert read_html_label(body) == LABEL


@pytest.mark.parametrize(
    "body",
    ["<p>x</p>", '<!-- rastro-synthetic {"kind":"other","seed":1} -->\n<p></p>'],
)
def test_unlabeled_or_invalid_html_is_rejected(body):
    with pytest.raises(MockLabError):
        read_html_label(body)


def test_json_label_comes_first_and_round_trips():
    payload = label_json({"data": 1}, LABEL)
    assert next(iter(payload)) == SYNTHETIC_KEY
    assert read_json_label(payload) == LABEL


@pytest.mark.parametrize("payload", [[1, 2], {SYNTHETIC_KEY: {}}])
def test_json_label_requires_an_unused_object_root(payload):
    with pytest.raises(MockLabError):
        label_json(payload, LABEL)


@pytest.mark.parametrize("payload", [[], {"data": 1}, {SYNTHETIC_KEY: {"synthetic": False}}])
def test_missing_or_invalid_json_labels_are_rejected(payload):
    with pytest.raises(MockLabError):
        read_json_label(payload)


@pytest.mark.parametrize("data", [{"kind": "fixture", "seed": -1}, {"kind": "x", "seed": 1}])
def test_label_is_strict(data):
    with pytest.raises(ValidationError):
        SyntheticLabel.model_validate(data)
    with pytest.raises(ValidationError):
        SyntheticLabel(kind="fixture", seed=1, synthetic=False)  # type: ignore[arg-type]
