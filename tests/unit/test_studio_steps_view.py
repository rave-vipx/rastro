# ruff: noqa: E501
"""Advanced, Settings: the recorded page steps in plain words, delete and look again."""

from __future__ import annotations

from tests.unit.studio_js import run_node


def test_steps_read_as_sentences_and_delete_saves() -> None:
    run_node(
        """
const i18n = await import("STATIC/i18n.js");
const dom = await import("STATIC/dom.js");
const steps = await import("STATIC/steps.js");
await i18n.setLang("en");
assert.equal(steps.stepText({action: "click", selector: "button.more"}), "Click button.more");
assert.equal(steps.stepText({action: "scroll", times: 3}), "Scroll down 3 times");
assert.equal(steps.stepText({action: "wait"}), "Wait a moment");
assert.equal(steps.stepText({action: "type", selector: "input", text: "lamp"}), "Type “lamp” in input");
assert.equal(steps.stepText({action: "nope"}), "Another step");
Object.assign(dom.state, {file: "shop.toml", sha: "a", ai: {enabled: true}});
const rows = [{source: null, index: 0, action: "click", selector: "button.more"},
              {source: null, index: 1, action: "scroll", times: 2}];
reply({"/api/steps": [200, {ok: true, sha256: "a", explore: true, steps: rows}],
       "/api/steps/delete": [200, {ok: true, sha256: "b", steps: [rows[1]]}]});
steps.initSteps({openJob: async () => {}, switchView() {}});
await steps.loadSteps();
const box = document.getElementById("steps-section");
assert.ok(box.textContent.includes("Click button.more"));
const again = buttons(box).find((b) => b.textContent === "Let the AI look again");
assert.ok(again);
await buttons(box).find((b) => b.textContent === "Delete").click();
assert.deepEqual(calls.at(-1), {path: "/api/steps/delete", body: {file: "shop.toml", sha256: "a", source: null, index: 0}});
assert.equal(dom.state.sha, "b");
assert.ok(!box.textContent.includes("Click button.more") && box.textContent.includes("Scroll down 2 times"));
i18n.setLang("es");
assert.ok(box.textContent.includes("Bajar 2 veces"));  // rebuilt when the language changes
"""
    )
