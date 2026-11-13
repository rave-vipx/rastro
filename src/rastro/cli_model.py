"""Shared ``--model`` and ``--allow-remote`` options for CLI commands (AI-001, P-001)."""

from __future__ import annotations

import argparse


def add_model_options(parser: argparse.ArgumentParser, purpose: str) -> None:
    parser.add_argument(
        "--model",
        metavar="SPEC",
        help=f"Model spec for rastro.model, or 'env' for RASTRO_MODEL: {purpose}",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow sending page content to a non-loopback or hosted model",
    )
