"""Independent Scrapling + Pydantic reference: explicit mappings and known fallbacks."""

import json
from collections.abc import Iterator, Sequence
from contextlib import suppress

from pydantic import BaseModel, ValidationError
from scrapling.parser import Selector

from benchmarks.contracts import Document, Mapping, Observation


def _objects(value: object) -> Iterator[dict[str, object]]:
    if isinstance(value, list):
        for item in value:
            yield from _objects(item)
    elif isinstance(value, dict):
        if "@graph" in value:
            yield from _objects(value["@graph"])
        else:
            yield value


def _pointer(item: dict[str, object], pointer: str) -> object:
    value: object = item
    for part in pointer.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value[key]
        elif isinstance(value, list):
            value = value[int(key)]
        else:
            raise KeyError(key)
    return value


def _types(item: dict[str, object]) -> list[object]:
    kinds = item.get("@type", [])
    return [kinds] if isinstance(kinds, str) else kinds if isinstance(kinds, list) else []


def _json_items(page: Selector) -> tuple[list[dict[str, object]], bool]:
    """Return JSON-LD objects and whether any block was malformed (reported, not raised)."""
    items: list[dict[str, object]] = []
    malformed = False
    for block in page.css('script[type="application/ld+json"]::text').getall():
        try:
            items.extend(_objects(json.loads(str(block))))
        except ValueError:
            malformed = True
    return items, malformed


def _json_rows(page: Selector, mapping: Mapping) -> tuple[list[dict[str, object]], bool]:
    rows = []
    items, malformed = _json_items(page)
    for item in items:
        if not isinstance(item.get("@type", []), str | list) or (
            mapping.item_type and mapping.item_type not in _types(item)
        ):
            continue
        row = {}
        for name, pointer in mapping.fields.items():
            with suppress(KeyError, IndexError, ValueError):
                row[name] = _pointer(item, pointer)
        rows.append(row)
    return rows, malformed


def run[T: BaseModel](
    html: str, mappings: Sequence[Mapping], schema: type[T]
) -> tuple[str, tuple[T, ...]]:
    """No oracle, Rastro parser, application service, or model is accessible here."""
    result: tuple[str, tuple[T, ...]] = ("missing_data", ())
    for mapping in mappings:
        page = Selector(html, adaptive=False, huge_tree=False)
        ambiguous = False
        malformed = False
        if mapping.strategy == "jsonld":
            rows, malformed = _json_rows(page, mapping)
        else:
            rows = []
            for node in page.css(mapping.record_selector or "html"):
                row: dict[str, object] = {}
                for field, selector in mapping.fields.items():
                    matches = node.css(selector).getall()
                    if len(matches) > 1:
                        ambiguous = True
                        break
                    if matches:
                        row[field] = str(matches[0]).strip()
                else:
                    rows.append(row)
        records: list[T] = []
        invalid = malformed
        missing = False
        for row in rows:
            if not schema.model_fields.keys() <= row.keys():
                missing = True
                continue
            try:
                records.append(schema.model_validate(row))
            except ValidationError:
                invalid = True
        warning = ambiguous or invalid or missing
        status = (
            ("partial_success" if warning else "success")
            if records
            else "insufficient_evidence"
            if ambiguous or missing
            else "invalid_data"
            if invalid
            else "missing_data"
        )
        result = (status, tuple(records))
        if records:
            break
    return result


def extract(document: Document) -> Observation:
    status, records = run(document.html, document.mappings, document.record_schema)
    return Observation(status, records)


def structured_mappings(html: str, fields: Sequence[str]) -> tuple[Mapping, ...]:
    """One-time custom adaptation: map schema.org JSON-LD/microdata to field names.

    Field names are looked up at the top level, then inside ``offers`` (an object or
    a one-element list), the most common schema.org placement for prices.
    """
    page = Selector(html, adaptive=False, huge_tree=False)
    mappings: list[Mapping] = []
    for item in _json_items(page)[0]:
        offers = item.get("offers")
        if isinstance(offers, list) and len(offers) == 1:
            offers, prefix = offers[0], "/offers/0/"
        else:
            prefix = "/offers/"
        pointers = {}
        for name in fields:
            if name in item:
                pointers[name] = f"/{name}"
            elif isinstance(offers, dict) and name in offers:
                pointers[name] = prefix + name
        kinds = [kind for kind in _types(item) if isinstance(kind, str)]
        if len(pointers) == len(fields):
            mapping = Mapping(
                strategy="jsonld", fields=pointers, item_type=kinds[0] if kinds else None
            )
            if mapping not in mappings:
                mappings.append(mapping)
    root = "[itemscope]:not([itemprop])"
    for node in page.css(root)[:1]:
        selectors = {}
        for name in fields:
            match = node.css(f'[itemprop="{name}"]')
            if match:
                suffix = "::attr(content)" if "content" in match[0].attrib else "::text"
                selectors[name] = f'[itemprop="{name}"]{suffix}'
        if len(selectors) == len(fields):
            mappings.append(Mapping(strategy="css", record_selector=root, fields=selectors))
    return tuple(mappings)
