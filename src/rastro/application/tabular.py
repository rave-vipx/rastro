"""Records as a flat table, shared by the CSV and XLSX exports."""

from __future__ import annotations

from rastro.domain.dataset import StoredRecord

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def flatten(value: object, prefix: str) -> dict[str, object]:
    """Nested values as columns: ``tickets[0].price`` for a list item's member, ``a.b`` for an
    object member. Empty lists and objects become one empty column named for the field."""
    if isinstance(value, dict) and value:
        out: dict[str, object] = {}
        for name, item in value.items():
            out.update(flatten(item, f"{prefix}.{name}"))
        return out
    if isinstance(value, list) and value:
        out = {}
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{index}]"))
        return out
    return {prefix: None if value in ({}, []) else value}


def guarded_text(value: object) -> str:
    """Cell text; source data is untrusted, so a leading formula character is neutralised."""
    if value is None:
        return ""
    text = "true" if value is True else "false" if value is False else str(value)
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) and isinstance(value, str) else text


def records_table(records: tuple[StoredRecord, ...]) -> tuple[list[str], list[list[str]]]:
    """Header and rows of guarded text; the last column is ``last_observed_at``."""
    rows = [
        {
            column: cell_value
            for name, value in record.fields.items()
            for column, cell_value in flatten(value, name).items()
        }
        for record in records
    ]
    columns = list(dict.fromkeys(column for row in rows for column in row))
    body = [
        [
            *(guarded_text(row.get(column)) for column in columns),
            record.last_observed_at.isoformat(),
        ]
        for row, record in zip(rows, records, strict=True)
    ]
    return [*columns, "last_observed_at"], body
