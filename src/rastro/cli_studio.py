"""`rastro studio`: serve the local Studio on loopback (P-008)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rastro.application.errors import missing_extra_error
from rastro.cli_trust import add_trust_arguments, trust_options
from rastro.domain.models import ConfigurationError
from rastro.ports.model import ModelPort

HOST = "127.0.0.1"


def add_command(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    studio = subcommands.add_parser("studio", help="Open the local Studio (needs rastro[studio])")
    studio.add_argument("--root", type=Path, default=Path.cwd())
    studio.add_argument("--port", type=int, default=8765)
    add_trust_arguments(studio)
    studio.add_argument(
        "--model", help="Model spec for Ask AI (default: RASTRO_MODEL); any provider works"
    )
    studio.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow sending page content to a non-loopback or hosted model",
    )


def _model(spec: str | None, allow_remote: bool) -> ModelPort | None:
    """The Ask AI model from ``--model`` or ``RASTRO_MODEL``; ``None`` when neither is set.

    Resolved through the provider-agnostic ``rastro.model`` factory, which enforces the
    remote opt-in.
    """
    if not spec and not os.environ.get("RASTRO_MODEL", "").strip():
        return None  # --allow-remote still applies to models declared in job files
    import rastro

    factory = getattr(rastro, "model", None)
    if factory is None:
        raise ConfigurationError("Ask AI needs the rastro.model factory (Rastro 0.6 or later)")
    port: ModelPort = factory(spec, allow_remote=allow_remote)
    return port


def studio_command(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from rastro.studio.app import create_app
    except ImportError:
        print(json.dumps(missing_extra_error("studio")), file=sys.stderr)
        return 2
    if not 1 <= args.port <= 65_535:
        raise ConfigurationError("Port must be between 1 and 65535")
    model = _model(args.model, args.allow_remote)
    app, token = create_app(
        args.root, model=model, model_remote=args.allow_remote, **trust_options(args)
    )
    url = f"http://{HOST}:{args.port}/#token={token}"
    print(f"Rastro Studio: open {url}  (Ctrl+C to stop)", file=sys.stderr)
    if model is not None:
        where = "may go to a remote model" if args.allow_remote else "stays on loopback models"
        print(f"Ask AI is on; page content {where}.", file=sys.stderr)
    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
    return 0
