"""The public scrape examples stay runnable offline (INT-001, REPAIR-001)."""

import runpy

import pytest

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


def test_scrape_example_prints_validated_offers(capsys):
    runpy.run_path("examples/scrape.py", run_name="__main__")
    output = capsys.readouterr().out
    assert output.startswith("success\n")
    assert "https://acme.example/careers/1 5200" in output


def test_self_repair_example_heals_the_renamed_class(tmp_path):
    demo = runpy.run_path("examples/self_repair.py")
    first, second = demo["run"](tmp_path / "recipes.db")
    assert first.status.value == second.status.value == "success"
    assert len(second.items) == 3
    assert second.repair is not None and second.repair.promoted
    assert second.recipe_version == 2
