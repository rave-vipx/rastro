"""Lone UTF-16 surrogates never fail a whole source (DATA-004, N3).

JSON decoding replaces them with U+FFFD; a record that still holds unstorable text is
rejected on its own as ``invalid_text``.
"""

from pydantic import BaseModel

from rastro import CssRecipe, Limits, Outcome, Source
from rastro.application.extraction import extract_document
from rastro.domain.json import strict_json
from rastro.ports.parser import Candidate, ParsedDocument


def test_strict_json_replaces_lone_surrogates_and_keeps_valid_pairs():
    raw = '{"a": "x \\ud83d", "\\ude00k": ["\\ud83d\\ude00", ["\\udc00y"], {"b": "\\ud800"}]}'
    assert strict_json(raw, repair_text=True) == {
        "a": "x �",
        "�k": ["\U0001f600", ["�y"], {"b": "�"}],
    }
    assert strict_json('"\\ud83d"', repair_text=True) == "�"
    assert strict_json(b'["\\udfff", 1]', repair_text=True) == ["�", 1]
    assert strict_json('"\\ud83d"') == "\ud83d"  # strict callers (Mock Lab) still refuse it


class Item(BaseModel):
    name: str


class _Parser:
    def parse(self, html, recipe, limits):
        candidates = tuple(
            Candidate(values={"name": name}, locators={"name": "li::text"})
            for name in ("Fiesta \ud83d", "Salsa")
        )
        return ParsedDocument(candidates, ())


def test_an_unstorable_record_is_rejected_alone_with_a_reason_code():
    result = extract_document(
        "<ul></ul>",
        source=Source(url="https://example.com/"),
        schema=Item,
        recipe=CssRecipe(record_selector="li", fields={"name": "li::text"}),
        parser=_Parser(),
        limits=Limits(),
    )
    assert [r.data.name for r in result.records] == ["Salsa"]
    assert [(i.code, i.record_index) for i in result.issues] == [("invalid_text", 0)]
    assert result.metrics.rejected == 1
    assert result.status == Outcome.PARTIAL_SUCCESS
