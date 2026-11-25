"""Nested list and object fields read from HTML record nodes."""

from __future__ import annotations

from collections.abc import Callable

from scrapling.parser import Selector

from rastro.domain.models import Issue, Limits
from rastro.domain.recipes import DOCUMENT_PREFIX, EXPAND_PREFIX, NestedMapping

__all__ = ["extract_css_field"]


def extract_css_field(
    page: Selector,
    node: Selector,
    mapping: NestedMapping,
    matches: Callable[[Selector, str], tuple[int, list[str]]],
    *,
    field: str,
    limits: Limits,
    record_index: int,
) -> tuple[list[object] | None, tuple[Issue, ...]]:
    """Items under each ``mapping.each`` match of ``node``; None when nothing matched.

    ``matches`` returns a selector's element count and values within a scope.

    A member that is absent or matches several elements is None (the latter with an
    ``ambiguous_match`` issue). Lists longer than ``max_records`` are cut with
    ``extraction_limit``.
    """
    items = list(node.css(mapping.each))
    if not items:
        return None, ()
    issues: list[Issue] = []
    if len(items) > limits.max_records:
        items = items[: limits.max_records]
        issues.append(Issue(code="extraction_limit", record_index=record_index, field=field))

    def read(item: Selector, expression: str) -> str | None:
        expression = expression.removeprefix(EXPAND_PREFIX)
        scope, css = item, expression
        if expression.startswith(DOCUMENT_PREFIX):
            scope, css = page, expression.removeprefix(DOCUMENT_PREFIX)
        elements, values = matches(scope, css)
        if elements > 1 or len(values) > 1:
            issues.append(Issue(code="ambiguous_match", record_index=record_index, field=field))
            return None
        return values[0].strip() if values else None

    if mapping.value is not None:
        return [read(item, mapping.value) for item in items], tuple(issues)
    return [
        {name: read(item, expression) for name, expression in mapping.members.items()}
        for item in items
    ], tuple(issues)
