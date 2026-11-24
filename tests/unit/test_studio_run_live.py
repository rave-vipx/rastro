"""Studio's running-read view: the data behind it and its DOM-free mapping."""

from __future__ import annotations

from types import SimpleNamespace

from rastro.studio.run_live import LiveRun
from tests.unit.studio_js import run_node


def _result(stop: str | None, records: int, status: str = "success") -> SimpleNamespace:
    pages = [SimpleNamespace(outcome="fetched"), SimpleNamespace(outcome="budget")]
    rows = [SimpleNamespace(data=SimpleNamespace(model_dump=lambda mode: {"t": "x" * 300}))]
    return SimpleNamespace(
        spider=SimpleNamespace(pages=pages),
        records=rows * records,
        status=status,
        pagination_stop=stop,
    )


def test_a_time_budget_stop_is_reported_as_continuing_and_samples_are_bounded() -> None:
    now = [0.0]
    live = LiveRun("a.toml", 2, 60, lambda: now[0])
    live.add(_result("spider_complete", 8))
    assert live.snapshot()["will_continue"] is False
    live.add(_result("spider_time_budget", 3, status="permanent_failure"))
    now[0] = 61.0
    live.finish("done")
    snap = live.snapshot()
    assert snap["will_continue"] is True and snap["pages"] == 2 and snap["records"] == 11
    assert snap["failed_sources"] == 1 and snap["seconds_used"] == 61.0
    assert len(snap["sample"]) == 5 and all(
        len(v) <= 120 for r in snap["sample"] for v in r.values()
    )


def test_run_view_maps_each_state_to_words_a_bar_and_a_resume_note() -> None:
    run_node("""
const { runView, clock } = await import("STATIC/sources.js");
assert.equal(clock(65), "1m 05s");
assert.equal(clock(9), "9s");
assert.equal(runView(null).idle, true);
const base = { state: "running", pages: 4, records: 12, sources_done: 1, sources_total: 3,
  seconds_used: 30, budget_seconds: 60, sample: [{ a: "x" }], failed_sources: 0,
  will_continue: false };
const going = runView(base);
assert.equal(going.running, true);
assert.equal(going.fraction, 0.5);
assert.ok(going.lines[0].includes("4 pages read") && going.lines[0].includes("12 results"));
assert.ok(going.lines.some((l) => l.includes("30s of 1m 00s used")));
const cut = runView({ ...base, state: "done", will_continue: true });
assert.equal(cut.running, false);
assert.ok(cut.resume.includes("It will continue from here on the next run."));
const bad = runView({ ...base, state: "failed", error: { message: "boom" } });
assert.ok(bad.error.includes("boom") && bad.tone === "fail");
""")
