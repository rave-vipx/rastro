"""The capability manifest covers every public surface and its generated files stay current
(N18, AGENT-001, AGENT-002)."""

import argparse
import json
import re
import unicodedata
from pathlib import Path

import pytest

import rastro
from rastro.capabilities import CAPABILITIES, REFERENCE_ONLY
from rastro.capabilities_render import render_llms, render_markdown, render_mcp_instructions
from rastro.cli import _build_parser, main

ROOT = Path(__file__).resolve().parents[2]
PYTHON = {name for cap in CAPABILITIES for name in cap.python}
MCP = {name for cap in CAPABILITIES for name in cap.mcp}
CLI = [command for cap in CAPABILITIES for command in cap.cli]
PROVIDERS = {"OllamaModel", "OpenAICompatibleModel"}  # public, kept out of __all__


@pytest.mark.parametrize(
    ("path", "render", "flag"),
    [("llms.txt", render_llms, "llms"), ("docs/capabilities.md", render_markdown, "markdown")],
)
def test_generated_files_are_current(path: str, render: object, flag: str) -> None:
    """N18: committed outputs equal a fresh render byte for byte."""
    assert callable(render)
    assert (ROOT / path).read_text(encoding="utf-8") == render(), (
        f"{path} is stale: run `uv run rastro capabilities --output {flag} > {path}`"
    )


def _commands(parser: argparse.ArgumentParser, prefix: str = "") -> list[str]:
    found: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                found += [prefix + name, *_commands(sub, prefix + name + " ")]
    return found


def test_every_cli_command_is_a_capability() -> None:
    """N18: each command and subcommand appears in a capability or REFERENCE_ONLY."""
    covered = [*CLI, *REFERENCE_ONLY]
    commands = _commands(_build_parser())
    assert {"capabilities", "init", "job load", "recipe install"} <= set(commands)
    missing = [c for c in commands if not any(f"{x} ".startswith(f"rastro {c} ") for x in covered)]
    assert missing == []
    for command in CLI:  # and nothing invented
        assert " ".join(command.split()[1:3]) in commands or command.split()[1] in commands


def test_every_public_name_is_listed_and_exists() -> None:
    """N18: rastro.__all__ is covered; listed names are real public names."""
    assert set(rastro.__all__) - PYTHON - set(REFERENCE_ONLY) == set()
    assert PYTHON - set(rastro.__all__) - PROVIDERS == set()


@pytest.mark.asyncio
async def test_every_mcp_tool_is_listed() -> None:
    """N18, AGENT-002: registered tools appear; listed tools exist; resource serves JSON."""
    pytest.importorskip("mcp")
    from rastro.adapters.mcp_server import INSTRUCTIONS, build_server

    server = build_server(ROOT / "docs")
    tools = {tool.name for tool in await server.list_tools()}
    assert tools - MCP - set(REFERENCE_ONLY) == set()
    assert MCP - tools == set()
    assert render_mcp_instructions() == INSTRUCTIONS
    [content] = await server.read_resource("rastro://capabilities")
    payload = json.loads(content.content)
    assert payload["rastro_version"] == rastro.__version__
    assert len(payload["capabilities"]) == len(CAPABILITIES)


def _slug(heading: str) -> str:
    text = unicodedata.normalize("NFKD", heading).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def test_docs_links_point_at_real_anchors() -> None:
    """N18: every capability links to an existing page and heading."""
    for cap in CAPABILITIES:
        page, _, anchor = cap.docs.partition("#")
        text = (ROOT / page).read_text(encoding="utf-8")
        if anchor:
            headings = re.findall(r"^#+ (.+)$", text, flags=re.MULTILINE)
            assert anchor in {_slug(h) for h in headings}, cap.docs


def test_entries_are_complete() -> None:
    """N18: each entry says what to use, what not to build and where the docs are."""
    for cap in CAPABILITIES:
        assert cap.task and cap.use and cap.instead_of and cap.docs.startswith("docs/")
        assert all(command.startswith("rastro ") for command in cap.cli)


@pytest.mark.parametrize("flag", ["json", "markdown", "llms"])
def test_capabilities_command_prints_each_format(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """N18: `rastro capabilities --output` prints the manifest; JSON parses."""
    assert main(["capabilities", "--output", flag]) == 0
    out = capsys.readouterr().out
    if flag == "json":
        assert json.loads(out)["schema_version"] == 1
    else:
        assert "Follow links from a listing to detail pages" in out
