"""`rastro job` error contract: one JSON object on stderr, CONFIG exit code, no leaks."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rastro import JobExitCode
from rastro.cli import main

TOKEN = "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
BASE = (
    'format_version = 1\n[recipe]\nstrategy = "css"\nrecord_selector = "article"\n'
    'fields = { name = "h2::text" }\n[[sources]]\npath = "page.html"\n'
    '[[fields]]\nname = "name"\ntype = "string"\n'
)


def _error(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    captured = capsys.readouterr()
    assert not captured.out
    lines = captured.err.strip().splitlines()
    assert len(lines) == 1, captured.err
    payload = json.loads(lines[0])
    assert set(payload) == {"error", "message", "location"}
    return payload


@pytest.mark.parametrize("command", ["validate", "run"])
@pytest.mark.parametrize("pattern", ["^(?=F)F.*", "(a)\\\\1"])
def test_unsupported_patterns_are_validation_errors(tmp_path: Path, capsys, command, pattern):
    (tmp_path / "page.html").write_text("<article><h2>Field</h2></article>")
    job = tmp_path / "badpat.toml"
    job.write_text(BASE + f'pattern = "{pattern}"\n')
    assert main(["job", command, str(job)]) == JobExitCode.CONFIG_ERROR
    payload = _error(capsys)
    assert payload["error"] == "validation_error"
    assert payload["location"] == "fields.0.string.pattern"
    assert "unsupported regular expression" in str(payload["message"])


def test_unexpected_schema_errors_never_produce_tracebacks(tmp_path: Path, capsys, monkeypatch):
    from pydantic_core import SchemaError

    import rastro.domain.jobs as jobs_module

    def explode(_self: object) -> None:
        raise SchemaError("boom")

    (tmp_path / "page.html").write_text("<article><h2>Field</h2></article>")
    job = tmp_path / "job.toml"
    job.write_text(BASE)
    monkeypatch.setattr(jobs_module.Job, "build_schema", explode)
    for command in ("validate", "run"):
        assert main(["job", command, str(job)]) == JobExitCode.CONFIG_ERROR
        assert _error(capsys)["error"] == "schema_error"
    monkeypatch.setattr(jobs_module.Job, "build_schema", lambda _self: 1 / 0)
    assert main(["job", "validate", str(job)]) == JobExitCode.CONFIG_ERROR
    assert _error(capsys) == {
        "error": "internal_error",
        "message": "Unexpected ZeroDivisionError",
        "location": None,
    }


def test_token_shaped_env_reference_is_rejected_and_never_echoed(tmp_path: Path):
    job = tmp_path / "tok.toml"
    job.write_text(
        BASE + f'[model]\nprovider = "openai_compatible"\nmodel = "m"\napi_key_env = "{TOKEN}"\n'
    )
    for command in ("validate", "run"):
        proc = subprocess.run(
            [sys.executable, "-m", "rastro.cli", "job", command, str(job)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == JobExitCode.CONFIG_ERROR
        assert TOKEN not in proc.stdout and TOKEN not in proc.stderr
        assert json.loads(proc.stderr)["location"] == "model.openai_compatible.api_key_env"


def test_validate_redacts_env_references(tmp_path: Path, capsys):
    (tmp_path / "page.html").write_text("<article><h2>Field</h2></article>")
    job = tmp_path / "env.toml"
    job.write_text(
        BASE + '[model]\nprovider = "openai_compatible"\nmodel = "m"\n'
        'api_key_env = "AKIA0123456789ABCDEF"\nbase_url_env = "MODEL_URL"\nallow_remote = true\n'
    )
    assert main(["job", "validate", str(job)]) == JobExitCode.SUCCESS
    out = capsys.readouterr().out
    model = json.loads(out)["job"]["model"]
    assert model["api_key_env"] == model["base_url_env"] == "<redacted>"
    assert "AKIA0123456789ABCDEF" not in out and "MODEL_URL" not in out


@pytest.mark.parametrize(
    ("argv", "fragment"),
    [
        (["job"], "required"),
        (["job", "valdate", "x.toml"], "invalid choice"),
        (["job", "schema", "--target", "nope"], "invalid choice"),
        (["job", "run", "x.toml", "--frob"], "unrecognized arguments"),
        (["--verbose", "job", "run"], "required"),
    ],
)
def test_job_usage_errors_are_json(capsys, argv, fragment):
    assert main(argv) == JobExitCode.CONFIG_ERROR
    payload = _error(capsys)
    assert payload["error"] == "usage_error" and payload["location"] is None
    assert fragment in str(payload["message"])


def test_non_job_usage_errors_keep_argparse_text(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["bogus"])
    assert exc.value.code == JobExitCode.CONFIG_ERROR
    captured = capsys.readouterr()
    assert "usage: rastro" in captured.err and "invalid choice" in captured.err


@pytest.mark.parametrize(
    ("name", "content", "code", "fragment"),
    [
        ("syn.toml", "format_version = 1\nx = [\n", "syntax_error", "not valid TOML (at"),
        ("syn.json", '{"format_version": 1,\n', "syntax_error", "not valid JSON (line"),
        ("dup.json", '{"format_version": 1, "format_version": 1}', "invalid_job", "Duplicate"),
        ("bom.json", "﻿{}", "invalid_job", "byte-order mark"),
        ("big.json", " " * 262_145, "resource_limit", "256 KiB"),
        ("missing.toml", None, "configuration_error", "does not exist"),
        ("job.yaml", "{}", "configuration_error", ".toml or .json"),
    ],
)
def test_job_file_errors_have_stable_codes(tmp_path: Path, capsys, name, content, code, fragment):
    path = tmp_path / name
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert main(["job", "validate", str(path)]) == JobExitCode.CONFIG_ERROR
    payload = _error(capsys)
    assert payload["error"] == code and fragment in str(payload["message"])


def test_non_utf8_and_unreadable_job_files(tmp_path: Path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_bytes(b"format_version = 1\n\xff\n")
    assert main(["job", "validate", str(bad)]) == JobExitCode.CONFIG_ERROR
    assert _error(capsys) == {
        "error": "syntax_error",
        "message": "Job file must be UTF-8 text",
        "location": None,
    }
    (tmp_path / "page.html").write_text("<article><h2>Field</h2></article>")
    unreadable = tmp_path / "locked.toml"
    unreadable.write_text(BASE)
    unreadable.chmod(0)
    try:
        # Windows and root ignore the mode bits; close the probe handle before skipping.
        with unreadable.open("rb"):  # pragma: no cover - permissions not enforced
            pass
        pytest.skip("permissions are not enforced for this user")
    except PermissionError:
        assert main(["job", "validate", str(unreadable)]) == JobExitCode.CONFIG_ERROR
        assert _error(capsys)["error"] == "file_error"
    finally:
        unreadable.chmod(0o600)


def test_validation_errors_do_not_echo_values_or_page_text(tmp_path: Path, capsys):
    (tmp_path / "page.html").write_text("<article><h2>PRIVATE-PAGE-TEXT</h2></article>")
    job = tmp_path / "tag.json"
    job.write_text(
        json.dumps(
            {
                "format_version": 1,
                "sources": [{"path": "page.html"}],
                "discover_recipes": True,
                "fields": [{"name": "name", "type": f"x-{TOKEN}"}],
            }
        )
    )
    assert main(["job", "run", str(job)]) == JobExitCode.CONFIG_ERROR
    payload = _error(capsys)
    assert payload["location"] == "fields.0"
    assert TOKEN not in json.dumps(payload) and "PRIVATE" not in json.dumps(payload)
