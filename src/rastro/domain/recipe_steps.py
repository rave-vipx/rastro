"""Page steps inside a recipe: which selectors they use and the CSS-only rule for them.

Steps run in a browser, where a selector string may name another engine (``xpath=``,
``text=``) or chain several (``>>``). Recipe steps stay CSS for every origin, so the same
selector cost policy covers them and no step can reach beyond the CSS vocabulary.
"""

from __future__ import annotations

import re

from rastro.domain.page_steps import ClickStep, FollowStep, PageStep

__all__ = ["STEP_ENGINE", "check_recipe_steps", "step_selectors"]

STEP_ENGINE = re.compile(r"^\s*[A-Za-z_][\w-]*\s*=")
"""A selector-engine prefix such as ``xpath=`` or ``css=``; refused in step selectors."""
_HREF = "::attr(href)"


def step_selectors(steps: tuple[PageStep, ...]) -> tuple[str, ...]:
    """Every CSS selector ``steps`` evaluate, with a follow step's ``::attr(href)`` removed."""
    found: list[str] = []
    for step in steps:
        selector = getattr(step, "selector", None)
        if isinstance(step, FollowStep):
            selector = step.selector.removesuffix(_HREF)
        if selector is not None:
            found.append(selector)
        if isinstance(step, ClickStep) and step.wait_for is not None:
            found.append(step.wait_for)
    return tuple(found)


def check_recipe_steps(steps: tuple[PageStep, ...]) -> None:
    """Raise ``ValueError`` when a step selector names an engine or chains selectors."""
    for selector in step_selectors(steps):
        if ">>" in selector or STEP_ENGINE.match(selector):
            raise ValueError("Step selectors are plain CSS: no engine prefix and no >>")
