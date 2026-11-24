"""examples/adapter.py stays runnable offline on public names only."""

import ast
import runpy
from pathlib import Path

import pytest

import rastro

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
EXAMPLE = Path("examples/adapter.py")


def test_adapter_example_loads_records_with_adapter_provenance(tmp_path):
    records = runpy.run_path(str(EXAMPLE))["run"](tmp_path.resolve())
    assert len(records) == 1
    assert records[0]["fields"]["slug"] == "noche-de-candombe"
    evidence = records[0]["provenance"]["title"]
    assert (evidence["method"], evidence["locator"]) == ("json", "adapter")


def test_adapter_example_uses_only_names_in_rastro_all():
    tree = ast.parse(EXAMPLE.read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(module.startswith("rastro") for module in imported)
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "rastro"
    }
    assert used <= set(rastro.__all__)
