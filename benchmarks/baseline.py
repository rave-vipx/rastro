"""Independent Scrapling + Pydantic reference: explicit mappings and known fallbacks."""

from collections.abc import Iterator
from contextlib import suppress

from pydantic import ValidationError
from scrapling.parser import Selector

from benchmarks.contracts import Document, Listing, Mapping, Observation


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


def _json_rows(page: Selector, mapping: Mapping) -> list[dict[str, object]]:
    import json

    rows = []
    for block in page.css('script[type="application/ld+json"]::text').getall():
        for item in _objects(json.loads(str(block))):
            kinds = item.get("@type", [])
            if isinstance(kinds, str):
                kinds = [kinds]
            if not isinstance(kinds, list) or (
                mapping.item_type and mapping.item_type not in kinds
            ):
                continue
            row = {}
            for name, pointer in mapping.fields.items():
                with suppress(KeyError, IndexError, ValueError):
                    row[name] = _pointer(item, pointer)
            rows.append(row)
    return rows


def extract(document: Document) -> Observation:
    """No oracle, Rastro parser, application service, or model is accessible here."""
    result = Observation("missing_data", ())
    for mapping in document.mappings:
        page = Selector(document.html, adaptive=False, huge_tree=False)
        ambiguous = False
        if mapping.strategy == "jsonld":
            rows = _json_rows(page, mapping)
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
        records: list[Listing] = []
        invalid = False
        missing = False
        for row in rows:
            if not document.record_schema.model_fields.keys() <= row.keys():
                missing = True
                continue
            try:
                records.append(document.record_schema.model_validate(row))
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
        result = Observation(status, tuple(records))
        if records:
            break
    return result
