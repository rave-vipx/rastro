"""Cost policy for CSS selectors in recipes from untrusted sources (security audit H3).

Recipes from Hub bundles, MCP tool arguments, Studio edits and model or repair proposals
are evaluated on documents of up to ``Limits.max_html_bytes``. lxml runs sibling
combinators (``a ~ b``, ``a + b``) through ``following-sibling::``, which is quadratic in
the number of siblings, and chaining them multiplies the cost: ``p ~ p ~ p ~ b`` took
105 s on a 48 KB page. ``:has()`` has the same shape. The evaluation cannot be interrupted
once it runs, so such selectors are refused before evaluation. Selectors an operator
writes in a job file are trusted and not checked here.
"""

from __future__ import annotations

import re

from rastro.domain.recipes import DOCUMENT_PREFIX, EXPAND_PREFIX, CssRecipe, Recipe
from rastro.domain.sources import ConfigurationError

MAX_ALTERNATIVES = 8  # comma-separated selectors in one list
MAX_COMPOUNDS = 8  # compound selectors chained by descendant or child combinators
MAX_NESTING = 2  # parenthesized pseudo-class depth, as in :not(:is(.a))
_PSEUDO_ELEMENT = re.compile(r"::(?:text|attr\([^)]*\))$")


def _refuse(rule: str) -> ConfigurationError:
    return ConfigurationError(
        f"Selector refused by the untrusted-recipe policy: {rule}",
        hint="Recipes from bundles, agents, Studio or models cannot use :has(), sibling "
        f"combinators (~, +), over {MAX_COMPOUNDS} chained compounds, over "
        f"{MAX_ALTERNATIVES} alternatives or pseudo-class nesting over {MAX_NESTING}",
    )


def _sibling_plus(selector: str, index: int, depth: int) -> bool:
    """Inside parentheses ``+`` is an An+B sign (``2n+1``) unless a selector follows it."""
    if depth == 0:
        return True
    rest = selector[index + 1 :].lstrip()
    return not rest[:1].isdigit()


def _skeleton(selector: str) -> str:
    """The top level of ``selector`` with quoted, bracketed and parenthesized text removed,
    after refusing sibling combinators and deep nesting anywhere in it."""
    kept: list[str] = []
    depth = 0
    quote: str | None = None
    bracket = False
    for index, character in enumerate(selector):
        if quote is not None:
            quote = None if character == quote else quote
        elif character in "'\"":
            quote = character
        elif bracket:
            bracket = character != "]"
        elif character == "[":
            bracket = True
        elif character == "(":
            depth += 1
            if depth > MAX_NESTING:
                raise _refuse("pseudo-class nesting is too deep")
        elif character == ")":
            depth -= 1
        elif character == "~" or (character == "+" and _sibling_plus(selector, index, depth)):
            raise _refuse("sibling combinators (~, +) are not allowed")
        elif depth == 0:
            kept.append(character)
    return "".join(kept)


def check_untrusted_selector(selector: str) -> None:
    """Raise ``ConfigurationError`` naming the rule a costly selector breaks."""
    if ":has(" in selector.lower():
        raise _refuse(":has() is not allowed")
    alternatives = _skeleton(selector).split(",")
    if len(alternatives) > MAX_ALTERNATIVES:
        raise _refuse("too many alternatives")
    for alternative in alternatives:
        if len(re.split(r"\s*>\s*|\s+", alternative.strip())) > MAX_COMPOUNDS:
            raise _refuse("too many compound selectors")


def check_untrusted_field(expression: str) -> None:
    """Check a CSS field mapping such as ``document:h1::text`` or ``a::attr(href)``."""
    expression = expression.removeprefix(EXPAND_PREFIX).removeprefix(DOCUMENT_PREFIX)
    selector = _PSEUDO_ELEMENT.sub("", expression).strip()
    if selector:
        check_untrusted_selector(selector)


def check_untrusted_recipe[R: Recipe](recipe: R) -> R:
    """Check every CSS selector of ``recipe`` and its fallbacks; other strategies pass."""
    for candidate in (recipe, *recipe.fallbacks):
        if not isinstance(candidate, CssRecipe):
            continue
        check_untrusted_selector(candidate.record_selector)
        for expression in candidate.fields.values():
            check_untrusted_field(expression)
    return recipe
