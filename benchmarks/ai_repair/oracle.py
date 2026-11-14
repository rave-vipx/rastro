"""Scripted ModelPort fakes for the offline run (QUAL-001, AI-001). NOT a quality claim.

The oracle sees only the requests Rastro sends. A recipe request carries the page: the
oracle finds it by digest in a table built from the case definitions and answers with a
recipe for that case. A semantic-judge request carries field names and clipped values:
the oracle answers per field. It measures what Rastro's gates do with right or wrong
answers, never how good any real model is.

* ``correct``: the right recipe for the new page (Rastro itself joins it with the
  serving selectors), and a truthful judge: ``match`` when every candidate value is a
  true value of that field, else ``mismatch``.
* ``wrong_recipe``: the right recipe with title and performer swapped, and the truthful
  judge (a model that writes a bad recipe but judges honestly).
* ``adversarial``: the swapped recipe, and a judge that always answers ``match`` with
  full confidence, trying to wave wrong values through.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, Literal

from benchmarks.ai_repair.cases import Case
from benchmarks.ai_repair.layouts import RECORDS, render
from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest

Mode = Literal["correct", "wrong_recipe", "adversarial"]
TRUTH = {name: {str(r[name]) for r in RECORDS} for name in RECORDS[0]}


def _digest(html: str) -> str:
    return hashlib.sha256(html.encode()).hexdigest()


def swapped(recipe: dict[str, Any]) -> dict[str, Any]:
    """A plausible wrong proposal: every value is real, but two meanings are exchanged."""
    fields = dict(recipe["fields"])
    fields["title"], fields["performer"] = fields["performer"], fields["title"]
    return {**recipe, "fields": fields}


def _verdict(field: dict[str, Any], mode: Mode) -> dict[str, Any]:
    if mode == "adversarial":
        return {"verdict": "match", "confidence": 1.0}
    values = field.get("untrusted_candidate_values") or []
    if not values:
        return {"verdict": "unsure", "confidence": 0.5}
    true = all(value in TRUTH.get(field["name"], ()) for value in values)
    return {"verdict": "match" if true else "mismatch", "confidence": 1.0}


class ScriptedOracle:
    """A fake model that answers from the prompt alone; counts its calls."""

    def __init__(self, cases: Iterable[Case], mode: Mode = "correct") -> None:
        self._mode = mode
        self._answers = {
            _digest(render(c.after)): swapped(c.correct_recipe())
            if mode != "correct"
            else c.correct_recipe()
            for c in cases
        }
        self.calls = 0

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        self.calls += 1
        content = json.loads(request.content)
        if "untrusted_html" not in content:
            fields = content.get("fields")
            if isinstance(fields, list) and all(isinstance(f, dict) for f in fields):
                return json.dumps({f["name"]: _verdict(f, self._mode) for f in fields})
            raise ModelError("invalid_model_response")  # e.g. a digest-only prompt
        answer = self._answers.get(_digest(content["untrusted_html"]))
        if answer is None:  # a page the script does not know: decline, as a model might
            raise ModelError("invalid_model_response")
        return json.dumps({"strategy": "css", **answer})
