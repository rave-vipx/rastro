"""Studio results: what changed, why a source is partial, and the spreadsheet view's data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rastro.domain.dataset import DatasetRecordKey, StoredRecord
from rastro.domain.models import Evidence
from rastro.domain.run_report import RunReport, RunTotals, SourceReport
from rastro.studio.record_extras import compare, why_partial
from rastro.studio.run_log import LoggedIssue, LoggedRun, LoggedSource
from tests.unit.studio_js import run_node

T0 = datetime(2026, 11, 1, tzinfo=UTC)
URL = "https://shop.example/list"


def _source(index: int, outcome: str, reason: str, **extra: object) -> SourceReport:
    return SourceReport(
        source_index=index,
        host=f"s{index}.example",
        path="/list",
        outcome=outcome,  # type: ignore[arg-type]
        reason=reason,  # type: ignore[arg-type]
        **extra,  # type: ignore[arg-type]
    )


def _run(at: datetime, sources: tuple[SourceReport, ...], logged: tuple[LoggedSource, ...] = ()):
    totals = dict.fromkeys(RunTotals.model_fields, 0) | {"sources": len(sources)}
    report = RunReport(
        kind="load",
        job="a.toml",
        started_at=at,
        completed_at=at,
        status="partial_success",
        exit_code=3,
        sources=sources,
        totals=RunTotals(**totals),
    )
    return LoggedRun(at=at, sources=logged, report=report)


def _record(name: str, seen: datetime) -> StoredRecord:
    evidence = Evidence(
        source_url=URL, observed_at=seen, method="css", locator="x", recipe_version=1
    )
    key = DatasetRecordKey(dataset="d", identity=(name,))
    return StoredRecord(
        key=key,
        source_url=URL,
        observed_at=seen,
        fields={"title": name},
        provenance={"title": evidence},
        first_observed_at=seen,
        last_observed_at=seen,
        origins={},
    )


def test_partial_sources_get_one_plain_reason_each() -> None:
    """N3: a cap, a failed page and a block are told apart; healthy sources say nothing."""
    run = _run(
        T0,
        (
            _source(1, "success", "page_cap", pages=5),
            _source(2, "success", "complete"),
            _source(3, "access_restriction", "blocked"),
            _source(4, "partial_success", "complete"),
            _source(5, "transient_failure", "failed"),
        ),
    )
    assert [(w["host"], w["why"]) for w in why_partial(run)] == [
        ("s1.example", "page_cap"),
        ("s3.example", "blocked"),
        ("s4.example", "page_failed"),
        ("s5.example", "page_failed"),
    ]


def test_a_logged_page_issue_marks_a_complete_source_partial() -> None:
    run = _run(
        T0,
        (_source(1, "success", "complete"),),
        (LoggedSource(url=URL, issues=(LoggedIssue(code="spider_page_failed"),)),),
    )
    assert [w["why"] for w in why_partial(run)] == ["page_failed"]
    assert why_partial(None) == []


def test_first_run_has_nothing_to_compare() -> None:
    """The first load shows an empty state, not a list of everything as new."""
    run = _run(T0, ())
    summary, marks = compare((_record("a", T0),), {}, (run,))
    assert summary["first_run"] is True and marks == {}


def test_second_run_marks_new_and_removed() -> None:
    first, second = T0, T0 + timedelta(days=1)
    old, kept = _record("old", first), _record("kept", first)
    fresh = _record("fresh", second)
    old = old.model_copy(update={"origins": {URL: _origin(old, first)}})
    kept = kept.model_copy(update={"origins": {URL: _origin(kept, second)}})
    fresh = fresh.model_copy(update={"origins": {URL: _origin(fresh, second)}})
    runs = (_run(first, ()), _run(second, ()))
    summary, marks = compare((old, kept, fresh), {URL: second}, runs)
    assert summary["first_run"] is False and summary["new"] == 1 and summary["removed"] == 1
    assert sorted(marks.values()) == ["new", "removed"]


def _origin(record: StoredRecord, seen: datetime):  # type: ignore[no-untyped-def]
    from rastro.domain.dataset import RecordOrigin

    return RecordOrigin(
        key=record.key,
        source_url=URL,
        observed_at=seen,
        fields=record.fields,
        provenance=record.provenance,
        first_observed_at=record.first_observed_at,
        last_observed_at=seen,
    )


def test_nested_members_expand_and_filter() -> None:
    """N5: a list of objects becomes sub-rows; a member filter keeps matching ones only."""
    run_node("""
const { nestedModel, filterMembers } = await import("STATIC/cells.js");
const tickets = [{tier: "General", price: "10"}, {tier: "VIP", price: "40"}];
assert.deepEqual(nestedModel("x"), null);
assert.deepEqual(nestedModel(["a", "b"]), null);
const model = nestedModel(tickets);
assert.deepEqual(model.members, ["tier", "price"]);
assert.equal(model.rows.length, 2);
assert.deepEqual(filterMembers(model.rows, "tier", "vip").map((r) => r.price), ["40"]);
assert.equal(filterMembers(model.rows, "", "zzz").length, 0);
assert.equal(filterMembers(model.rows, "tier", "").length, 2);
""")


def test_change_chips_model() -> None:
    """N5: chips are new / changed / removed with counts; the first run is an empty state."""
    run_node("""
const { chipsModel, partialLines } = await import("STATIC/changes.js");
assert.deepEqual(chipsModel({first_run: true}), {state: "first", chips: []});
const m = chipsModel({first_run: false, new: 2, removed: 1, changed: 0});
assert.equal(m.state, "ready");
const pairs = m.chips.map((c) => [c.kind, c.count]);
assert.deepEqual(pairs, [["new", 2], ["changed", 0], ["removed", 1]]);
assert.equal(chipsModel({first_run: false, new: 0, removed: 0, changed: 0}).state, "quiet");
const lines = partialLines([{host: "a.example", path: "/x", why: "page_cap"}]);
assert.deepEqual(lines, [{host: "a.example", why: "page_cap"}]);
""")
