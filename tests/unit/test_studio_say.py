"""The per-site traffic light and one-sentence hint, read from a stored run report."""

from typing import Any

from rastro.studio.overview_say import hint, light
from tests.unit.test_run_report_model import report, source

URL = "https://shop.example/catalog"


def say(**changes: Any) -> dict[str, Any] | None:
    return hint(report(sources=(source(**changes),)), URL)


def test_nothing_to_say_for_a_clean_site_or_no_report() -> None:
    assert say() is None
    assert hint(None, URL) is None
    assert hint(report(), "https://other.example/catalog") is None


def test_robots_refusal_and_blocked_hint_with_the_rung_that_worked() -> None:
    assert say(reason="robots_denied", outcome="access_restriction") == {"key": "robots"}
    blocked = {"kind": "captcha", "docs": "docs/blocked.md#captcha"}
    assert say(outcome="access_restriction", reason="blocked", blocked=blocked) == {
        "key": "blocked",
        "kind": "captcha",
    }
    ladder = [{"level": "http", "outcome": "blocked"}, {"level": "browser", "outcome": "ok"}]
    got = say(blocked=blocked, acquisition={"render": "browser", "ladder": ladder})
    assert got == {"key": "blocked_passed", "kind": "captcha", "rung": "browser"}


def test_repair_and_drift_hints() -> None:
    assert say(repair={"decision": "promoted"}) == {"key": "repaired"}
    assert say(repair={"decision": "quarantined"}) == {"key": "repair_waiting"}
    assert say(repair={"drift": "high"}) == {"key": "drift"}
    assert say(repair={"drift": "low"}) is None


def test_light_never_gets_greener_than_the_status() -> None:
    assert light("ok", None) == "green"
    assert light("ok", {"key": "drift"}) == "amber"
    assert light("failed", {"key": "repaired"}) == "red"
    assert light("never", None) == "grey"
    assert light("ok", {"key": "robots"}) == "red"


def test_sentences_and_lights_are_written_by_the_page_without_dom() -> None:
    from tests.unit.studio_js import run_node

    run_node(
        """
const a = await import("STATIC/alerts.js");
const say = (x) => a.sayText(x);
assert.equal(say(null), "");
assert.match(say({ key: "robots" }), /asks programs not to read/);
assert.match(say({ key: "blocked", kind: "captcha" }),
  /turned Rastro away \\(it asked for a check/);
assert.match(say({ key: "blocked_passed", kind: "login_wall", rung: "browser" }),
  /let it in by opening it in a browser/);
assert.match(say({ key: "repair_waiting" }), /fix is ready/);
assert.equal(a.lightTone("red"), "fail");
assert.equal(a.lightTone("nonsense"), "none");
assert.equal(a.lightWord("amber"), "Needs a look");
"""
    )


def test_sites_view_shows_the_light_and_its_sentence() -> None:
    from tests.unit.studio_js import run_node

    run_node(
        """
const i18n = await import("STATIC/i18n.js");
const s = await import("STATIC/sources.js");
await i18n.setLang("en");
const amber = s.lightOf({status: "ok", light: "amber", say: {key: "blocked", kind: "captcha"}});
assert.equal(amber.tone, "warn");
assert.equal(amber.word, "Needs a look");
assert.ok(amber.say.length > 0 && !amber.say.includes("say."));
const old = s.lightOf({status: "failed", issue_code: "timeout"});
assert.equal(old.say, "");
assert.equal(old.tone, "fail");
"""
    )
