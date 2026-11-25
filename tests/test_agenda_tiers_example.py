"""examples/event-agenda/read_agenda.py: nested tiers, no adapter, offline, public names only."""

import ast
import runpy
from pathlib import Path

import rastro

EXAMPLE = Path("examples/event-agenda/read_agenda.py")


def test_example_reads_nested_tiers_back_with_provenance(tmp_path):
    records = runpy.run_path(str(EXAMPLE))["run"](tmp_path.resolve())
    by_title = {r.fields["title"]: r for r in records}
    assert len(records) == 3
    tiers = by_title["Noche de candombe"].fields["tiers"]
    assert [(t["tier"], t["price"]) for t in tiers] == [
        ("General", "450"),
        ("Platea", "700"),
        ("Palco", "1200"),
    ]
    assert by_title["Milonga abierta"].fields["tiers"] == [{"tier": "Entrada libre", "price": "0"}]
    assert (
        by_title["Tango en la rambla"]
        .provenance["title"]
        .source_url.startswith("https://salanorte.example/")
    )


def test_example_prints_a_short_report(tmp_path, capsys):
    runpy.run_path(str(EXAMPLE))["run"](tmp_path.resolve())
    assert capsys.readouterr().out.strip() == "success: 1 source, 3 accepted, 0 rejected"


def test_example_uses_only_names_in_rastro_all():
    tree = ast.parse(EXAMPLE.read_text(encoding="utf-8"))
    used = {
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "rastro"
    }
    assert used <= set(rastro.__all__)
    assert not any(
        isinstance(n, ast.ImportFrom) and (n.module or "").startswith("rastro")
        for n in ast.walk(tree)
    )
