"""`rastro init --agent` writes the agent block or skill safely (N18, AGENT-001, SEC-008)."""

import contextlib
import json
from pathlib import Path

import pytest

from rastro.capabilities import GAP_REPORT_TEMPLATE, PUBLIC_IMPORT_RULE, UNTRUSTED_DATA_RULE
from rastro.capabilities_render import render_snippet
from rastro.cli import main
from rastro.cli_agent import _ANCHORED, init_project
from rastro.domain.models import ConfigurationError

# init writes only where directory-relative operations exist (POSIX); elsewhere it fails closed.
needs_dir_fd = pytest.mark.skipif(not _ANCHORED, reason="init needs dir_fd (POSIX only)")

BEGIN, END = "<!-- rastro:begin -->", "<!-- rastro:end -->"


def _init(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, str]]:
    code = main(["init", *args])
    captured = capsys.readouterr()
    return code, json.loads(captured.out or captured.err)


def test_snippet_carries_the_rules() -> None:
    """N18: public imports, the gap template and the untrusted-data rule, in that order."""
    snippet = render_snippet("0.0.0")
    positions = [
        snippet.index(text)
        for text in ("| Task |", PUBLIC_IMPORT_RULE, GAP_REPORT_TEMPLATE, UNTRUSTED_DATA_RULE)
    ]
    assert positions == sorted(positions)
    assert "MCP job_load" in snippet and "Not Rastro's job" in snippet


@pytest.mark.parametrize(("agent", "name"), [("claude", "CLAUDE.md"), ("codex", "AGENTS.md")])
@needs_dir_fd
def test_block_is_idempotent_and_keeps_other_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], agent: str, name: str
) -> None:
    """N18: the block is appended once, replaced in place and the rest is untouched."""
    target = tmp_path / name
    target.write_text("# My project\n\nKeep this.", encoding="utf-8")
    code, result = _init(capsys, "--agent", agent, "--path", str(tmp_path))
    assert code == 0 and result == {
        "agent": agent,
        "format": "block",
        "path": name,
        "action": "updated",
    }
    first = target.read_text(encoding="utf-8")
    assert first.startswith("# My project\n\nKeep this.\n\n" + BEGIN)
    assert first.count(BEGIN) == 1 and first.endswith(END + "\n")
    target.write_text(first.replace(BEGIN + "\n", BEGIN + "\nold text\n") + "Footer\n")
    assert _init(capsys, "--agent", agent, "--path", str(tmp_path))[1]["action"] == "updated"
    again = target.read_text(encoding="utf-8")
    assert again == first + "Footer\n"
    assert _init(capsys, "--agent", agent, "--path", str(tmp_path))[1]["action"] == "unchanged"
    assert list(tmp_path.iterdir()) == [target]  # no temporary files left


@needs_dir_fd
def test_block_creates_a_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """N18: generic agents get AGENTS.md, created when missing."""
    code, result = _init(capsys, "--agent", "generic", "--path", str(tmp_path))
    assert code == 0 and result["action"] == "created"
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8").startswith(BEGIN)


@needs_dir_fd
def test_skill_has_frontmatter(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """N18: --format skill writes .agents/skills/rastro/SKILL.md with name and description."""
    code, result = _init(capsys, "--agent", "codex", "--path", str(tmp_path), "--format", "skill")
    assert code == 0 and result["path"] == ".agents/skills/rastro/SKILL.md"
    text = (tmp_path / result["path"]).read_text(encoding="utf-8")
    assert text.startswith("---\nname: rastro\ndescription: ")
    assert PUBLIC_IMPORT_RULE in text


@needs_dir_fd
def test_refuses_symlink_targets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """SEC-008: a symlinked file or directory is never followed; the outside file is unchanged."""
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").symlink_to(outside)
    code, error = _init(capsys, "--agent", "claude", "--path", str(project))
    assert code == 2 and error["error"] == "configuration_error"
    (project / ".agents").symlink_to(tmp_path)
    code, _ = _init(capsys, "--agent", "claude", "--path", str(project), "--format", "skill")
    assert code == 2 and not (tmp_path / "skills").exists()
    assert outside.read_text(encoding="utf-8") == "secret\n"


@pytest.mark.parametrize(
    "text",
    [BEGIN + "\nno end\n", END + "\n" + BEGIN + "\n", BEGIN + END + BEGIN + END, "x\0\xff"],
)
@needs_dir_fd
def test_refuses_unbalanced_markers_and_bad_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    """N18: ambiguous markers fail closed with exit 2 and leave the file as it was."""
    target = tmp_path / "AGENTS.md"
    target.write_bytes(text.encode("latin-1") if "\xff" in text else text.encode())
    before = target.read_bytes()
    code, error = _init(capsys, "--agent", "codex", "--path", str(tmp_path))
    assert code == 2 and error["error"] == "configuration_error"
    assert target.read_bytes() == before


def test_refuses_a_missing_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """N18: --path must be an existing directory."""
    code, error = _init(capsys, "--agent", "codex", "--path", str(tmp_path / "missing"))
    assert code == 2 and error["error"] == "configuration_error"


def _swap_for_link(monkeypatch: pytest.MonkeyPatch, directory: Path, outside: Path) -> None:
    """Swap ``directory`` for a symlink to ``outside`` just before the temporary file opens."""

    def swap(_size: int) -> str:
        directory.rename(directory.with_name(directory.name + "-moved"))
        directory.symlink_to(outside, target_is_directory=True)
        return "race"

    monkeypatch.setattr("rastro.cli_agent.secrets.token_hex", swap)


@needs_dir_fd
def test_skill_directory_swap_cannot_write_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-008: a parent swapped for a symlink mid-write never redirects the write outside."""
    project, outside = tmp_path / "project", tmp_path / "outside"
    target = project / ".agents" / "skills" / "rastro"
    target.mkdir(parents=True)
    outside.mkdir()
    (outside / "SKILL.md").write_text("private\n", encoding="utf-8")
    _swap_for_link(monkeypatch, target, outside)
    with contextlib.suppress(ConfigurationError):
        init_project(project, "codex", "skill")
    assert (outside / "SKILL.md").read_text(encoding="utf-8") == "private\n"
    assert sorted(p.name for p in outside.iterdir()) == ["SKILL.md"]


@needs_dir_fd
def test_block_root_swap_cannot_write_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-008: --path swapped for a symlink mid-write never redirects the write outside."""
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (outside / "AGENTS.md").write_text("private\n", encoding="utf-8")
    _swap_for_link(monkeypatch, project, outside)
    with contextlib.suppress(ConfigurationError):
        init_project(project, "codex", "block")
    assert (outside / "AGENTS.md").read_text(encoding="utf-8") == "private\n"
    assert sorted(p.name for p in outside.iterdir()) == ["AGENTS.md"]


def test_without_dir_fd_init_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PR #145 re-check, SEC-008: without dir_fd (Windows) init writes nothing and says why."""
    monkeypatch.setattr("rastro.cli_agent._ANCHORED", False)
    project = tmp_path / "project"
    project.mkdir()
    for agent, fmt in (("codex", "skill"), ("claude", "block")):
        with pytest.raises(ConfigurationError, match="rastro capabilities --output markdown"):
            init_project(project, agent, fmt)
    assert list(project.iterdir()) == []


def test_snippet_tells_the_agent_how_results_reach_studio() -> None:
    """N18, Rave adoption: results go through a job dataset; no ad hoc database for Studio."""
    from rastro.capabilities_render import render_llms
    from rastro.config_catalog import STUDIO_RESULTS_RULE

    snippet = render_snippet("0.0.0")
    assert STUDIO_RESULTS_RULE in snippet and STUDIO_RESULTS_RULE in render_llms()
    assert "rastro.load_records" in STUDIO_RESULTS_RULE
    assert snippet.index(STUDIO_RESULTS_RULE) < snippet.index(PUBLIC_IMPORT_RULE)
