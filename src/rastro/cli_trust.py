"""Operator opt-in flags for every command (SEC-004/005/011).

``--allow-remote``, ``--allow-command`` and ``--allow-env NAME`` may appear before or
after the subcommand (``rastro mcp --allow-remote``); they are hoisted to the root parser
and apply process-wide, so Studio and MCP servers inherit them.
"""

from __future__ import annotations

import argparse

from rastro.application.operator_trust import env_names, set_process_trust
from rastro.domain.model_trust import OperatorTrust

_FLAGS = frozenset({"--allow-remote", "--allow-command"})
_VALUED = "--allow-env"


def add_trust_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("operator opt-ins for models declared in job files")
    group.add_argument(
        "--allow-remote",
        action="store_true",
        help="Let job-declared models use non-loopback endpoints (page content leaves)",
    )
    group.add_argument(
        "--allow-command", action="store_true", help="Let job-declared models run a command"
    )
    group.add_argument(
        "--allow-env",
        action="append",
        default=[],
        metavar="NAME",
        help="Let job files read this environment variable (repeatable)",
    )


def hoist_trust_flags(argv: list[str]) -> list[str]:
    """Move the opt-in flags in front of the subcommand; ``--`` ends the search."""
    hoisted: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--":
            rest.extend(argv[index:])
            break
        if item in _FLAGS or item.startswith(_VALUED + "="):
            hoisted.append(item)
        elif item == _VALUED and index + 1 < len(argv):
            hoisted.extend(argv[index : index + 2])
            index += 1
        else:
            rest.append(item)
        index += 1
    return hoisted + rest


def apply_trust(args: argparse.Namespace, argv: list[str]) -> OperatorTrust:
    """Install the parsed opt-ins process-wide; returns the previous value.

    Subcommands with their own ``--allow-remote`` (``studio``, ``suggest``, ``recipe
    repair``) see the hoisted flag too, so it means the same thing everywhere.
    """
    head = argv[: argv.index("--")] if "--" in argv else argv
    if "--allow-remote" in head:
        args.allow_remote = True
    return set_process_trust(
        OperatorTrust(
            allow_remote=bool(getattr(args, "allow_remote", False)),
            allow_command=bool(getattr(args, "allow_command", False)),
            allowed_env=env_names(getattr(args, "allow_env", None) or ()),
        )
    )
