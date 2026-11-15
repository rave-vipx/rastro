"""`new_job` keeps a typed signature and the CLI keeps its option help (JOB-001)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from rastro.application.job_new import new_job
from rastro.cli import main


def test_new_job_rejects_unknown_keywords_and_documents_its_options(tmp_path: Path):
    """A typo such as ``recipe_memroy`` must fail, not be silently ignored."""
    page = tmp_path / "page.html"
    page.write_text("<html></html>")
    with pytest.raises(TypeError):
        new_job(tmp_path / "j.toml", page=page, recipe_memroy="m.json")  # type: ignore[call-arg]
    params = inspect.signature(new_job).parameters
    assert {"allow_partial", "recipe_memory", "model", "fields", "dataset", "identity"} <= set(
        params
    )
    assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    doc = new_job.__doc__ or ""
    assert all(word in doc for word in ("allow_partial", "recipe_memory", "model"))


def test_new_help_describes_example_field_memory_and_partial(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit):
        main(["new", "--help"])
    flat = " ".join(capsys.readouterr().out.split())
    for phrase in (
        "starts the next example record",
        "without an example",
        "enabling repair",
        "only a partial success",
    ):
        assert phrase in flat
