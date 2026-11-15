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


def _assert_typed(func, documented: tuple[str, ...]) -> None:
    params = inspect.signature(func).parameters
    assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    assert set(documented) <= set(params)
    assert all(word in (func.__doc__ or "") for word in documented)


def test_new_job_from_goal_rejects_unknown_keywords_and_documents_its_options(tmp_path: Path):
    from rastro.application.job_new import new_job_from_goal

    with pytest.raises(TypeError):
        new_job_from_goal(tmp_path / "j.toml", goal="events", aprove=("a.example",))  # type: ignore[call-arg]
    _assert_typed(
        new_job_from_goal,
        ("search", "provider", "model", "acquisition", "allow_remote", "allow_partial", "approve"),
    )


def test_new_job_from_sites_rejects_unknown_keywords_and_documents_its_options(tmp_path: Path):
    from rastro.application.job_new import new_job_from_sites

    with pytest.raises(TypeError):
        new_job_from_sites(tmp_path / "j.toml", sites=("https://a.example/x",), alow_remote=True)  # type: ignore[call-arg]
    _assert_typed(
        new_job_from_sites, ("model", "acquisition", "allow_remote", "allow_partial", "fields")
    )
