"""`rastro capabilities` and `rastro init --agent`: tell a coding agent what Rastro does (N18,
AGENT-001, SEC-008). `init` writes only inside ``--path``, never follows symlinks and replaces
files atomically; failures are ``ConfigurationError`` (JSON on stderr, exit 2).

On POSIX every step is anchored to directory descriptors opened with ``O_NOFOLLOW``, so a
directory swapped for a symlink mid-run cannot redirect the write. Where ``dir_fd`` is
unsupported (Windows) the path checks remain but that race guard does not apply."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import sys
from pathlib import Path

from rastro import JobExitCode, __version__
from rastro.capabilities_render import (
    render_json,
    render_llms,
    render_markdown,
    render_snippet,
)
from rastro.domain.models import ConfigurationError

COMMANDS = frozenset({"capabilities", "init"})
BEGIN, END = "<!-- rastro:begin -->", "<!-- rastro:end -->"
MAX_FILE_BYTES = 1_048_576
_BLOCK_FILES = {"claude": "CLAUDE.md", "codex": "AGENTS.md", "generic": "AGENTS.md"}
_SKILL = Path(".agents/skills/rastro/SKILL.md")
# os.replace shares os.rename's dir_fd support but is not listed in os.supports_dir_fd.
_ANCHORED = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and {os.open, os.mkdir, os.rename, os.unlink} <= os.supports_dir_fd
)
_SKILL_HEAD = (
    "---\nname: rastro\ndescription: Use when the task scrapes, crawls, monitors or extracts "
    "web data in this project. Lists what Rastro already does so you use it instead of "
    "rebuilding it.\n---\n\n# Rastro\n\n"
)


def add_commands(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    caps = subcommands.add_parser("capabilities", help="Print what Rastro can do, for agents")
    caps.add_argument("--output", choices=("json", "markdown", "llms"), default="json")
    init = subcommands.add_parser("init", help="Write Rastro instructions for a coding agent")
    init.add_argument("--agent", choices=sorted(_BLOCK_FILES), required=True)
    init.add_argument("--path", type=Path, default=Path(), help="Project directory (default: cwd)")
    init.add_argument("--format", choices=("block", "skill"), default="block")


def agent_command(args: argparse.Namespace) -> int:
    if args.command == "capabilities":
        render = {"json": lambda: render_json(__version__), "markdown": render_markdown}
        sys.stdout.write(render.get(args.output, render_llms)())
    else:
        print(json.dumps(init_project(args.path, args.agent, args.format)))
    return int(JobExitCode.SUCCESS)


def init_project(root: Path, agent: str, fmt: str) -> dict[str, str]:
    """Write the agent block or skill under ``root``; report the file and what changed."""
    if root.is_symlink() or not root.is_dir():
        raise ConfigurationError("--path must be an existing directory, not a symbolic link")
    relative = _SKILL if fmt == "skill" else Path(_BLOCK_FILES[agent])
    snippet = render_snippet(__version__)
    dir_fd, parent = _open_parent(root, relative)
    try:
        old = _read(dir_fd, parent / relative.name)
        if fmt == "skill":
            new = _SKILL_HEAD + snippet.rstrip("\n") + "\n"
        else:
            new = _with_block(old or "", f"{BEGIN}\n## Rastro\n\n{snippet.rstrip()}\n{END}")
        action = "created" if old is None else "unchanged" if old == new else "updated"
        if action != "unchanged":
            _write_atomic(dir_fd, parent / relative.name, new.encode("utf-8"))
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
    return {"agent": agent, "format": fmt, "path": relative.as_posix(), "action": action}


def _open_parent(root: Path, relative: Path) -> tuple[int | None, Path]:
    """Return the target's parent as ``(dir_fd, Path())`` or, without dir_fd, ``(None, path)``."""
    if not _ANCHORED:
        _refuse_links(root, relative)
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        _refuse_links(root, relative)
        return None, (root / relative).parent
    fd = _open_dir(root, None)
    try:
        for name in relative.parent.parts:
            with contextlib.suppress(FileExistsError):
                os.mkdir(name, 0o755, dir_fd=fd)
            child = _open_dir(name, fd)
            os.close(fd)
            fd = child
    except OSError:
        os.close(fd)
        raise ConfigurationError(f"{relative.parent} cannot be created") from None
    except BaseException:
        os.close(fd)
        raise
    return fd, Path()


def _open_dir(name: str | Path, dir_fd: int | None) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError:
        raise ConfigurationError(
            "Symbolic links are not followed; every path component must be a directory"
        ) from None


def _with_block(text: str, block: str) -> str:
    begins, ends = text.count(BEGIN), text.count(END)
    if begins == ends == 0:
        if not text:
            return f"{block}\n"
        return text + ("\n" if text.endswith("\n") else "\n\n") + block + "\n"
    start, stop = text.find(BEGIN), text.find(END)
    if begins != 1 or ends != 1 or stop < start:
        raise ConfigurationError("The rastro:begin/rastro:end markers are unbalanced; fix them")
    return text[:start] + block + text[stop + len(END) :]


def _refuse_links(root: Path, relative: Path) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ConfigurationError("Symbolic links are not followed")
        if current != root / relative and current.exists() and not current.is_dir():
            raise ConfigurationError("A path component is not a directory")


def _read(dir_fd: int | None, path: Path) -> str | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags, dir_fd=dir_fd), "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError:
        raise ConfigurationError(f"{path.name} cannot be read") from None
    if len(data) > MAX_FILE_BYTES:
        raise ConfigurationError(f"{path.name} exceeds {MAX_FILE_BYTES} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationError(f"{path.name} is not UTF-8") from None


def _write_atomic(dir_fd: int | None, path: Path, data: bytes) -> None:
    temp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(temp, flags, 0o644, dir_fd=dir_fd), "wb") as handle:
            handle.write(data)
        os.replace(temp, path, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except OSError:
        raise ConfigurationError(f"{path.name} cannot be written") from None
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=dir_fd)
