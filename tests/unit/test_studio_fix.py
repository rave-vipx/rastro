"""Studio "Fix a site": before/after, apply with a reason, discard, auto-apply off."""

from __future__ import annotations

from lxml import html

from tests.unit.studio_js import STATIC, run_node

COMPARISON = {
    "previous_version": 4,
    "candidate_version": 5,
    "diff": {
        "record_selector": [".event-card", "article[data-kind=event]"],
        "fields": [
            {"field": "titulo", "old": "h3::text", "new": "h3::text", "changed": False},
            {"field": "lugar", "old": None, "new": ".meta .venue::text", "changed": True},
        ],
    },
    "evaluation": {
        "decision": "quarantine",
        "reasons": ["auto_promote is off"],
        "current_records": 18,
        "historical_passed": 3,
        "historical_fixtures": 3,
        "regressions": [{"name": "historical_replay", "passed": True}],
    },
}
REPORT = {"sources": [{"outcome": "candidates", "issues": [], "comparisons": [COMPARISON]}]}
PROMOTED = {"sources": [{"promoted_version": 5, "comparisons": []}]}

SETUP = """
const dom = await import("STATIC/dom.js");
Object.assign(dom.state, { file: "agenda.toml", hasMemory: true, sha: "abc" });
const fix = await import("STATIC/fix.js");
fix.initFix();
fix.fillFixSources(2);
document.getElementById("fix-source").value = "2";
"""


def test_proposal_shows_before_after_and_checks() -> None:
    """Each field's current and proposed reading, what changed, and every check."""
    run_node(
        SETUP
        + """
reply({"/api/repair": [200, {ok: true, report: REPORT, alerts: []}]});
await document.getElementById("fix-find").listeners.click();
await new Promise((r) => setTimeout(r, 400));
const out = document.getElementById("fix-result");
const text = out.textContent;
assert.equal(calls.find((c) => c.path === "/api/repair").body.source, 2);
assert.ok(text.includes("version 5 replaces version 4"));
assert.ok(text.includes("Needs your review") && text.includes("18 records read now"));
assert.ok(text.includes("3 of 3 past samples pass") && text.includes("historical_replay: pass"));
assert.ok(text.includes(".meta .venue::text") && text.includes("Changed") && text.includes("Same"));
assert.ok(buttons(out).some((b) => b.textContent === "Apply fix"));
assert.ok(buttons(out).some((b) => b.textContent === "Discard"));
""",
        REPORT=REPORT,
    )


def test_apply_needs_a_reason_and_discard_writes_nothing() -> None:
    """No reason, no request; with a reason, /api/promote; discard sends nothing."""
    run_node(
        SETUP
        + """
reply({"/api/repair": [200, {ok: true, report: REPORT, alerts: []}],
       "/api/promote": [200, {ok: true, report: PROMOTED, alerts: []}]});
await document.getElementById("fix-find").listeners.click();
await new Promise((r) => setTimeout(r, 400));
const out = document.getElementById("fix-result");
const apply = buttons(out).find((b) => b.textContent === "Apply fix");
await apply.click();
assert.ok(!calls.some((c) => c.path === "/api/promote"));
assert.ok(document.getElementById("fix-status").textContent.includes("write a reason"));
const reason = walk(out).find((n) => n.tagName === "INPUT");
assert.equal(reason.maxLength, 500);
reason.value = "Venue moved to a new element";
await apply.click();
const sent = calls.find((c) => c.path === "/api/promote").body;
assert.deepEqual([sent.source, sent.version, sent.reason], [2, 5, "Venue moved to a new element"]);
assert.ok(!("override_gates" in sent));
assert.ok(document.getElementById("fix-status").textContent.includes("Version 5 now serves"));
calls.length = 0;
fix.resetFix();
await document.getElementById("fix-find").listeners.click();
await new Promise((r) => setTimeout(r, 400));
calls.length = 0;
const again = buttons(document.getElementById("fix-result"));
await again.find((b) => b.textContent === "Discard").click();
assert.equal(calls.length, 0);
assert.ok(document.getElementById("fix-status").textContent.includes("Nothing was applied"));
""",
        REPORT=REPORT,
        PROMOTED=PROMOTED,
    )


def test_missing_repair_route_is_an_error_not_a_result() -> None:
    """A failed request says so; no proposal and no "now serves" message appear."""
    run_node(
        SETUP
        + """
reply({});
await document.getElementById("fix-find").listeners.click();
await new Promise((r) => setTimeout(r, 400));
const status = document.getElementById("fix-status");
assert.ok(status.textContent.startsWith("Error: not_found"));
assert.equal(status.className, "bad");
assert.equal(document.getElementById("fix-result").children.length, 0);
"""
    )


def test_auto_apply_switch_starts_off_in_the_fix_view() -> None:
    """The automatic switch lives in Fix, is unchecked in the page and explains itself."""
    page = html.fromstring((STATIC / "index.html").read_text(encoding="utf-8"))
    switch = page.get_element_by_id("auto-promote")
    assert switch.get("checked") is None
    assert switch.xpath("ancestor::div[@id='view-repair']")
    assert page.get_element_by_id("auto-promote-status").get("role") == "status"
    stealth = page.get_element_by_id("allow-stealth")
    assert stealth.get("checked") is None
