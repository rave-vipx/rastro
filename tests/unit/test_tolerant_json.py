"""Tolerant decoding of page JSON; config and fixture scrubbing stay strict."""

import json

import pytest
from pydantic import BaseModel

from rastro import Outcome, Rastro, Source
from rastro.adapters.jsonld_objects import jsonld_objects
from rastro.adapters.scrapling import ScraplingParser, _jsonld_payload
from rastro.application.job_loader import parse_job
from rastro.domain.models import EmbeddedJsonRecipe, JsonLdRecipe, Limits
from rastro.domain.tolerant_json import tolerant_json
from rastro.mocklab.json_scrubber import scrub_json
from rastro.mocklab.seeds import DeterministicStream


class _Product(BaseModel):
    name: str


def test_trailing_comma_jsonld_repaired():
    issues = []
    assert _jsonld_payload('{"a":1,}', issues) == {"a": 1}
    assert [issue.code for issue in issues] == ["repaired_json"]


def test_trailing_commas_in_nested_containers_and_strings_untouched():
    payload, repaired = tolerant_json('{"a": [1, 2, ], "b": "x,}", }')
    assert payload == {"a": [1, 2], "b": "x,}"}
    assert repaired


def test_valid_json_is_not_flagged():
    assert tolerant_json('{"a": [1, 2]}') == ({"a": [1, 2]}, False)


def test_bom_embedded_json_repaired():
    html = '<div id="d" data-x=\'﻿{"items": [{"n": "A"}]}\'></div>'
    recipe = EmbeddedJsonRecipe(source="#d::attr(data-x)", record_path="/items", fields={"n": "/n"})
    parsed = ScraplingParser().parse(html, recipe, Limits())
    assert parsed.candidates[0].values == {"n": "A"}
    assert [i.code for i in parsed.issues] == ["repaired_json"]
    assert tolerant_json("﻿[1]") == ([1], True)


def test_duplicate_keys_still_rejected():
    with pytest.raises(ValueError):
        tolerant_json('{"a":1,"a":2,}')
    with pytest.raises(ValueError):
        tolerant_json('{"a":NaN}')
    with pytest.raises(ValueError):
        tolerant_json('{"a":1,,}')


def test_mocklab_refuses_repaired_input():
    with pytest.raises(ValueError):
        json.loads('{"a":1,}')
    # The scrubber only ever sees decoded strict JSON; the tolerant decoder is not used there.
    import rastro.mocklab.json_scrubber as scrubber

    assert "tolerant_json" not in vars(scrubber)
    assert scrub_json({"a": 1}, frozenset({"/a"}), DeterministicStream(0, "t")) == {"a": 1}


def test_itemlist_traversed():
    items = [{"@type": "Product", "name": f"P{i}"} for i in range(3)]
    raw = json.dumps(
        {
            "@type": "ItemList",
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "item": item}
                for i, item in enumerate(items)
            ],
        }
    )
    objects = list(jsonld_objects(json.loads(raw)))
    assert [obj["name"] for obj, _ in objects] == ["P0", "P1", "P2"]
    assert objects[1][1] == "/itemListElement/1/item"
    result = Rastro().extract_html(
        f'<script type="application/ld+json">{raw}</script>',
        source=Source(url="https://shop.example/"),
        schema=_Product,
        recipe=JsonLdRecipe(item_type="Product", fields={"name": "/name"}),
    )
    assert result.status == Outcome.SUCCESS
    assert [r.data.name for r in result.records] == ["P0", "P1", "P2"]


def test_config_json_stays_strict():
    job = b'{"format_version": 1,}'
    with pytest.raises(ValueError):
        parse_job(job, format="json")
