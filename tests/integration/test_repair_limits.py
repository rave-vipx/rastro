"""SEC-008/DRIFT-001: composition forwards configured storage budgets."""

import pytest
from test_recipe_cli import setup_sample

from rastro.application.repair_context import RepairContext
from rastro.cli import main
from rastro.domain.models import Limits

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


@pytest.mark.parametrize("component", ["dataset", "drift", "history"])
def test_cli_forwards_job_limits_to_storage(tmp_path, capsys, monkeypatch, component):
    from rastro.application import job_dataset, repair_context

    _, job = setup_sample(tmp_path)
    job.write_text(
        job.read_text(encoding="utf-8") + "\n[limits]\nmax_records = 17\n", encoding="utf-8"
    )
    module = repair_context if component == "history" else job_dataset
    name = "SQLiteDriftMonitor" if component == "drift" else "SQLiteDatasetStore"
    constructor = getattr(module, name)
    budgets = []

    def store(*args, **kwargs):
        budgets.append(kwargs.get("limits"))
        return constructor(*args, **kwargs)

    monkeypatch.setattr(module, name, store)
    assert main(["job", "load", str(job), "--drift"]) == 0
    capsys.readouterr()
    if component == "history":
        assert RepairContext.load(job, False).history()
    assert budgets == [Limits(max_records=17)]
