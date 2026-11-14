"""Open drift is a persistent, non-zero alert until acknowledged (DRIFT-003/005, QUAL-005)."""

import json

import pytest

import rastro
from rastro.cli import main
from rastro.domain.job_results import JobExitCode

JOB = """format_version = 1

[dataset]
name = "items"
path = "data.sqlite"
identity = ["sku"]

[[fields]]
name = "sku"
type = "string"

[[fields]]
name = "name"
type = "string"

[[sources]]
kind = "file"
path = "page.html"
url = "https://shop.example/list"

[sources.recipe]
version = 1
strategy = "css"
record_selector = ".item"
require_evidence = true

[sources.recipe.fields]
sku = "::attr(data-sku)"
name = "h2::text"
"""


def page(count):
    items = "".join(
        f'<div class="item" data-sku="S{n}"><h2>Item {n}</h2></div>' for n in range(count)
    )
    return f"<html><body>{items}</body></html>"


@pytest.fixture
def job(tmp_path):
    (tmp_path / "job.toml").write_text(JOB, encoding="utf-8")
    (tmp_path / "page.html").write_text(page(5), encoding="utf-8")
    return tmp_path / "job.toml"


def run(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if captured.out.strip() else None


def test_successful_load_with_elevated_drift_exits_4_until_acknowledged(job, capsys):
    """DRIFT-005: the drifted run never becomes the baseline; ack-drift accepts it."""
    load = ("job", "load", str(job), "--drift")
    assert run(capsys, *load)[0] == 0
    (job.parent / "page.html").write_text(page(1), encoding="utf-8")
    code, report = run(capsys, *load)
    assert code == JobExitCode.NEEDS_ATTENTION == 4
    assert (report["job"]["status"], report["status"]) == ("success", "degraded")
    assert report["drift"][0]["risk"] == "elevated"
    assert [d["source_url"] for d in report["open_drift"]] == ["https://shop.example/list"]
    code, report = run(capsys, *load)
    assert (code, report["drift"][0]["risk"]) == (4, "elevated")
    code, report = run(capsys, "job", "load", str(job))
    assert (code, report["status"], "drift" in report) == (4, "degraded", False)
    code, acked = run(capsys, "job", "ack-drift", str(job), "--source", "1")
    assert (code, acked["acknowledged"]) == (0, ["https://shop.example/list"])
    code, report = run(capsys, *load)
    assert (code, report["status"], report["open_drift"]) == (0, "success", [])
    assert report["drift"][0]["risk"] == "low"


def test_acknowledge_job_drift_python_api_and_noop(job, capsys):
    load = ("job", "load", str(job), "--drift")
    assert run(capsys, *load)[0] == 0
    assert rastro.acknowledge_job_drift(job) == ()
    (job.parent / "page.html").write_text(page(1), encoding="utf-8")
    assert run(capsys, *load)[0] == 4
    assert rastro.load_job_file(job).exit_code == JobExitCode.NEEDS_ATTENTION
    assert rastro.acknowledge_job_drift(job) == ("https://shop.example/list",)
    assert rastro.load_job_file(job).exit_code == JobExitCode.SUCCESS


def test_ack_drift_rejects_an_unknown_source_as_json_error(job, capsys):
    assert main(["job", "ack-drift", str(job), "--source", "9"]) == 2
    error = json.loads(capsys.readouterr().err)
    assert (error["error"], error["location"]) == ("configuration_error", "source")


def test_serving_recipe_change_clears_open_drift(job, capsys):
    """REPAIR-005: promotion and rollback call serving_changed, which clears open drift."""
    from rastro.application.repair_context import RepairContext

    text = job.read_text(encoding="utf-8")
    job.write_text(text.replace("[dataset]", 'recipe_memory = "recipes.sqlite"\n\n[dataset]'))
    load = ("job", "load", str(job), "--drift")
    assert run(capsys, *load)[0] == 0
    (job.parent / "page.html").write_text(page(1), encoding="utf-8")
    assert run(capsys, *load)[0] == 4
    RepairContext.load(job, False).serving_changed(1)
    code, report = run(capsys, *load)
    assert (code, report["open_drift"]) == (0, [])
    assert run(capsys, *load)[0] == 0
