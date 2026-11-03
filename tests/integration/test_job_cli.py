import json
import subprocess
import sys
from pathlib import Path

import pytest

from rastro import (
    ConfigurationError,
    JobExitCode,
    JobRecordData,
    JobRunResult,
    ResourceLimitError,
    load_job,
    run_job_file,
)
from rastro.application.job_loader import resolve_job_path
from rastro.cli import main


def test_cli_validate_and_run_supplied_files(tmp_path: Path, capsys):
    (tmp_path / "page.html").write_text(
        "<article><h2>Trail notebook</h2><span class='p'>12.50</span></article>"
    )
    (tmp_path / "api.json").write_text('{"items":[{"name":"Pen","price":"3.25"}]}')
    job_toml = tmp_path / "job.toml"
    job_toml.write_text(
        """
format_version = 1

[region]
locale = "en-US"

[[sources]]
path = "page.html"
representation = "html"
url = "https://example.com/page"

[sources.recipe]
strategy = "css"
record_selector = "article"
fields = { name = "h2::text", price = ".p::text" }

[[sources]]
path = "api.json"
representation = "json"

[sources.recipe]
strategy = "json"
record_path = "/items"
fields = { name = "/name", price = "/price" }

[[fields]]
name = "name"
type = "string"
min_length = 1

[[fields]]
name = "price"
type = "decimal"
ge = 0
"""
    )
    assert main(["job", "validate", str(job_toml)]) == JobExitCode.SUCCESS
    validated = json.loads(capsys.readouterr().out)
    assert validated["valid"] is True and validated["source_count"] == 2

    assert main(["job", "run", str(job_toml), "--output", "json"]) == JobExitCode.SUCCESS
    raw_out = capsys.readouterr().out
    report = JobRunResult[JobRecordData].model_validate_json(raw_out)
    assert report.schema_version == 1
    assert report.data_trust == "untrusted_source_data"
    assert report.job.status == "success" and report.job.records_accepted == 2
    assert report.results[0].records[0].evidence["name"].locator == "article [match 0] >> h2::text"
    assert report.results[1].records[0].evidence["price"].locator == "json#/items/0/price"


def test_cli_exit_codes_and_local_source_errors(tmp_path: Path, capsys):
    (tmp_path / "mixed.html").write_text(
        "<article><h2>Good</h2><span class='p'>5</span></article>"
        "<article><h2>Bad</h2><span class='p'>-9</span></article>"
    )
    (tmp_path / "empty.html").write_text("<div>none</div>")
    base = (
        'format_version = 1\n[recipe]\nstrategy = "css"\nrecord_selector = "article"\n'
        'fields = { name = "h2::text", price = ".p::text" }\n'
        '[[fields]]\nname = "name"\ntype = "string"\n'
        '[[fields]]\nname = "price"\ntype = "integer"\nge = 0\n'
    )
    partial_job = tmp_path / "partial.toml"
    partial_job.write_text(base + '[[sources]]\npath = "mixed.html"\n')
    assert main(["job", "run", str(partial_job)]) == JobExitCode.PARTIAL_SUCCESS
    assert json.loads(capsys.readouterr().out)["job"]["status"] == "partial_success"

    failed_job = tmp_path / "failed.toml"
    failed_job.write_text(base + '[[sources]]\npath = "empty.html"\n')
    assert main(["job", "run", str(failed_job)]) == JobExitCode.FAILED
    assert json.loads(capsys.readouterr().out)["job"]["status"] == "failed"

    bad_job = tmp_path / "bad.toml"
    bad_job.write_text("format_version = 99\n")
    assert main(["job", "run", str(bad_job)]) == JobExitCode.CONFIG_ERROR
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err) == {
        "error": "validation_error",
        "message": "Input should be 1 (and 2 more validation errors)",
        "location": "format_version",
    }

    missing_src = tmp_path / "missing_src.toml"
    missing_src.write_text(base + '[[sources]]\npath = "gone.html"\n')
    with pytest.raises(ConfigurationError):
        run_job_file(missing_src)

    (tmp_path / "bad_utf8.html").write_bytes(b"\xff\xfe")
    bad_utf8 = tmp_path / "utf8.toml"
    bad_utf8.write_text(base + '[[sources]]\npath = "bad_utf8.html"\n')
    with pytest.raises(ConfigurationError):
        run_job_file(bad_utf8)

    (tmp_path / "big.html").write_text("x" * 20)
    big_job = tmp_path / "big.toml"
    big_job.write_text(base + '[limits]\nmax_html_bytes = 5\n[[sources]]\npath = "big.html"\n')
    with pytest.raises(ResourceLimitError):
        run_job_file(big_job)


def test_path_containment_symlinks_and_allow_absolute_paths(tmp_path: Path, capsys):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "data.html"
    outside_file.write_text("<article><h2>Item</h2></article>")

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "linked.html").symlink_to(outside_file)
    recipe_cfg = {"strategy": "css", "record_selector": "article", "fields": {"name": "h2::text"}}

    symlink_job = job_dir / "symlink.json"
    symlink_job.write_text(
        json.dumps(
            {
                "format_version": 1,
                "sources": [{"path": "linked.html"}],
                "recipe": recipe_cfg,
                "fields": [{"name": "name", "type": "string"}],
            }
        )
    )
    assert main(["job", "validate", str(symlink_job)]) == JobExitCode.CONFIG_ERROR
    capsys.readouterr()

    abs_job = job_dir / "abs.json"
    abs_job.write_text(
        json.dumps(
            {
                "format_version": 1,
                "sources": [{"path": str(outside_file)}],
                "recipe": recipe_cfg,
                "fields": [{"name": "name", "type": "string"}],
            }
        )
    )
    assert main(["job", "run", str(abs_job)]) == JobExitCode.CONFIG_ERROR
    capsys.readouterr()
    assert main(["job", "validate", str(abs_job), "--allow-absolute-paths"]) == JobExitCode.SUCCESS
    capsys.readouterr()
    proc = subprocess.run(
        [sys.executable, "-m", "rastro.cli", "job", "run", str(abs_job), "--allow-absolute-paths"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["job"]["records_accepted"] == 1

    with pytest.raises(ConfigurationError):
        resolve_job_path("rel.html", base_dir=None, allow_absolute_paths=False)
    with pytest.raises(ConfigurationError):
        load_job(job_dir / "bad.txt")
    with pytest.raises(ConfigurationError):
        load_job(job_dir / "missing.toml")
