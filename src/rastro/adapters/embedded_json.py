"""JSON embedded in an HTML attribute or script, read through one CSS selector (N16)."""

from __future__ import annotations

from json import JSONDecodeError

from scrapling.parser import Selector

from rastro.adapters.nested_json import extract_json_field
from rastro.adapters.structured import load_json
from rastro.domain.json import MISSING, escape_token, resolve_pointer
from rastro.domain.models import EmbeddedJsonRecipe, Issue, Limits, ResourceLimitError
from rastro.domain.recipes import NestedMapping, scalar_expression
from rastro.domain.tolerant_json import repaired_text
from rastro.ports.parser import Candidate, ParsedDocument


def parse_embedded_json(
    page: Selector, recipe: EmbeddedJsonRecipe, limits: Limits, element_count: int
) -> ParsedDocument:
    """Parse the single node ``recipe.source`` selects; zero or several nodes are issues."""
    values = [str(value) for value in page.css(recipe.source).getall()]
    if element_count > 1 or len(values) > 1:
        return ParsedDocument((), (Issue(code="ambiguous_match"),))
    if not values:
        return ParsedDocument((), (Issue(code="embedded_json_not_found"),))
    notes: tuple[Issue, ...] = ()
    try:
        try:
            payload = load_json(values[0], limits, repair_text=True)
        except JSONDecodeError:
            payload = load_json(repaired_text(values[0]), limits, repair_text=True)
            notes = (Issue(code="repaired_json"),)
    except ValueError:
        return ParsedDocument((), (Issue(code="invalid_json"),))
    target = resolve_pointer(payload, recipe.record_path)
    if target is MISSING:
        return ParsedDocument((), (Issue(code="record_path_not_found"),))
    if isinstance(target, dict):
        records: list[tuple[object, str]] = [(target, recipe.record_path)]
    elif isinstance(target, list):
        records = [(item, f"{recipe.record_path}/{i}") for i, item in enumerate(target)]
    else:
        return ParsedDocument((), (Issue(code="invalid_record_path"),))
    if len(records) > limits.max_records:
        raise ResourceLimitError("Record count exceeds max_records")
    candidates: list[Candidate] = []
    for index, (item, path) in enumerate(records):
        if not isinstance(item, dict):
            candidates.append(
                Candidate({}, {}, (Issue(code="invalid_record", record_index=index),))
            )
            continue
        found: dict[str, object] = {}
        locators: dict[str, str] = {}
        item_issues: list[Issue] = []
        for field, pointer in recipe.fields.items():
            if isinstance(pointer, NestedMapping):
                value, extra = extract_json_field(
                    item, pointer, field=field, limits=limits, record_index=index
                )
                item_issues.extend(extra)
                pointer = pointer.each
            else:
                value = resolve_pointer(item, scalar_expression(pointer))
            if value is not MISSING:
                found[field] = value
                locators[field] = f"embedded_json[{escape_token(recipe.source)}]#{path}{pointer}"
        candidates.append(Candidate(found, locators, tuple(item_issues)))
    return ParsedDocument(tuple(candidates), notes)
