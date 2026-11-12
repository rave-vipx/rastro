"""DATA-001 / SCR-002: optional relocation cannot misreport a committed load."""

import json

import pytest
from test_recipe_cli import setup_sample

from rastro.adapters.scrapling_relocation import ScraplingRelocation
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.repair_context import RepairContext
from rastro.cli import main
from rastro.domain.models import Limits

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


@pytest.mark.parametrize("case", ["optional", "nodes", "mixed"])
def test_load_retains_fixture_and_honest_success_when_relocation_is_limited(tmp_path, capsys, case):
    folder, job = setup_sample(tmp_path)
    path = folder / "sodre-cartelera.html"
    html = path.read_text(encoding="utf-8")
    if case == "optional":
        html = html.replace('<span class="cyg--precio">450</span>', "", 1)
    elif case == "nodes":
        html = html.replace("</body>", "<p></p>" * 4097 + "</body>")
    else:
        job.write_text(
            job.read_text(encoding="utf-8").replace(
                "h2.cyg--display-3::text", "h2.cyg--display-3::text, h3::attr(title)"
            ),
            encoding="utf-8",
        )
    path.write_text(html, encoding="utf-8")
    assert main(["job", "load", str(job)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["job"]["status"] == "success"
    assert output["job"]["records_accepted"] == 7
    store = SQLiteDatasetStore(folder / "agenda.sqlite")
    before = store.records("uy_dance_agenda")
    assert len(before) == 6  # The cross-source performance shares an identity.
    context = RepairContext.load(job, False)
    source, key, recipe = context.source(1)
    snapshot, fixtures = context.fixtures(source, recipe)
    assert len(fixtures) == 1
    assert context.repository.get(key).recipe.version == 1
    if case == "optional":
        assert "issues" not in output["sources"][0]
        assert snapshot is not None and "price" not in snapshot.fields
        assert "price" not in fixtures[0].expected.records[0].evidence
        assert ScraplingRelocation().relocate(html, recipe, snapshot, Limits()) == ()
    else:
        assert snapshot is None
        assert output["sources"][0]["issues"][0]["code"] == "relocation_retention_skipped"
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    assert [r.fields for r in store.records("uy_dance_agenda")] == [r.fields for r in before]
