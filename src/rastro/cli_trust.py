"""Operator opt-in flags shared by commands that run a job (security audit H1)."""

import argparse
from typing import Any


def add_trust_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allow-egress",
        action="store_true",
        help="Use the egress proxies the job declares (also RASTRO_ALLOW_EGRESS=1)",
    )
    parser.add_argument(
        "--allow-env",
        action="append",
        default=[],
        metavar="NAME",
        help="Let the job read this environment variable besides RASTRO_* (repeatable; "
        "also RASTRO_ALLOWED_ENV)",
    )


def trust_options(args: argparse.Namespace) -> dict[str, Any]:
    return {"allow_egress": args.allow_egress, "allow_env": args.allow_env}
