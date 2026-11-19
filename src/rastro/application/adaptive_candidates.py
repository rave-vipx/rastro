"""Repair candidates from the engine's adaptive tools, tried before any model.

When the serving CSS recipe stops matching, the last known-good values say what to
look for: each field's retained values are searched on the new page, the repeated
group around a match becomes the record selector, and the path from the record to
the match becomes the field's selector. A field whose values are gone is looked up
next to its visible label (``price`` next to "Price:"); otherwise it keeps its
serving selector. Every recipe built here is only a proposal and meets the same
gates as any other candidate.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence

from pydantic import JsonValue, ValidationError

from rastro.domain.models import ConfigurationError, CssRecipe, Limits, Recipe, ResourceLimitError
from rastro.domain.recipe_compare import CandidateEvaluation
from rastro.domain.recipes import FieldMapping, scalar_expression
from rastro.domain.repair import RepairCandidate
from rastro.ports.engine import AdaptiveLocator

__all__ = ["PASSING_REASONS", "adaptive_recipes", "adaptive_repair", "passing", "with_history"]

PASSING_REASONS = frozenset({"all_gates_passed", "automatic_promotion_disabled"})

MAX_ANCHORS = 3  # retained values tried per field
MAX_GROUPS = 2  # record groups turned into recipes
_SUFFIX = re.compile(r"::(?:text|attr\([^)]+\))$")


def passing(evaluation: CandidateEvaluation) -> bool:
    """Would promote if promotion were enabled: no rejection and no quarantine gate failed."""
    return evaluation.decision != "reject" and set(evaluation.reasons) <= PASSING_REASONS


async def adaptive_repair(
    locator: AdaptiveLocator | None,
    html: str,
    previous: CssRecipe,
    history: Sequence[Mapping[str, JsonValue]],
    limits: Limits,
    normalize: Callable[[Recipe], RepairCandidate | None],
    gate: Callable[[RepairCandidate], CandidateEvaluation],
) -> list[tuple[RepairCandidate, CandidateEvaluation]]:
    """The first adaptive candidate, history union before plain, that passes every gate.

    ``locator`` defaults to the engine's. An unusable page or locator yields no
    candidates rather than failing repair: adaptive matching is one optional source.
    """
    if locator is None:
        from rastro.adapters.scrapling_adaptive import ScraplingAdaptive

        locator = ScraplingAdaptive()
    try:
        recipes = await asyncio.to_thread(
            adaptive_recipes, locator, html, previous=previous, history=history, limits=limits
        )
    except (ConfigurationError, ResourceLimitError):
        return []
    for recipe in recipes:
        try:
            plain = normalize(recipe)
            union = None if plain is None else normalize(with_history(plain.recipe, previous))
        except ConfigurationError:
            continue
        for candidate in (c for c in (union, plain) if c is not None):
            try:
                evaluation = await asyncio.to_thread(gate, candidate)
            except ConfigurationError:
                continue
            if passing(evaluation):
                return [(candidate, evaluation)]
    return []


def adaptive_recipes(
    locator: AdaptiveLocator,
    html: str,
    *,
    previous: CssRecipe,
    history: Sequence[Mapping[str, JsonValue]],
    limits: Limits,
) -> tuple[CssRecipe, ...]:
    """Recipes for the record groups that hold the most fields with known values."""
    groups: dict[str, dict[str, str]] = {}
    counts: dict[str, int] = {}
    for name, mapping in previous.fields.items():
        if _suffix(mapping) != "::text":
            continue  # attribute values are not visible text to search for
        for anchor in _anchors(history, name):
            for group in locator.similar_records(html, anchor, limits=limits):
                path = group.field_locators.get(anchor)
                if path is not None:
                    groups.setdefault(group.record_selector, {}).setdefault(name, path)
                    counts[group.record_selector] = group.count
    ranked = sorted(groups, key=lambda s: (-len(groups[s]), -counts[s], s))[:MAX_GROUPS]
    recipes = (
        _recipe(locator, html, previous, selector, groups[selector], limits) for selector in ranked
    )
    return tuple(r for r in recipes if r is not None)


def _recipe(
    locator: AdaptiveLocator,
    html: str,
    previous: CssRecipe,
    selector: str,
    located: Mapping[str, str],
    limits: Limits,
) -> CssRecipe | None:
    fields: dict[str, FieldMapping] = {}
    for name, mapping in previous.fields.items():
        suffix = _suffix(mapping)
        path = located.get(name)
        if path is None and suffix == "::text":
            label = name.replace("_", " ")
            path = locator.anchor_by_label(html, selector, label, limits=limits)
        fields[name] = mapping if path is None or suffix is None else path + suffix
    try:
        recipe = Recipe.model_validate(
            {**previous.model_dump(), "record_selector": selector, "fields": fields}
        )
    except ValidationError:
        return None
    return recipe if isinstance(recipe, CssRecipe) else None


def _suffix(mapping: FieldMapping) -> str | None:
    if not isinstance(mapping, str):
        return None
    match = _SUFFIX.search(mapping)
    return match[0] if match else None


def _anchors(history: Sequence[Mapping[str, JsonValue]], name: str) -> list[str]:
    """Up to ``MAX_ANCHORS`` distinct visible forms of the field's retained values."""
    found: list[str] = []
    for row in history:
        value = row.get(name)
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            continue
        text = str(value).strip()
        if text and text not in found:
            found.append(text)
        if len(found) == MAX_ANCHORS:
            break
    return found


def with_history(proposal: Recipe, previous: Recipe) -> Recipe:
    """Prefix each changed CSS locator with the serving one, as relocation does.

    ``"old, new"`` keeps historical pages matching, so fixture replay can pass; any
    union that does not validate falls back to the plain proposal.
    """
    if not (isinstance(proposal, CssRecipe) and isinstance(previous, CssRecipe)):
        return proposal
    fields = {
        name: _union(_scalar(previous.fields.get(name)), scalar_expression(sel))
        for name, sel in proposal.fields.items()
    }
    record = _union(previous.record_selector, proposal.record_selector)
    try:
        return Recipe.model_validate(
            {**proposal.model_dump(), "fields": fields, "record_selector": record}
        )
    except ValidationError:
        return proposal


def _scalar(mapping: FieldMapping | None) -> str | None:
    return mapping if isinstance(mapping, str) else None


def _union(old: str | None, new: str) -> str:
    if old is None or old == new or new.startswith(old + ","):
        return new
    return f"{old}, {new}"
