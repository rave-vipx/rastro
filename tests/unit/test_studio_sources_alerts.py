"""Studio Sources and Alerts views over /api/sources and /api/alerts (N5)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.studio_js import run_node

SAMPLE = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/design/studio-sample-data.json").read_text(
        encoding="utf-8"
    )
)
SOURCES = [
    {
        "url": s["url"],
        "status": s["status"],
        "issue_code": s.get("issue_code"),
        "last_fetch": s["last_fetch"],
        "read": s["read"],
        "rejected": s["rejected"],
        "cap": s["cap"],
        "warnings": ["sample_not_stored"] if s["status"] == "warning" else [],
    }
    for s in SAMPLE["sources"]
] + [
    {"url": "https://stale.example/", "status": "stale", "issue_code": None, "last_fetch": None,
     "read": 0, "rejected": 0, "cap": None, "warnings": []},
    {"url": "https://new.example/", "status": "never", "issue_code": None, "last_fetch": None,
     "read": 0, "rejected": 0, "cap": None, "warnings": []},
]  # fmt: skip
ALERTS = [
    {"at": "2026-11-21T22:02:41-03:00", "kind": "failure", "source": SOURCES[4]["url"],
     "code": "invalid_robots", "field": None, "action": "details"},
    {"at": "2026-11-21T22:02:30-03:00", "kind": "cap", "source": SOURCES[3]["url"],
     "code": "spider_page_limit", "field": None, "action": "raise_cap"},
    {"at": "2026-11-20T22:00:00-03:00", "kind": "drift", "source": SOURCES[1]["url"],
     "code": "missing_fields", "field": "lugar", "action": "fix"},
    {"at": "2026-11-20T21:03:00-03:00", "kind": "promote_refused", "source": None,
     "code": "promote_refused", "field": None, "action": "retry"},
]  # fmt: skip


def test_every_source_status_has_words_a_shape_and_a_next_step() -> None:
    """N5: each status reads as words (robots and caps included) with a shape and an action."""
    run_node(
        """
const dom = await import("STATIC/dom.js");
dom.state.file = "agenda.toml";
reply({"/api/sources": [200, {sources: SOURCES}]});
const { loadSources, sourceStatus, counts } = await import("STATIC/sources.js");
await loadSources();
const out = document.getElementById("sources-list");
const text = out.textContent;
assert.ok(text.includes("2 of 7 sources up to date"));
assert.ok(text.includes("The site returned something that can't be read"));
assert.ok(text.includes("Read 20 records; cap 20."));
assert.ok(text.includes("Last load failed") && text.includes("Not read yet"));
assert.equal(sourceStatus({status: "failed", issue_code: "robots_denied"}).word,
  "Access denied by the site (robots.txt)");
assert.match(classes(out), /ico-octagon/);
assert.match(classes(out), /ico-triangle/);
assert.match(classes(out), /ico-circle/);
const labels = buttons(out).map((b) => b.textContent);
for (const label of ["Retry", "Fix this site", "Raise the cap", "Run a load", "Details"]) {
  assert.ok(labels.includes(label), label);
}
assert.ok(!text.includes("undefined") && !text.includes("null"));
""",
        SOURCES=SOURCES,
    )


def test_missing_routes_show_an_error_state_never_an_empty_success() -> None:
    """N5: a 404 or a server error is said in words; no "no problems" message appears."""
    run_node(
        """
const dom = await import("STATIC/dom.js");
dom.state.file = "agenda.toml";
reply({"/api/alerts": [500, {ok: false, error: "internal", message: "boom"}]});
const { loadSources } = await import("STATIC/sources.js");
const { loadAlerts } = await import("STATIC/alerts.js");
await loadSources();
await loadAlerts();
const sources = document.getElementById("sources-list").textContent;
const alerts = document.getElementById("alerts-list").textContent;
assert.ok(sources.includes("Sources are not available") && sources.includes("/api/sources"));
assert.ok(alerts.includes("Alerts are not available") && alerts.includes("boom"));
assert.ok(!alerts.includes("No alerts") && !sources.includes("up to date"));
"""
    )


def test_alerts_are_grouped_by_day_with_one_sentence_and_one_action() -> None:
    """N5: plain sentence from kind + code in the chosen language, one action per alert."""
    run_node(
        """
const dom = await import("STATIC/dom.js");
dom.state.file = "agenda.toml";
reply({"/api/alerts": [200, {alerts: ALERTS}]});
const { loadAlerts, sentence } = await import("STATIC/alerts.js");
const { setLang } = await import("STATIC/i18n.js");
await loadAlerts();
const out = document.getElementById("alerts-list");
const days = walk(out).filter((n) => n.tagName === "H3");
assert.equal(days.length, 2);
const items = walk(out).filter((n) => n.tagName === "LI");
assert.equal(items.length, 4);
for (const item of items) assert.equal(buttons(item).length, 1);
assert.ok(out.textContent.includes("robots.txt"));
assert.ok(sentence(ALERTS[2]).includes("lugar"));
assert.ok(!sentence(ALERTS[3]).includes("promote_refused"));
setLang("es");
assert.ok(sentence(ALERTS[0]).startsWith("No se pudo leer cartelera-x.example"));
const raise = buttons(items[1])[0];
raise.click();
assert.equal(events.at(-1).type, "studio:settings");
""",
        ALERTS=ALERTS,
    )
