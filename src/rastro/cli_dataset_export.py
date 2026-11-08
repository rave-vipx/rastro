"""Presentation and selection of dataset records (DATA-004, SCHED-007)."""

from datetime import UTC, datetime
from html import escape
from urllib.parse import urlsplit

from rastro.domain.dataset import DatasetRecordKey, StoredRecord
from rastro.domain.models import ConfigurationError


def select_records(
    records: tuple[StoredRecord, ...], field: str | None, upcoming: bool
) -> tuple[StoredRecord, ...]:
    if upcoming and not field:
        raise ConfigurationError("--upcoming requires --sort-field")
    if not field:
        return records
    if records and not any(field in record.fields for record in records):
        raise ConfigurationError("Sort field is not present in the dataset")
    if upcoming:
        now = datetime.now(UTC)
        selected = []
        for record in records:
            value = record.fields.get(field)
            if value is None:
                continue
            try:
                date = datetime.fromisoformat(str(value))
            except ValueError:
                raise ConfigurationError(
                    "--upcoming requires ISO date or datetime values"
                ) from None
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            if date.date() >= now.date() if len(str(value)) == 10 else date >= now:
                selected.append(record)
        records = tuple(selected)
    return tuple(sorted(records, key=lambda r: sort_key(r.fields.get(field))))


def sort_key(value: object) -> tuple[bool, int, float | str]:
    if value is None:
        return True, 0, ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return False, 0, float(value)
    if isinstance(value, str):
        try:
            date = datetime.fromisoformat(value)
            return False, 1, date.replace(tzinfo=date.tzinfo or UTC).timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    return False, 2, str(value)


def cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    escaped = escape(text)
    try:
        url = urlsplit(text)
        if (
            url.scheme.lower() in ("http", "https")
            and url.netloc
            and text.startswith(("http://", "https://", "HTTP://", "HTTPS://"))
        ):
            return f'<a href="{escaped}">{escaped}</a>'
    except ValueError:
        pass
    return escaped


def html_export(
    records: tuple[StoredRecord, ...], stale: set[DatasetRecordKey], fields: list[str]
) -> str:
    headers = "".join(f"<th>{escape(name)}</th>" for name in [*fields, "last_observed_at", "stale"])
    rows = []
    for record in records:
        values = [record.fields.get(name) for name in fields]
        values.extend([record.last_observed_at.isoformat(), "stale" if record.key in stale else ""])
        rows.append("<tr>" + "".join(f"<td>{cell(value)}</td>" for value in values) + "</tr>")
    if not rows:
        rows.append(f'<tr><td colspan="{len(fields) + 2}">No records</td></tr>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Dataset</title>'
        "</head><body><p>Untrusted source data. Generated at "
        f"{escape(datetime.now(UTC).isoformat())}</p><table><thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></body></html>"
    )
