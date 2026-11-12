"""AGENT-001 / DATA-003: every CLI error is one JSON envelope with error, message and location."""

import json
import sys

import pytest

from rastro.cli import main


def _envelope(capsys):
    captured = capsys.readouterr()
    assert not captured.out
    payload = json.loads(captured.err)
    assert (
        {"error", "message", "location"}
        <= set(payload)
        <= {
            "error",
            "message",
            "location",
            "hint",
        }
    )
    return payload


@pytest.mark.parametrize("command", ["export", "status"])
def test_dataset_command_on_missing_file_names_the_path(tmp_path, capsys, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    assert main(["dataset", command, "data/agenda.sqlite"]) == 2
    payload = _envelope(capsys)
    assert payload["error"] == "invalid_input"
    assert payload["message"] == "Dataset file not found: data/agenda.sqlite"
    assert payload["location"] == "path"
    assert "rastro job load" in payload["hint"]
    assert not (tmp_path / "data").exists()


def test_non_job_errors_carry_a_message_and_location(tmp_path, capsys):
    assert main(["validate-recipe", str(tmp_path / "missing.json")]) == 2
    payload = _envelope(capsys)
    assert payload["error"] == "invalid_input"
    assert payload["message"]
    assert payload["location"] is None


def test_missing_extra_helper_is_shared_and_exact():
    from rastro.application.errors import missing_extra_error
    from rastro.cli_errors import missing_extra_error as reexported

    assert reexported is missing_extra_error
    assert missing_extra_error("studio") == {
        "error": "missing_extra",
        "message": "This command needs the optional 'studio' extra",
        "location": None,
        "hint": "pip install 'rastro-engine[studio]'",
    }
    with pytest.raises(ValueError):
        missing_extra_error("studio]' && rm -rf /")


@pytest.mark.parametrize(
    ("argv", "module", "extra"),
    [
        (["studio"], "uvicorn", "studio"),
        (["mcp", "--root", "."], "rastro.adapters.mcp_server", "mcp"),
    ],
)
def test_cli_reports_missing_extras_with_the_helper(capsys, monkeypatch, argv, module, extra):
    from rastro.application.errors import missing_extra_error

    monkeypatch.setitem(sys.modules, module, None)
    assert main(argv) == 2
    assert _envelope(capsys) == missing_extra_error(extra)


def test_job_load_names_a_missing_dataset_folder(tmp_path, capsys):
    """DATA-001: a dataset folder that does not exist is named, with its TOML key."""
    (tmp_path / "page.html").write_text("<ul><li><h3>A</h3></li><li><h3>B</h3></li></ul>")
    job = tmp_path / "job.toml"
    job.write_text(
        'format_version = 1\n[recipe]\nstrategy = "css"\nrecord_selector = "li"\n'
        '[recipe.fields]\nt = "h3::text"\n'
        '[dataset]\nname = "d"\npath = "nested/d.sqlite"\nidentity = ["t"]\n'
        '[[sources]]\nkind = "file"\npath = "page.html"\nurl = "https://x.example/"\n'
        '[[fields]]\nname = "t"\ntype = "string"\n'
    )
    assert main(["job", "load", str(job)]) == 2
    payload = _envelope(capsys)
    assert payload["location"] == "dataset.path"
    assert "nested/d.sqlite" in payload["message"]
