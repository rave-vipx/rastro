"""REPAIR-002/004, QUAL-005: documented repair stays reproducible after sample dates."""

import runpy
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

CLOCK_MODULES = (
    "rastro.application.extraction",
    "rastro.application.service",
    "rastro.application.memory",
    "rastro.application.outcomes",
    "rastro.application.intelligence",
    "rastro.cli_recipe",
)


def test_repair_demo_succeeds_under_future_wall_clock(tmp_path, capsys, offline_cli_loop):
    demo = runpy.run_path("examples/uy-dance-agenda/repair_demo.py")
    future = datetime(2027, 6, 1, tzinfo=UTC)
    with ExitStack() as stack:
        for name in CLOCK_MODULES:
            clock = stack.enter_context(patch(f"{name}.datetime", wraps=datetime))
            clock.now.return_value = future
        # The documented script owns its clock pin, independently of pytest fixtures.
        demo["run_demo"](tmp_path / "sample")
    output = capsys.readouterr().out
    readme = Path("examples/uy-dance-agenda/README.md").read_text(encoding="utf-8")
    section = readme.split("## Drift and repair offline", 1)[1]
    expected = section.split("```text\n", 1)[1].split("```", 1)[0]
    assert output == expected
