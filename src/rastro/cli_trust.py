"""Operator opt-in flags for every command (security audit H1, SEC-004/005/011).

``--allow-remote``, ``--allow-egress`` and ``--allow-env NAME`` may appear before or after
the subcommand (``rastro mcp --allow-remote``); they are hoisted to the root parser and
apply process-wide, so Studio and MCP servers inherit them. A job file can never make
Rastro start a local command; a command model comes only from operator configuration.
"""

from __future__ import annotations

import argparse
from typing import Any

from rastro.application.operator_trust import env_names, set_process_trust
from rastro.domain.model_trust import OperatorTrust

_FLAGS = frozenset({"--allow-remote", "--allow-egress"})
_VALUED = "--allow-env"


def add_trust_arguments(parser: argparse.ArgumentParser, *, root: bool = False) -> None:
    """Root parser: the real flags. Subcommands: the same flags listed for ``--help`` only.

    Hoisting means the root parser always consumes them; subcommand copies default to
    ``SUPPRESS`` so they never overwrite the root's parsed values.
    """
    default: dict[str, Any] = {} if root else {"default": argparse.SUPPRESS}
    group = parser.add_argument_group("operator opt-ins for job files")
    if root:
        group.add_argument(
            "--allow-remote",
            action="store_true",
            help="Let job-declared models use non-loopback endpoints (page content leaves)",
        )
    group.add_argument(
        "--allow-egress",
        action="store_true",
        help="Use the egress proxies the job declares (also RASTRO_ALLOW_EGRESS=1)",
        **default,
    )
    group.add_argument(
        "--allow-env",
        action="append",
        metavar="NAME",
        help="Let the job read this environment variable besides RASTRO_* (repeatable; "
        "also RASTRO_ALLOWED_ENV)",
        **({"default": []} if root else default),
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
            allowed_env=env_names(getattr(args, "allow_env", None) or ()),
        )
    )


def trust_options(args: argparse.Namespace) -> dict[str, Any]:
    """The egress opt-ins passed explicitly to job, dataset, repair, MCP and Studio entries."""
    return {
        "allow_egress": bool(getattr(args, "allow_egress", False)),
        "allow_env": list(getattr(args, "allow_env", None) or ()),
    }
