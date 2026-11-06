"""`rastro job preview` (DATA-002, JOB-002, AGENT-001): offline, read-only, provenance-complete."""

import json
from pathlib import Path

import pytest

from rastro import JobExitCode
from rastro.cli import main
from rastro.domain.job_dataset import JobPreview, job_preview_json_schema

SCHEMA_PATH = Path(__file__).parents[2] / "docs" / "schemas" / "job-preview.schema.json"
PAGE = (
    "<ul>"
    "<li><b>Tango</b><i>Sala Verdi</i><s>2026-12-01T20:00:00Z</s><u>10.5</u></li>"
    "<li><b>Candombe</b><i>Teatro Solis</i><s>2026-12-02T21:00:00Z</s><u>-3</u></li>"
    "<li><b>Milonga</b><i>Cabildo</i><s>2026-12-03T22:00:00Z</s><u>7</u></li>"
    "</ul>"
)
JOB = """
format_version = 1

[dataset]
name = "uy_dance_agenda"
path = "agenda.sqlite"
identity = ["title", "venue", "starts_at"]
approve = ["title", "venue", "starts_at"]
match_tolerance_minutes = 90

[recipe]
strategy = "css"
record_selector = "li"
fields = { title = "b::text", venue = "i::text", starts_at = "s::text", price = "u::text" }

[[sources]]
path = "page.html"
url = "https://agenda.example.com/events"

[[fields]]
name = "title"
type = "string"

[[fields]]
name = "venue"
type = "string"

[[fields]]
name = "starts_at"
type = "datetime"

[[fields]]
name = "price"
type = "decimal"
ge = 0
"""


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    (tmp_path / "page.html").write_text(PAGE)
    (tmp_path / "job.toml").write_text(JOB)
    return tmp_path


def _listing(root: Path) -> list[tuple[str, int]]:
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*"))


def test_preview_table_shows_values_provenance_and_loaded_flags(job_dir: Path, capsys):
    assert main(["job", "preview", str(job_dir / "job.toml")]) == JobExitCode.PARTIAL_SUCCESS
    out = capsys.readouterr().out
    assert "untrusted_source_data" in out
    assert "uy_dance_agenda" in out
    assert "Tango" in out and "Sala Verdi" in out
    assert "css" in out and "li [match 0] >> b::text" in out
    row = next(line for line in out.splitlines() if "title" in line and "Tango" in line)
    assert row.split().count("yes") == 1
    price = next(
        line for line in out.splitlines() if line.strip().startswith("price") and "10.5" in line
    )
    assert price.rstrip().endswith("no")


def test_preview_json_matches_published_schema(job_dir: Path, capsys):
    code = main(["job", "preview", str(job_dir / "job.toml"), "--output", "json"])
    assert code == JobExitCode.PARTIAL_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    schema = json.loads(SCHEMA_PATH.read_text())
    assert schema == job_preview_json_schema()
    assert JobPreview.model_validate(payload).model_dump(mode="json") == payload
    assert schema["$id"].endswith("/schemas/job-preview.schema.json")
    assert payload["data_trust"] == "untrusted_source_data"
    first = payload["sources"][0]["records"][0]
    assert first["status"] == "accepted"
    title = next(f for f in first["fields"] if f["name"] == "title")
    assert title["value"] == "Tango" and title["method"] == "css"
    assert title["locator"] == "li [match 0] >> b::text"
    assert title["validation"] == "valid" and title["loaded"] is True and title["identity"] is True
    price = next(f for f in first["fields"] if f["name"] == "price")
    assert price["loaded"] is False and price["identity"] is False


def test_preview_rejected_record_shows_reason(job_dir: Path, capsys):
    main(["job", "preview", str(job_dir / "job.toml"), "--output", "json"])
    records = json.loads(capsys.readouterr().out)["sources"][0]["records"]
    rejected = [r for r in records if r["status"] == "rejected"]
    assert len(rejected) == 1 and rejected[0]["index"] == 1
    assert rejected[0]["fields"] == []
    assert {"code": "validation_error", "field": "price"} in rejected[0]["reasons"]
    assert [r["index"] for r in records] == [0, 1, 2]
    main(["job", "preview", str(job_dir / "job.toml")])
    assert "rejected" in capsys.readouterr().out


def test_preview_writes_nothing(job_dir: Path):
    before = _listing(job_dir)
    main(["job", "preview", str(job_dir / "job.toml")])
    main(["job", "preview", str(job_dir / "job.toml"), "--output", "json"])
    assert _listing(job_dir) == before
    assert not (job_dir / "agenda.sqlite").exists()


def test_preview_requires_dataset_block_and_reports_config_errors(job_dir: Path, capsys):
    plain = job_dir / "plain.toml"
    plain.write_text(
        JOB.split("[recipe]")[0].split("[dataset]")[0] + "[recipe]" + JOB.split("[recipe]")[1]
    )
    assert main(["job", "preview", str(plain)]) == JobExitCode.CONFIG_ERROR
    captured = capsys.readouterr()
    assert not captured.out
    assert "[dataset]" in json.loads(captured.err)["message"]

    escaped = job_dir / "escaped.toml"
    escaped.write_text(JOB.replace('path = "agenda.sqlite"', 'path = "/tmp/agenda.sqlite"'))
    assert main(["job", "preview", str(escaped)]) == JobExitCode.CONFIG_ERROR
    assert "--allow-absolute-paths" in json.loads(capsys.readouterr().err)["message"]

    assert main(["job", "preview", str(job_dir / "job.toml"), "--output", "xml"]) == 2


def test_preview_table_neutralizes_terminal_control_sequences(job_dir: Path, capsys):
    (job_dir / "page.html").write_text(
        "<ul><li><b>Evil\x1b[31mRED</b><i>V</i><s>2026-12-01T20:00:00Z</s><u>1</u></li></ul>"
    )
    main(["job", "preview", str(job_dir / "job.toml")])
    assert "\x1b" not in capsys.readouterr().out


def test_job_without_dataset_block_run_output_is_unchanged(job_dir: Path, capsys):
    plain = job_dir / "plain.toml"
    plain.write_text(JOB.replace(JOB[JOB.index("[dataset]") : JOB.index("[recipe]")], ""))
    assert main(["job", "run", str(plain)]) == JobExitCode.PARTIAL_SUCCESS
    report = json.loads(capsys.readouterr().out)
    assert "dataset" not in report and "dataset" not in json.dumps(report)
    assert main(["job", "validate", str(plain)]) == JobExitCode.SUCCESS
    assert "dataset" not in json.loads(capsys.readouterr().out)["job"]
