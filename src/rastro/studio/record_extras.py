"""Per-record extras for the Results view (N5, PROV-001, N17): field provenance, why a
declared field is empty, instruction-like value flags and whether the record came from an
adapter (``rastro.load_records``) rather than a web extraction.

Values are never altered; flags only label them. Locators are capped like field values.
"""

from __future__ import annotations

from typing import Any

from rastro.domain.dataset import StoredRecord
from rastro.domain.injection import value_flags
from rastro.records_api import ADAPTER_LOCATOR

MAX_LOCATOR = 500


def _provenance(record: StoredRecord) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "source_url": evidence.source_url,
            "observed_at": evidence.observed_at.isoformat(),
            "method": evidence.method,
            "locator": evidence.locator[:MAX_LOCATOR],
            "recipe_version": evidence.recipe_version,
        }
        for name, evidence in record.provenance.items()
    }


def is_adapter(record: StoredRecord) -> bool:
    locators = [evidence.locator for evidence in record.provenance.values()]
    return bool(locators) and all(locator == ADAPTER_LOCATOR for locator in locators)


def extras(record: StoredRecord, declared: list[str], stale_urls: set[str]) -> dict[str, Any]:
    """Keys added to a ``/api/records`` row; existing keys are untouched."""
    failed = record.source_url in stale_urls or any(url in stale_urls for url in record.origins)
    reason = "source_failed" if failed else "not_published"
    return {
        "provenance": _provenance(record),
        "empty": {name: reason for name in declared if record.fields.get(name) is None},
        "flags": value_flags(record.fields),
        "origin": "adapter" if is_adapter(record) else "web",
    }
