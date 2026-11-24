"""What changed in a dataset: new, removed and changed records between two readings."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from pydantic import JsonValue

from rastro.domain.dataset import IdentityValue, StoredRecord
from rastro.domain.sources import Contract


class FieldChange(Contract):
    field: str
    before: JsonValue
    after: JsonValue


class RecordChange(Contract):
    identity: tuple[IdentityValue, ...]
    fields: tuple[FieldChange, ...] = ()


class Changes(Contract):
    new: tuple[RecordChange, ...] = ()
    removed: tuple[RecordChange, ...] = ()
    changed: tuple[RecordChange, ...] = ()


def _by_key(records: tuple[StoredRecord, ...]) -> dict[str, StoredRecord]:
    return {record.key.model_dump_json(): record for record in records}


def changes_between(before: tuple[StoredRecord, ...], after: tuple[StoredRecord, ...]) -> Changes:
    """Compare two readings of one dataset; a changed record lists each field's before and after."""
    old, new = _by_key(before), _by_key(after)
    changed = []
    for key, record in new.items():
        if (previous := old.get(key)) is None:
            continue
        names = list(dict.fromkeys([*previous.fields, *record.fields]))
        fields = tuple(
            FieldChange(field=n, before=previous.fields.get(n), after=record.fields.get(n))
            for n in names
            if previous.fields.get(n) != record.fields.get(n)
        )
        if fields:
            changed.append(RecordChange(identity=record.key.identity, fields=fields))
    return Changes(
        new=tuple(RecordChange(identity=r.key.identity) for k, r in new.items() if k not in old),
        removed=tuple(
            RecordChange(identity=r.key.identity) for k, r in old.items() if k not in new
        ),
        changed=tuple(changed),
    )


def changes_since(
    records: tuple[StoredRecord, ...],
    since: datetime,
    *,
    latest_success: Mapping[str, datetime],
    listed_since: datetime | None = None,
) -> Changes:
    """New and removed records from stored timestamps alone.

    New: first seen after ``since``. Removed: every source that listed it at or after
    ``listed_since`` (default ``since``) has had a newer successful run that did not. The store
    keeps no run history, so a record last listed before that time is never reported: it may
    have vanished long before. Changed fields need ``changes_between`` with an earlier reading.
    """
    floor = since if listed_since is None else listed_since
    new, removed = [], []
    for record in records:
        if record.first_observed_at > since:
            new.append(RecordChange(identity=record.key.identity))
        elif record.origins and all(
            (done := latest_success.get(url)) is not None
            and done > origin.last_observed_at >= floor
            for url, origin in record.origins.items()
        ):
            removed.append(RecordChange(identity=record.key.identity))
    return Changes(new=tuple(new), removed=tuple(removed))
