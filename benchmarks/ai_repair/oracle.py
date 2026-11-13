"""Scripted ModelPort fakes for the offline run (QUAL-001, AI-001). NOT a quality claim.

The oracle sees only the request Rastro sends. It finds the page in the untrusted
envelope, looks its digest up in a table built from the case definitions and answers
with a recipe for that case. It measures what Rastro's gates do with a right or a
wrong proposal, never how good any real model is.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Any, Literal

from benchmarks.ai_repair.cases import Case
from benchmarks.ai_repair.layouts import render
from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest


def _digest(html: str) -> str:
    return hashlib.sha256(html.encode()).hexdigest()


def swapped(recipe: dict[str, Any]) -> dict[str, Any]:
    """A plausible wrong proposal: every value is real, but two meanings are exchanged."""
    fields = dict(recipe["fields"])
    fields["title"], fields["performer"] = fields["performer"], fields["title"]
    return {**recipe, "fields": fields}


def compatible(case: Case) -> dict[str, Any]:
    """The correct recipe joined with the old one, so it parses old and new pages alike."""
    old, new = case.recipe(), case.correct_recipe()

    def join(a: str, b: str) -> str:
        return a if a == b else f"{a}, {b}"

    return {
        "record_selector": join(old["record_selector"], new["record_selector"]),
        "fields": {k: join(old["fields"][k], v) for k, v in new["fields"].items()},
    }


Mode = Literal["correct", "compatible", "adversarial"]
MODES: dict[Mode, Callable[[Case], dict[str, Any]]] = {
    "correct": Case.correct_recipe,
    "compatible": compatible,
    "adversarial": lambda case: swapped(compatible(case)),
}


class ScriptedOracle:
    """A fake model that answers from the prompt alone; counts its calls.

    ``correct`` answers what a perfect reader of the new page alone could propose;
    ``compatible`` also keeps the old selectors (it knows the old recipe, which this
    base's repair prompt does not send); ``adversarial`` swaps two meanings.
    """

    def __init__(self, cases: Iterable[Case], mode: Mode = "correct") -> None:
        self._answers = {_digest(render(case.after)): MODES[mode](case) for case in cases}
        self.calls = 0

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        self.calls += 1
        html = json.loads(request.content)["untrusted_html"]
        answer = self._answers.get(_digest(html))
        if answer is None:  # a page the script does not know: decline, as a model might
            raise ModelError("invalid_model_response")
        return json.dumps({"strategy": "css", **answer})
