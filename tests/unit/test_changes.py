"""Changes between two readings of a dataset, and XLSX export."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from xml.etree import ElementTree

from rastro.adapters.xlsx_export import xlsx_export
from rastro.application.changes import changes_between, changes_since
from rastro.domain.dataset import DatasetRecordKey, StoredRecord
from rastro.domain.results import Evidence

URL = "https://a.example/items"
T1 = datetime(2030, 1, 1, tzinfo=UTC)
T2 = datetime(2030, 1, 2, tzinfo=UTC)


def record(name, observed=T1, first=None, **fields):
    fields = {"name": name, **fields}
    evidence = {
        k: Evidence(source_url=URL, observed_at=observed, method="css", locator=k, recipe_version=1)
        for k, v in fields.items()
    }
    base = dict(
        key=DatasetRecordKey(dataset="d", identity=(name,)),
        source_url=URL,
        observed_at=observed,
        fields=fields,
        provenance=evidence,
        first_observed_at=first or observed,
        last_observed_at=observed,
    )
    return StoredRecord(**base, origins={URL: StoredRecord(**base, origins={})})


def test_changes_price_changed():
    before = (record("a", price=10), record("b", price=5), record("gone", price=1))
    after = (
        record("a", T2, first=T1, price=12),
        record("b", T2, first=T1, price=5),
        record("new", T2, price=3),
    )
    result = changes_between(before, after)
    assert [c.identity for c in result.new] == [("new",)]
    assert [c.identity for c in result.removed] == [("gone",)]
    assert len(result.changed) == 1
    (change,) = result.changed
    assert change.identity == ("a",)
    assert [(f.field, f.before, f.after) for f in change.fields] == [("price", 10, 12)]


def test_changes_between_reports_added_and_removed_fields():
    result = changes_between((record("a", price=1),), (record("a", T2, color="red"),))
    (change,) = result.changed
    assert {(f.field, f.before, f.after) for f in change.fields} == {
        ("price", 1, None),
        ("color", None, "red"),
    }


def test_changes_since_reads_stored_timestamps():
    rows = (record("old"), record("fresh", T2), record("gone", T1))
    result = changes_since(rows, T1, latest_success={URL: T2})
    assert [c.identity for c in result.new] == [("fresh",)]
    assert sorted(c.identity for c in result.removed) == [("gone",), ("old",)]
    assert result.changed == ()


def _cells(data: bytes) -> dict[str, str]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        root = ElementTree.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    return {c.get("r"): "".join(c.itertext()) for c in root.iterfind(".//m:c", ns)}


def test_xlsx_opens_valid_zip_and_cells():
    data = xlsx_export((record("a", price=10, tags=["x", "y"]), record("=evil", price=2)))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml"} <= set(zf.namelist())
    cells = _cells(data)
    assert cells["A1"] == "name" and cells["B1"] == "price"
    assert cells["A2"] == "a" and cells["B2"] == "10"
    assert cells["C1"] == "tags[0]" and cells["C2"] == "x"
    assert cells["A3"] == "'=evil"  # same formula guard as CSV
    assert cells["E1"] == "last_observed_at"
