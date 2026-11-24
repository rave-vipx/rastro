"""Shared ``--model``, ``--allow-remote`` and model cost options."""

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
    add_cost_argument(parser)


def add_cost_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-model-cost",
        type=float,
        metavar="USD",
        help="Stop calling the model once this cost would be exceeded (needs "
        "--model-cost-per-call or the job's [model] cost_per_call; overrides max_cost)",
    )
    parser.add_argument(
        "--model-cost-per-call",
        type=float,
        metavar="USD",
        help="Price of one model call; overrides the job's [model] cost_per_call",
    )
