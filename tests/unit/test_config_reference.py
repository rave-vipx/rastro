"""The configuration reference lists every option, with a description, and its generated
outputs stay current."""

import argparse
import inspect
import json
import re
import tomllib
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

import rastro
from rastro import Job, parse_job
from rastro.capabilities_render import render_snippet
from rastro.cli import _build_parser, main
from rastro.config_catalog import ENV_VARS, GRANTS, PYTHON_PARAMS, SECTIONS
from rastro.config_reference import reference
from rastro.config_render import render_json, render_markdown
from rastro.config_template import render_template

ROOT = Path(__file__).resolve().parents[2]
ENTRIES = reference()
PATHS = {entry.path for entry in ENTRIES}


def _models(model: type[BaseModel], seen: set[type[BaseModel]]) -> set[type[BaseModel]]:
    seen.add(model)
    for field in model.model_fields.values():
        for item in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", repr(field.annotation)):
            found = getattr(rastro, item.rsplit(".", 1)[-1], None)
            if inspect.isclass(found) and issubclass(found, BaseModel) and found not in seen:
                _models(found, seen)
    return seen


def test_every_job_field_is_listed_with_a_description() -> None:
    """Each field of the job models appears in the reference; every entry says what
    it does, gives a docs page and a type."""
    job_names = {entry.path.rsplit(".", 1)[-1] for entry in ENTRIES if entry.kind == "job"}
    for model in _models(Job, set()):
        assert set(model.model_fields) <= job_names, model.__name__
    for entry in ENTRIES:
        assert entry.description and entry.type and entry.docs.startswith("docs/"), entry


def test_spider_keys_have_types_defaults_and_bounds() -> None:
    """Entries carry the model's type, default and bounds."""
    by_path = {entry.path: entry for entry in ENTRIES}
    pages = by_path["spider.max_pages"]
    assert (pages.type, pages.default) == ("integer or string", "25")
    assert pages.bounds.startswith(">= 1, <= 100") and '"unbounded"' in pages.bounds
    budget = by_path["spider.time_budget_seconds"]
    assert (budget.type, budget.default, budget.bounds) == ("integer", "1800", ">= 60, <= 86400")
    assert by_path["spider.seed"].bounds == "one of page, sitemap"
    assert (
        by_path["spider.seed_order"].default,
        by_path["spider.seed_order"].bounds,
    ) == ('"document"', "one of document, reverse, lastmod")
    assert by_path["spider.allow_pattern"].bounds == "length >= 1, length <= 512"
    assert by_path["sources[].recipe"].same_as == "recipe"
    assert {e.variant for e in ENTRIES if e.path == "recipe.strategy"} >= {'strategy = "json"'}


def _commands(
    parser: argparse.ArgumentParser, command: str = "rastro"
) -> list[tuple[str, argparse.ArgumentParser]]:
    found = [(command, parser)]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {choice.dest: choice.help for choice in action._choices_actions}
            for name, sub in action.choices.items():
                assert helps.get(name), f"{command} {name} has no help"
                found += _commands(sub, f"{command} {name}")
    return found


def test_every_cli_flag_has_help_and_is_listed() -> None:
    """Every command and flag of the real parser has help and appears in the reference;
    grant copies on subcommands are listed once, as global flags."""
    for command, parser in _commands(_build_parser()):
        assert command == "rastro" or command in PATHS
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction | argparse._VersionAction):
                continue
            if isinstance(action, argparse._SubParsersAction):
                continue
            name = action.option_strings[-1] if action.option_strings else action.dest.upper()
            assert action.help, f"{command} {name} has no help"
            if action.default is argparse.SUPPRESS:
                assert f"rastro {name}" in PATHS
            else:
                assert f"{command} {name}" in PATHS


def test_every_python_keyword_is_described() -> None:
    """Every parameter of the listed public functions has a description, none stale."""
    for name, (_, described) in PYTHON_PARAMS.items():
        assert set(inspect.signature(getattr(rastro, name)).parameters) == set(described), name


def test_every_environment_variable_and_grant_is_listed() -> None:
    """Each RASTRO_* name in the code and each operator grant flag has an entry."""
    names: set[str] = set()
    for path in (ROOT / "src" / "rastro").rglob("*.py"):
        names |= set(re.findall(r"\"(RASTRO_[A-Z][A-Z0-9_]+)\"", path.read_text("utf-8")))
    listed = set(ENV_VARS) | {env for env, *_ in GRANTS.values() if env}
    assert names - listed == set()
    root = _build_parser()
    group = next(g for g in root._action_groups if g.title == "operator opt-ins for job files")
    flags = {action.option_strings[-1] for action in group._group_actions}
    assert flags <= set(GRANTS)
    every_flag = {
        option
        for _, parser in _commands(root)
        for action in parser._actions
        for option in action.option_strings
    }
    assert set(GRANTS) <= every_flag


def _slug(heading: str) -> str:
    text = unicodedata.normalize("NFKD", heading).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def test_docs_anchors_exist() -> None:
    """Every entry links to an existing page and heading."""
    for docs in {entry.docs for entry in ENTRIES}:
        page, _, anchor = docs.partition("#")
        text = (ROOT / page).read_text(encoding="utf-8")
        if anchor:
            headings = re.findall(r"^#+ (.+)$", text, flags=re.MULTILINE)
            assert anchor in {_slug(h) for h in headings}, docs


def test_generated_page_is_current() -> None:
    """Docs/configuration.md equals a fresh render byte for byte."""
    assert (ROOT / "docs/configuration.md").read_text(encoding="utf-8") == render_markdown(), (
        "docs/configuration.md is stale: run "
        "`uv run rastro config --output markdown > docs/configuration.md`"
    )


@pytest.mark.parametrize("all_variants", [False, True])
def test_template_validates_as_printed(all_variants: bool) -> None:
    """The uncommented lines of the template are a valid job."""
    job = parse_job(render_template("test", all_variants=all_variants), format="toml")
    assert job.acquisition is not None and job.acquisition.allowed_hosts == ("example.com",)


def test_template_lines_uncomment_to_valid_toml() -> None:
    """Every commented key with a default parses; uncommenting a section still
    validates."""
    text = render_template("test", all_variants=True)
    for line in text.splitlines():
        if re.match(r"# [a-z_]+ = [^<]", line):
            tomllib.loads(line.removeprefix("# "))
    acquisition = text.split("[acquisition]\n", 1)[1].split("\n\n", 1)[0]
    uncommented = re.sub(r"^# ([a-z_]+ = [^<])", r"\1", acquisition, flags=re.MULTILINE)
    job = parse_job(text.replace(acquisition, uncommented), format="toml")
    assert job.acquisition is not None and job.acquisition.max_requests == 30
    assert "# [spider]" in text and "# [[consistency]]" in text and '# strategy = "json"' in text


def test_template_command_output_passes_job_validate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`rastro job template` output passes `rastro job validate`."""
    assert main(["job", "template"]) == 0
    path = tmp_path / "job.toml"
    path.write_text(capsys.readouterr().out, encoding="utf-8")
    assert main(["job", "validate", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


@pytest.mark.parametrize("flag", ["json", "markdown"])
def test_config_command_prints_each_format(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    """`rastro config` prints JSON or the docs page."""
    assert main(["config", "--output", flag]) == 0
    out = capsys.readouterr().out
    if flag == "json":
        payload = json.loads(out)
        assert payload["rastro_version"] == rastro.__version__
        assert len(payload["entries"]) == len(ENTRIES)
    else:
        assert out == render_markdown()


@pytest.mark.asyncio
async def test_mcp_resource_serves_the_reference() -> None:
    """Rastro://configuration is registered next to rastro://capabilities."""
    pytest.importorskip("mcp")
    from rastro.adapters.mcp_server import INSTRUCTIONS, build_server

    server = build_server(ROOT / "docs")
    resources = {str(item.uri) for item in await server.list_resources()}
    assert {"rastro://capabilities", "rastro://configuration"} <= resources
    [content] = await server.read_resource("rastro://configuration")
    assert content.content == render_json(rastro.__version__)
    assert "rastro://configuration" in INSTRUCTIONS


def test_snippet_has_the_configuration_map() -> None:
    """The agent snippet maps every job section and states the no-invention rule."""
    snippet = render_snippet("test")
    assert "check `rastro config`" in snippet and "unknown keys" in snippet
    jobs = [e.path for e in ENTRIES if e.kind == "job" and "." in e.path]
    assert set(SECTIONS) == {path.split(".")[0].removesuffix("[]") for path in jobs}
    assert all(f"| `{name}` |" in snippet for name in SECTIONS)


def _validate_error(tmp_path: Path, extra: str, capsys: pytest.CaptureFixture[str]) -> Any:
    job = render_template("test").replace("[acquisition]\n", f"[acquisition]\n{extra}\n", 1)
    path = tmp_path / "job.toml"
    path.write_text(job, encoding="utf-8")
    assert main(["job", "validate", str(path)]) == 2
    return json.loads(capsys.readouterr().err)


def test_unknown_key_suggests_the_closest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown key names the closest valid key and its section."""
    error = _validate_error(tmp_path, 'renderr = "browser"', capsys)
    assert error["hint"] == 'Unknown key "renderr" in [acquisition]; did you mean "render"?'
    error = _validate_error(tmp_path, "zzzz = 1", capsys)
    assert error["hint"].startswith('Unknown key "zzzz" in [acquisition]; valid keys: allow_http')
    assert len(error["hint"]) <= 300


def test_spider_seed_order_unknown_value_and_key_suggest_closest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unknown seed_order value and typo key suggest the closest valid one."""
    base = render_template("test")
    bad_value = tmp_path / "bad-val.toml"
    bad_value.write_text(
        base + '\n[spider]\nmode = "spider"\nseed = "sitemap"\nseed_order = "revers"\n',
        encoding="utf-8",
    )
    assert main(["job", "validate", str(bad_value)]) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["location"] == "spider.seed_order"
    assert 'did you mean "reverse"?' in payload["message"]

    bad_key = tmp_path / "bad-key.toml"
    bad_key.write_text(
        base + '\n[spider]\nmode = "spider"\nseed = "sitemap"\nseed_ordr = "reverse"\n',
        encoding="utf-8",
    )
    assert main(["job", "validate", str(bad_key)]) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["hint"] == 'Unknown key "seed_ordr" in [spider]; did you mean "seed_order"?'


def test_unknown_key_in_nested_and_variant_tables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Union tags and list indexes in the location are skipped."""
    job = render_template("test").replace('record_selector = "article"', "record_selectr = 1")
    path = tmp_path / "job.toml"
    path.write_text(job, encoding="utf-8")
    assert main(["job", "validate", str(path)]) == 2
    hint = json.loads(capsys.readouterr().err)["hint"]
    assert hint == 'Unknown key "record_selectr" in [recipe]; did you mean "record_selector"?'


def test_unknown_key_hint_never_echoes_unsafe_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A key outside [A-Za-z0-9_-]{1,64} is not repeated in the hint."""
    for key in ('"rend er"', "k" * 65, '"ign\\u202eore"'):
        error = _validate_error(tmp_path, f"{key} = 1", capsys)
        assert error["hint"] == (
            "Unknown key in [acquisition]; `rastro config` lists every valid key"
        )


def test_the_reference_does_not_depend_on_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #147: the studio --root default once embedded the checkout path."""
    reference.cache_clear()
    here = render_markdown()
    monkeypatch.chdir(tmp_path)
    reference.cache_clear()
    try:
        assert render_markdown() == here
        assert str(tmp_path) not in render_markdown()
    finally:
        reference.cache_clear()
