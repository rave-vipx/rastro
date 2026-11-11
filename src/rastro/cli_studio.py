"""`rastro studio`: serve the local Studio on loopback (P-008)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rastro.domain.models import ConfigurationError

HOST = "127.0.0.1"


def add_command(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    studio = subcommands.add_parser("studio", help="Open the local Studio (needs rastro[studio])")
    studio.add_argument("--root", type=Path, default=Path.cwd())
    studio.add_argument("--port", type=int, default=8765)


def studio_command(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from rastro.studio.app import create_app
    except ImportError:
        print(
            json.dumps({"error": "missing_extra", "hint": "pip install 'rastro-engine[studio]'"}),
            file=sys.stderr,
        )
        return 2
    if not 1 <= args.port <= 65_535:
        raise ConfigurationError("Port must be between 1 and 65535")
    app, _ = create_app(args.root)
    print(f"Rastro Studio on http://{HOST}:{args.port}/  (Ctrl+C to stop)", file=sys.stderr)
    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
    return 0
