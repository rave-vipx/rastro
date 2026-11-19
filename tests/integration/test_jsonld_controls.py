"""INT-002, N17: tolerate raw string controls without hiding malformed JSON-LD."""

import json

import pytest
from pydantic import BaseModel

from rastro import JsonLdRecipe, Outcome, Rastro, Source


class Event(BaseModel):
    name: str
    description: str


def extract(raw):
    return Rastro().extract_html(
        f'<script type="application/ld+json">{raw}</script>',
        source=Source(url="https://events.example/"),
        schema=Event,
        recipe=JsonLdRecipe(
            item_type="Event", fields={"name": "/name", "description": "/description"}
        ),
    )


@pytest.mark.parametrize("control", ["\t", "\n"])
def test_raw_string_controls_are_preserved_with_visible_warning(control):
    """INT-002, N17, N23: portable HTML controls retain values, provenance and a warning."""
    # libxml2 builds differ on other controls and CR; cover those before HTML parsing.
    description = 'one "quoted" \\ path' + control + "two"
    raw = json.dumps({"@type": "Event", "name": "A", "description": description})
    raw = raw.replace(json.dumps(control)[1:-1], control)
    result = extract(raw)
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert result.records[0].data.description == description
    assert result.records[0].evidence["description"].locator == "jsonld[0]#/description"
    assert [i.code for i in result.issues] == ["repaired_json"]
    assert result.issues[0].message is None


@pytest.mark.parametrize(
    "raw",
    [
        '{"@type":"Event","name":"A","description":"one\ntwo"',
        '{"@type":"Event","name":"A","name":"B","description":"one\ntwo"}',
        '{"@type":"Event","name":"A","description":"one\ntwo","x":NaN}',
        '{"@type":"Event","name":"A","description":"one\\\ntwo"}',
    ],
)
def test_tolerance_does_not_repair_structure_or_relax_other_validation(raw):
    """INT-002: broken structure, escapes, duplicate keys and NaN still fail closed."""
    result = extract(raw)
    assert result.status == Outcome.INVALID_DATA
    assert result.records == ()
    assert [i.code for i in result.issues] == ["invalid_jsonld"]


def test_valid_jsonld_keeps_structural_whitespace_and_needs_no_warning():
    """INT-002: strict JSON-LD remains unchanged."""
    result = extract('{\n"@type":"Event",\t"name":"A", "description":"one\\ntwo"\n}')
    assert result.status == Outcome.SUCCESS
    assert result.records[0].data.description == "one\ntwo"
    assert result.issues == ()
