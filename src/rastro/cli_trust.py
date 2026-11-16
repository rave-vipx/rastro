"""Operator opt-in flags for every command (security audit H1, SEC-004/005/011).

``--allow-remote``, ``--allow-egress``, ``--allow-env NAME`` and the network permissions
``--allow-private-network``, ``--allow-port N`` and ``--ignore-robots HOST`` may appear before
or after the subcommand (``rastro mcp --allow-remote``); they are hoisted to the root parser and
apply process-wide, so Studio and MCP servers inherit them. A job file can never make
Rastro start a local command; a command model comes only from operator configuration.
"""

from __future__ import annotations

import argparse
from typing import Any

from rastro.application.operator_trust import (
    env_hosts,
    env_names,
    env_ports,
    set_process_trust,
)
from rastro.domain.model_trust import OperatorTrust

_FLAGS = frozenset(
    {"--allow-remote", "--allow-egress", "--allow-stealth", "--allow-private-network"}
)
_VALUED = ("--allow-env", "--allow-port", "--ignore-robots")


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
        "--allow-stealth",
        action="store_true",
        help="Allow stealth browser rendering (also RASTRO_ALLOW_STEALTH=1)",
        **default,
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
        help="Let the job's model keys and egress proxy credentials read this environment "
        "variable besides RASTRO_* (repeatable; also RASTRO_ALLOWED_ENV)",
        **({"default": []} if root else default),
    )
    group.add_argument(
        "--allow-private-network",
        action="store_true",
        help="Reach private, loopback and CGNAT addresses of allowed hosts; link-local and "
        "cloud metadata stay blocked (also RASTRO_ALLOW_PRIVATE_NETWORK=1)",
        **default,
    )
    group.add_argument(
        "--allow-port",
        action="append",
        metavar="N",
        help="Also allow this port besides 80/443 (repeatable; also RASTRO_ALLOWED_PORTS)",
        **({"default": []} if root else default),
    )
    group.add_argument(
        "--ignore-robots",
        action="append",
        metavar="HOST",
        help="Do not fetch or enforce robots.txt for this exact host: you assert you have "
        "permission (repeatable; also RASTRO_IGNORE_ROBOTS)",
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
        if item in _FLAGS or item.startswith(tuple(f"{name}=" for name in _VALUED)):
            hoisted.append(item)
        elif item in _VALUED and index + 1 < len(argv):
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
            allow_stealth=bool(getattr(args, "allow_stealth", False)),
            allowed_env=env_names(getattr(args, "allow_env", None) or ()),
            allow_private_network=bool(getattr(args, "allow_private_network", False)),
            allowed_ports=env_ports(getattr(args, "allow_port", None) or ()),
            ignore_robots=env_hosts(getattr(args, "ignore_robots", None) or ()),
        )
    )


def trust_options(args: argparse.Namespace) -> dict[str, Any]:
    """The egress opt-ins passed explicitly to job, dataset, repair, MCP and Studio entries."""
    return {
        "allow_egress": bool(getattr(args, "allow_egress", False)),
        "allow_env": list(getattr(args, "allow_env", None) or ()),
    }
