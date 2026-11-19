"""The release workflow publishes a tag only from main or from its maintenance branch."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

WORKFLOW = Path(".github/workflows/release.yml")
RELEASING = Path("docs/releasing.md")
STABILITY = Path("docs/stability.md")
TAG_GUARD = "Require the tagged commit to be on main or its release branch"

_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _dedent(lines: list[str]) -> str:
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    margin = min(indents, default=0)
    return "\n".join(line[margin:] if line.strip() else "" for line in lines)


def _run_scripts(text: str) -> dict[str, str]:
    """Map each named step of a workflow to the shell script it runs."""
    scripts: dict[str, str] = {}
    lines = text.splitlines()
    name: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("- name: "):
            name = stripped.removeprefix("- name: ").strip()
        elif name is not None and stripped == "run: |":
            indent = len(line) - len(line.lstrip())
            block: list[str] = []
            index += 1
            while index < len(lines):
                candidate = lines[index]
                if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= indent:
                    break
                block.append(candidate)
                index += 1
            scripts[name] = _dedent(block)
            name = None
            continue
        index += 1
    return scripts


def _git(cwd: Path, *arguments: str) -> str:
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release@example.test",
        "GIT_COMMITTER_NAME": "Release Test",
        "GIT_COMMITTER_EMAIL": "release@example.test",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, env=environment, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@dataclass(frozen=True)
class Branches:
    """A clone of an origin whose main and maintenance branches have diverged."""

    clone: Path
    main: str
    released: str
    fix: str
    unrelated: str


@pytest.fixture
def branches(tmp_path: Path) -> Branches:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    (seed / "app.txt").write_text("released\n", encoding="utf-8")
    _git(seed, "add", "app.txt")
    _git(seed, "commit", "-m", "start the line")
    released = _git(seed, "rev-parse", "HEAD")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "origin", "main")

    _git(seed, "switch", "-c", "release/1.0")
    (seed / "fix.txt").write_text("fix\n", encoding="utf-8")
    _git(seed, "add", "fix.txt")
    _git(seed, "commit", "-m", "fix a defect")
    fix = _git(seed, "rev-parse", "HEAD")
    _git(seed, "push", "origin", "release/1.0")

    _git(seed, "switch", "main")
    (seed / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(seed, "add", "feature.txt")
    _git(seed, "commit", "-m", "add a feature")
    main = _git(seed, "rev-parse", "HEAD")
    _git(seed, "push", "origin", "main")

    _git(seed, "switch", "-c", "scratch")
    (seed / "scratch.txt").write_text("scratch\n", encoding="utf-8")
    _git(seed, "add", "scratch.txt")
    _git(seed, "commit", "-m", "unmerged work")
    unrelated = _git(seed, "rev-parse", "HEAD")
    _git(seed, "push", "origin", "scratch")

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "--quiet", str(origin), str(clone))
    return Branches(clone=clone, main=main, released=released, fix=fix, unrelated=unrelated)


def _check_tag(branches: Branches, *, tag: str, commit: str) -> subprocess.CompletedProcess[str]:
    script = _run_scripts(WORKFLOW.read_text(encoding="utf-8"))[TAG_GUARD]
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=branches.clone,
        env={**os.environ, "GITHUB_SHA": commit, "GITHUB_REF_NAME": tag},
        capture_output=True,
        text=True,
    )


def test_a_patch_tag_from_the_maintenance_branch_is_accepted(branches: Branches) -> None:
    result = _check_tag(branches, tag="v1.0.1", commit=branches.fix)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_patch_tag_from_main_is_accepted(branches: Branches) -> None:
    result = _check_tag(branches, tag="v1.0.1", commit=branches.main)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_minor_tag_from_main_is_accepted(branches: Branches) -> None:
    result = _check_tag(branches, tag="v1.1.0", commit=branches.main)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_minor_tag_from_the_maintenance_branch_is_rejected(branches: Branches) -> None:
    result = _check_tag(branches, tag="v1.1.0", commit=branches.fix)
    assert result.returncode != 0
    assert "main" in result.stdout + result.stderr


def test_a_patch_tag_from_an_unrelated_branch_is_rejected(branches: Branches) -> None:
    result = _check_tag(branches, tag="v1.0.1", commit=branches.unrelated)
    assert result.returncode != 0
    assert "release/1.0" in result.stdout + result.stderr


def test_a_patch_tag_for_a_missing_maintenance_branch_is_rejected(branches: Branches) -> None:
    result = _check_tag(branches, tag="v1.2.1", commit=branches.unrelated)
    assert result.returncode != 0


def test_a_tag_that_is_not_a_version_is_rejected(branches: Branches) -> None:
    result = _check_tag(branches, tag="v1.1", commit=branches.main)
    assert result.returncode != 0


def test_every_action_is_pinned_to_a_full_commit() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    actions = re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)", text, flags=re.MULTILINE)
    assert actions
    for action in actions:
        assert re.search(r"@[0-9a-f]{40}$", action), f"unpinned action: {action}"


def test_the_workflow_keeps_the_tag_trigger_and_the_publishing_contract() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "tags: ['v*']" in text
    scripts = _run_scripts(text)
    assert "Require the tag to match the package version" in scripts
    assert "Require a dated changelog section for this version" in scripts
    assert "name: pypi" in text
    assert "id-token: write" in text


def test_the_releasing_guide_documents_the_patch_path() -> None:
    text = RELEASING.read_text(encoding="utf-8")
    assert "release/1.0" in text
    assert "cherry-pick" in text
    assert "no feature freeze" in text.lower()


def test_the_stability_policy_lists_the_one_dot_zero_freeze() -> None:
    text = STABILITY.read_text(encoding="utf-8")
    assert "## The 1.0.0 freeze" in text
    for expected in ("rastro.__all__", "format_version = 1", "run-report.schema.json"):
        assert expected in text


@pytest.mark.parametrize("guide", [RELEASING, STABILITY])
def test_guide_relative_links_resolve(guide: Path) -> None:
    text = guide.read_text(encoding="utf-8")
    targets = _MARKDOWN_LINK.findall(text)
    assert targets
    missing = [
        target
        for target in targets
        if not target.startswith(("http://", "https://", "mailto:", "#"))
        and not (guide.parent / target.split("#", maxsplit=1)[0]).exists()
    ]
    assert missing == []
