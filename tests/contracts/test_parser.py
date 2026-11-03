import ast
from pathlib import Path

import pytest

from rastro import Limits, Outcome, Rastro, ResourceLimitError
from rastro.ports.parser import Candidate, ParsedDocument


class FakeParser:
    def __init__(self, candidates):
        self.candidates = candidates

    def parse(self, html, recipe, limits):
        return ParsedDocument(tuple(self.candidates))


def test_application_rejects_candidates_without_evidence(source, schema, recipe):
    parser = FakeParser([Candidate({"name": "Test", "price": 1}, {})])
    result = Rastro(parser=parser).extract_html(
        "provided", source=source, schema=schema, recipe=recipe
    )
    assert result.status == Outcome.INSUFFICIENT_EVIDENCE
    assert result.issues[0].code == "missing_evidence"


def test_application_enforces_adapter_record_budget(source, schema, recipe):
    parser = FakeParser([Candidate({}, {})] * 2)
    with pytest.raises(ResourceLimitError):
        Rastro(parser=parser, limits=Limits(max_records=1)).extract_html(
            "provided", source=source, schema=schema, recipe=recipe
        )


def test_domain_does_not_import_infrastructure():
    root = Path(__file__).parents[2] / "src" / "rastro" / "domain"
    forbidden = {"scrapling", "sqlite3", "fastapi", "ollama", "structlog"}
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imports = [entry.name for entry in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            else:
                continue
            assert not any(
                name.split(".")[0] in forbidden or name.startswith("rastro.adapters")
                for name in imports
            )


def test_missing_evidence_names_each_unproven_field(source, schema, recipe):
    candidates = [
        Candidate({"name": "A", "price": 1}, {"name": "n", "price": "p"}),
        Candidate({"name": "B", "price": 2}, {"name": "n"}),
    ]
    result = Rastro(parser=FakeParser(candidates)).extract_html(
        "provided", source=source, schema=schema, recipe=recipe
    )
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert [(i.code, i.record_index, i.field) for i in result.issues] == [
        ("missing_evidence", 1, "price")
    ]
    assert result.metrics.rejected == 1


def test_required_evidence_names_missing_required_fields(source, schema, recipe):
    candidates = [Candidate({"name": "A"}, {"name": "n"})] * 3
    strict = recipe.model_copy(update={"require_evidence": True})
    result = Rastro(parser=FakeParser(candidates)).extract_html(
        "provided", source=source, schema=schema, recipe=strict
    )
    assert result.status == Outcome.INSUFFICIENT_EVIDENCE
    assert [(i.code, i.record_index, i.field) for i in result.issues] == [
        ("missing_evidence", index, "price") for index in range(3)
    ]


def test_html_parser_rejects_json_recipes():
    from rastro import ConfigurationError, JsonRecipe
    from rastro.adapters.scrapling import ScraplingParser

    with pytest.raises(ConfigurationError):
        ScraplingParser().parse("<p></p>", JsonRecipe(fields={"x": "/x"}, record_path=""), Limits())
