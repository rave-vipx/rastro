"""`rastro model detect|test`: find local models and check a model works (cost shown)."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from rastro.application.model_budget import price_per_call
from rastro.application.model_choice import resolve_model
from rastro.application.model_detect import detect_local_models
from rastro.cli_model import add_cost_argument
from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest
from rastro.domain.job_results import JobExitCode
from rastro.model_factory import is_remote

_PING = ModelRequest(
    system="Reply with a JSON object only.",
    content='{"task": "reply with {\\"ok\\": true}"}',
    response_schema={
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    },
)


def add_command(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    model = subcommands.add_parser("model", help="Find local AI models and test a model")
    actions = model.add_subparsers(dest="model_command", required=True)
    actions.add_parser("detect", help="List models running on this machine (loopback only)")
    test = actions.add_parser("test", help="Make one small call and show what it costs")
    test.add_argument("--model", metavar="SPEC", help="Model spec, or 'env' for RASTRO_MODEL")
    test.add_argument("--allow-remote", action="store_true", help="Allow a hosted model")
    add_cost_argument(test)


def model_command(args: argparse.Namespace) -> int:
    if args.model_command == "detect":
        found = detect_local_models()
        print(json.dumps({"models": [d.model_dump(mode="json") for d in found]}, indent=2))
        return int(JobExitCode.SUCCESS)
    return _test(args)


def _test(args: argparse.Namespace) -> int:
    port = resolve_model(args.model or "env", allow_remote=args.allow_remote)
    assert port is not None
    policy = InferencePolicy(max_calls=1)
    started = time.monotonic()
    error: str | None = None
    try:
        asyncio.run(port.generate(_PING, policy), loop_factory=lambda: asyncio.SelectorEventLoop())
    except ModelError as failure:
        error = str(failure)
    price = price_per_call()
    out: dict[str, Any] = {
        "ok": error is None,
        "remote": is_remote(port),
        "calls": 1,
        "cost_per_call": price,
        "seconds": round(time.monotonic() - started, 3),
    }
    if error is not None:
        out["error"] = error
    print(json.dumps(out, indent=2))
    return int(JobExitCode.SUCCESS if error is None else JobExitCode.FAILED)
