"""Write every case as reviewable files under ``cases/`` (QUAL-001).

Each case folder holds ``before.html``, ``after.html``, ``expected.json`` (the records
a correct extraction of the after page returns) and ``case.json`` (kind, base recipe,
correct recipe). ``schema.json`` describes the fields. The files are generated: edit
``layouts.py`` or ``cases.py`` and run ``python -m benchmarks.ai_repair.export``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.ai_repair.cases import CASES
from benchmarks.ai_repair.job import SCHEMA
from benchmarks.ai_repair.layouts import expected, render

ROOT = Path(__file__).parent / "cases"


def _json(value: Any) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False) + "\n"


def files() -> dict[str, str]:
    """Relative path to content, for every generated file."""
    out = {"schema.json": _json(list(SCHEMA))}
    for case in CASES:
        meta = {
            "kind": case.kind,
            "layout": case.layout,
            "mutation": case.mutation,
            "recipe": case.recipe(),
            "correct_recipe": case.correct_recipe(),
        }
        out[f"{case.name}/before.html"] = render(case.before)
        out[f"{case.name}/after.html"] = render(case.after)
        out[f"{case.name}/expected.json"] = _json(expected(case.after))
        out[f"{case.name}/case.json"] = _json(meta)
    return out


def main() -> int:
    for relative, content in files().items():
        path = ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
