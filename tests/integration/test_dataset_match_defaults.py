"""DATA-004 / AGENT-001: domain-neutral match_fields default and located dataset errors."""

import json

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.cli import main
from rastro.domain.job_dataset import DatasetSpec

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

JOB = """format_version = 1

[dataset]
name = "jobs"
path = "jobs.sqlite"
identity = ["code"]
{extra}

[[fields]]
name = "code"
type = "string"

[[fields]]
name = "title"
type = "string"

[[fields]]
name = "venue"
type = "string"

[[fields]]
name = "start_time"
type = "datetime"

[[sources]]
kind = "file"
path = "a.html"
url = "https://a.example/list"

[[sources]]
kind = "file"
path = "b.html"
url = "https://b.example/list"

[recipe]
strategy = "css"
record_selector = "li"
fields = {{ code = "b::text", title = "h3::text", venue = "i::text", start_time = "time::text" }}
"""


def _page(code: str) -> str:
    return (
        f"<ul><li><b>{code}</b><h3>Gala</h3><i>Hall</i>"
        "<time>2026-12-01T20:00:00+00:00</time></li></ul>"
    )


def _write(tmp_path, extra=""):
    (tmp_path / "a.html").write_text(_page("A1"), encoding="utf-8")
    (tmp_path / "b.html").write_text(_page("B1"), encoding="utf-8")
    job = tmp_path / "job.toml"
    job.write_text(JOB.format(extra=extra), encoding="utf-8")
    return job


def test_default_match_fields_follow_identity_not_event_names():
    spec = DatasetSpec(name="d", path="d.sqlite", identity=("code",))
    assert spec.match_fields is None


def test_without_match_fields_event_named_fields_are_not_near_matched(tmp_path, capsys):
    job = _write(tmp_path)
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    records = SQLiteDatasetStore(tmp_path / "jobs.sqlite").records("jobs")
    assert len(records) == 2
    assert all(not r.uncertain_match for r in records)


def test_explicit_match_fields_still_flag_uncertain_matches(tmp_path, capsys):
    job = _write(tmp_path, 'match_fields = ["title", "venue", "start_time"]')
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    records = SQLiteDatasetStore(tmp_path / "jobs.sqlite").records("jobs")
    assert all(r.uncertain_match for r in records)


@pytest.mark.parametrize(
    ("extra", "location"),
    [
        ('match_fields = ["title", "venue", "nope"]', "dataset.match_fields"),
        ('approve = ["code", "nope"]', "dataset.approve"),
    ],
)
def test_wrong_dataset_names_report_their_toml_key(tmp_path, capsys, extra, location):
    job = _write(tmp_path, extra)
    assert main(["job", "validate", str(job)]) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["location"] == location


def test_wrong_identity_name_reports_its_toml_key(tmp_path, capsys):
    job = _write(tmp_path)
    job.write_text(job.read_text().replace('identity = ["code"]', 'identity = ["nope"]'))
    assert main(["job", "validate", str(job)]) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"] == "validation_error"
    assert payload["location"] == "dataset.identity"
