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
