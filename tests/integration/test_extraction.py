import json
from decimal import Decimal

import pytest
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from rastro import ConfigurationError, Limits, Outcome, Rastro, Recipe, ResourceLimitError, Source


def ld(value):
    return '<script type="application/ld+json">' + json.dumps(value) + "</script>"


def test_jsonld_end_to_end_with_provenance(html, source, schema, recipe):
    result = Rastro().extract_html(html, source=source, schema=schema, recipe=recipe)
    assert result.status == Outcome.SUCCESS
    assert [r.data.name for r in result.records] == ["Notebook", "Pen"]
    assert result.records[0].data.price == Decimal("12.50")
    evidence = result.records[0].evidence["price"]
    assert evidence.locator == "jsonld[0]#/@graph/0/offers/price"
    assert evidence.source_url == "https://example.com/products"
    assert evidence.recipe_version == 1
    assert "currency" not in result.records[0].evidence  # Schema default is not source evidence.
    assert result.locale == "es-UY"
    assert result.metrics.accepted == result.metrics.candidates == 2
    assert result.metrics.rejected == 0
    assert result.started_at <= result.completed_at
    assert result.metrics.total_seconds >= result.metrics.validation_seconds >= 0
    assert result.metrics.extraction_seconds >= 0
    assert "private" not in result.model_dump_json()


def test_explicit_extraction_supplies_regional_validation_context():
    class RegionalPrice(BaseModel):
        price: str

        @field_validator("price")
        @classmethod
        def validate_region(cls, value: str, info: ValidationInfo) -> str:
            assert info.context == {
                "locale": "es-UY",
                "languages": ("es-UY", "en"),
                "timezone": "America/Montevideo",
                "currency": "UYU",
                "egress": "direct",
            }
            return value

    result = Rastro().extract_html(
        ld({"price": "1250,00"}),
        source=Source(
            url="https://example.com/",
            locale="es-UY",
            languages=("es-UY", "en"),
            timezone="America/Montevideo",
            currency="UYU",
        ),
        schema=RegionalPrice,
        recipe=Recipe(strategy="jsonld", fields={"price": "/price"}),
    )
    assert result.status == Outcome.SUCCESS


def test_custom_pydantic_init_is_not_bypassed_to_supply_context():
    initialized = False
    seen = []

    class CustomInit(BaseModel):
        name: str

        def __init__(self, **data):
            nonlocal initialized
            initialized = True
            super().__init__(**data)

        @field_validator("name")
        @classmethod
        def capture_context(cls, value: str, info: ValidationInfo) -> str:
            seen.append(info.context)
            return value

    result = Rastro().extract_html(
        ld({"name": "Notebook"}),
        source=Source(url="https://example.com/", currency="UYU"),
        schema=CustomInit,
        recipe=Recipe(strategy="jsonld", fields={"name": "/name"}),
    )

    assert result.status == Outcome.SUCCESS
    assert initialized
    assert seen == [None]


def test_css_contract_matches_jsonld(html, source, schema, recipe):
    css = Recipe(
        strategy="css",
        record_selector=".product",
        fields={"name": "h2::text", "price": "span::attr(data-price)"},
    )
    engine = Rastro()
    a = engine.extract_html(html, source=source, schema=schema, recipe=recipe)
    b = engine.extract_html(html, source=source, schema=schema, recipe=css)
    assert [r.data for r in a.records] == [r.data for r in b.records]
    assert b.status == Outcome.SUCCESS
    assert b.records[1].evidence["price"].method == "css"
    assert "match 1" in b.records[1].evidence["price"].locator


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"@type": "Product", "name": "X", "offers": {"price": -1}}, Outcome.INVALID_DATA),
        ({"@type": "Product", "name": "X"}, Outcome.INVALID_DATA),
        ({"@type": "Product"}, Outcome.MISSING_DATA),
        ({"@type": "Organization", "name": "X"}, Outcome.MISSING_DATA),
        ({"@type": None, "name": "X"}, Outcome.MISSING_DATA),
        ([], Outcome.MISSING_DATA),
        (42, Outcome.MISSING_DATA),
    ],
)
def test_no_false_success(payload, expected, source, schema, recipe):
    result = Rastro().extract_html(ld(payload), source=source, schema=schema, recipe=recipe)
    assert result.status == expected
    assert not result.records


def test_partial_success_keeps_accounting(source, schema, recipe):
    result = Rastro().extract_html(
        ld(
            [
                {"@type": "Product", "name": "Valid", "offers": {"price": 10}},
                {"@type": "Product", "name": "Invalid", "offers": {"price": -10}},
            ]
        ),
        source=source,
        schema=schema,
        recipe=recipe,
    )
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert len(result.records) == 1
    assert result.metrics.accepted + result.metrics.rejected == result.metrics.candidates == 2
    assert result.issues[0].field == "price"
    assert result.issues[0].record_index == 1


@pytest.mark.parametrize(
    "raw",
    ["{", '{"price":NaN}', '{"name":"A","name":"B"}', "[" * 2000],
    ids=["truncated", "nan", "duplicate-key", "deep-nesting"],
)
def test_invalid_jsonld_is_visible(raw, source, schema, recipe):
    result = Rastro().extract_html(
        '<script type="application/ld+json">' + raw + "</script>",
        source=source,
        schema=schema,
        recipe=recipe,
    )
    assert result.status == Outcome.INVALID_DATA
    assert result.issues[0].code == "invalid_jsonld"


def test_valid_data_with_malformed_block_is_partial(html, source, schema, recipe):
    result = Rastro().extract_html(
        html.replace("</head>", '<script type="application/ld+json">{</script></head>'),
        source=source,
        schema=schema,
        recipe=recipe,
    )
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert len(result.records) == 2


@pytest.mark.parametrize("html", ["", "  ", "<html><p>No data</p></html>"])
def test_empty_input_is_missing(html, source, schema, recipe):
    assert (
        Rastro().extract_html(html, source=source, schema=schema, recipe=recipe).status
        == Outcome.MISSING_DATA
    )


def test_ambiguous_css_never_chooses_first(source, schema):
    recipe = Recipe(
        strategy="css", record_selector="article", fields={"name": "h2::text", "price": "p::text"}
    )
    result = Rastro().extract_html(
        "<article><h2>A</h2><h2>B</h2><p>12</p></article>",
        source=source,
        schema=schema,
        recipe=recipe,
    )
    assert result.status == Outcome.INSUFFICIENT_EVIDENCE
    assert result.issues[0].code == "ambiguous_match"
    assert result.metrics.rejected == 1


def test_css_missing_field(source, schema):
    recipe = Recipe(
        strategy="css", record_selector="article", fields={"name": "h2::text", "price": "p::text"}
    )
    result = Rastro().extract_html(
        "<article><h2>A</h2></article>", source=source, schema=schema, recipe=recipe
    )
    assert result.status == Outcome.INVALID_DATA


def test_invalid_selector_is_configuration_error(source, schema):
    recipe = Recipe(strategy="css", record_selector="[", fields={"name": "h2::text"})
    with pytest.raises(ConfigurationError):
        Rastro().extract_html("<p>Test</p>", source=source, schema=schema, recipe=recipe)


@pytest.mark.parametrize("limits", [Limits(max_html_bytes=10), Limits(max_records=1)])
def test_limits_reject_whole_operation(limits, html, source, schema, recipe):
    with pytest.raises(ResourceLimitError):
        Rastro(limits=limits).extract_html(html, source=source, schema=schema, recipe=recipe)


def test_jsonld_block_limit(html, source, schema, recipe):
    with pytest.raises(ResourceLimitError):
        Rastro(limits=Limits(max_jsonld_blocks=1)).extract_html(
            html.replace("</head>", ld({}) + "</head>"), source=source, schema=schema, recipe=recipe
        )


def test_css_record_limit(html, source, schema):
    recipe = Recipe(strategy="css", record_selector="article", fields={"name": "h2::text"})
    with pytest.raises(ResourceLimitError):
        Rastro(limits=Limits(max_records=1)).extract_html(
            html, source=source, schema=schema, recipe=recipe
        )


def test_custom_validator_messages_are_private(source):
    class PrivateSchema(BaseModel):
        name: str

        @field_validator("name")
        @classmethod
        def reject(cls, value: str) -> str:
            raise ValueError(f"PRIVATE:{value}")

    result = Rastro().extract_html(
        ld({"name": "secret"}),
        source=source,
        schema=PrivateSchema,
        recipe=Recipe(strategy="jsonld", fields={"name": "/name"}),
    )
    assert result.status == Outcome.INVALID_DATA
    assert "secret" not in result.model_dump_json()
    assert "PRIVATE" not in result.model_dump_json()


def test_schema_aliases_require_explicit_future_support(source, html):
    class Aliased(BaseModel):
        name: str = Field(alias="title")

    with pytest.raises(ConfigurationError, match="aliases"):
        Rastro().extract_html(
            html,
            source=source,
            schema=Aliased,
            recipe=Recipe(strategy="jsonld", fields={"name": "/name"}),
        )


def test_unknown_recipe_fields_are_configuration_error(source, schema, html):
    with pytest.raises(ConfigurationError):
        Rastro().extract_html(
            html,
            source=source,
            schema=schema,
            recipe=Recipe(strategy="jsonld", fields={"typo": "/name"}),
        )


CORPUS_RESULT_JSON_SHA256 = {
    "static": "c0e85865d51a70f7e9409dbf770192593c3403c814d67e6c4572d3f853e50723",
    "metadata": "aa73d30733771e023a423cc67b63bbcad974125b944ee9422a73702be90bbc6f",
    "reordered": "3b4002a9d7589a0d64eebced30cebbfae1562b94f2b5bc99ea9598ed2e0feb2f",
    "renamed": "7f29cdc9e186f9bf9693dc9fe827dba4a15ef52b6e2c70c88493c8ff170583b9",
    "missing": "2adc96b51ae255611cf200851430a19ec633c591684c873834b16cd9b95fd12c",
    "invalid": "9f91b37a9a279a7f5d47eb3dea89d59142c3905fc53ecac87a49aac315ff8e7f",
    "partial": "e4bfe87f74481f34875268b5028e8ef854fef36282c0ceb01df79a05b958422f",
    "ambiguous": "413da29fa1be09d59825815f3d1c630754e3856fb2c220adf06d8a58997f7e4b",
    "broken": "11402f9c9a4fdfa16da6e52b57d5fbe69a8f13f22c59d0b643f6b6d9f85e6cf1",
    "regional": "381c6aee54cf8a36cd6c39dc44516308422fc4213a8332d4a8a71b8d8f3f83d2",
    "rendered": "2966232ec8f8ad2003165a2724106ee960554d49d67d099d545c528146603feb",
    "js_shell": "6f3051e120f8564babf4bddfbad2107738a142648cee8f887397a47090e6e065",
    "semantic_trap": "7da074358f229e879bbc037605108a628d28f9d3765a5ff67265d27429d63ff3",
    "large": "516de062e4de0989e8cd505393a36f2f3fb01748db5322ff317ebb5d392ed130",
}


def _zero_timings(result):
    metrics = result.metrics.model_copy(
        update={
            "extraction_seconds": 0.0,
            "validation_seconds": 0.0,
            "total_seconds": 0.0,
            "storage_seconds": 0.0,
        }
    )
    return result.model_copy(update={"metrics": metrics})


def _rebuild_provenance(result):
    from rastro import Evidence, ExtractionRecord

    records = tuple(
        ExtractionRecord(
            data=record.data,
            evidence={field: Evidence(**ev.model_dump()) for field, ev in record.evidence.items()},
        )
        for record in result.records
    )
    return result.model_copy(update={"records": records})


def test_corpus_serialized_results_issues_and_provenance_are_unchanged(monkeypatch):
    import hashlib
    from datetime import UTC, datetime
    from uuid import UUID

    import rastro.application.extraction as extraction_module
    import rastro.application.memory as memory_module
    from benchmarks.run import load_cases

    fixed_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    fixed_uuid = UUID("00000000-0000-0000-0000-000000000000")
    frozen = type("FrozenDateTime", (), {"now": staticmethod(lambda tz=None: fixed_time)})
    monkeypatch.setattr(extraction_module, "datetime", frozen)
    monkeypatch.setattr(extraction_module, "uuid4", lambda: fixed_uuid)
    monkeypatch.setattr(memory_module, "datetime", frozen)

    cases = load_cases()
    assert {case.name for case in cases} == set(CORPUS_RESULT_JSON_SHA256)
    for case in cases:
        mappings = [
            Recipe(**mapping.model_dump(), require_evidence=True)
            for mapping in case.document.mappings
        ]
        recipe = mappings[0].model_copy(update={"fallbacks": tuple(mappings[1:])})
        result = Rastro().extract_html(
            case.document.html,
            source=Source(url="https://example.com/benchmark?token=omit#frag"),
            schema=case.document.record_schema,
            recipe=recipe,
        )
        zeroed = _zero_timings(result)
        dumped = zeroed.model_dump_json()
        assert hashlib.sha256(dumped.encode()).hexdigest() == CORPUS_RESULT_JSON_SHA256[case.name]
        assert dumped == _rebuild_provenance(zeroed).model_dump_json()
