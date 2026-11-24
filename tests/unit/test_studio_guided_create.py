# ruff: noqa: E501
"""Studio guided create: the words and shapes the wizard builds from route data."""

from __future__ import annotations

from tests.unit.studio_js import run_node

IMPORTS = """
const i18n = await import("STATIC/i18n.js");
const wizard = await import("STATIC/wizard.js");
const correct = await import("STATIC/correct.js");
const pick = await import("STATIC/pick.js");
"""


def test_address_is_checked_live_in_plain_words() -> None:
    run_node(
        IMPORTS
        + """
assert.deepEqual(wizard.urlState(" https://shop.example/list?page=2 "), {ok: true, host: "shop.example"});
assert.deepEqual(wizard.urlState("ftp://shop.example"), {ok: false, key: "wizard.url.notWeb"});
assert.deepEqual(wizard.urlState(""), {ok: false, key: "wizard.needUrl"});
assert.equal(i18n.t("wizard.url.notWeb"), "That address doesn't look like a web page.");
"""
    )


def test_estimate_reads_as_requests_and_minutes() -> None:
    run_node(
        IMPORTS
        + """
const quick = correct.estimateText({requests_max: 1, minutes_min: 0.001, minutes_max: 0.05});
assert.equal(quick, "It will make about 1 requests and take less than a minute.");
const long = correct.estimateText({requests_max: 120, minutes_min: 4.2, minutes_max: 5.3});
assert.equal(long, "It will make about 120 requests and take 4 to 6 minutes.");
i18n.setLang("es");
assert.match(correct.estimateText({requests_max: 25, minutes_min: 0.4, minutes_max: 1.65}),
  /unos 25 pedidos y tardar de 1 a 2 minutos/);
"""
    )


def test_exploration_events_become_a_narration() -> None:
    run_node(
        IMPORTS
        + """
const seen = [
  {kind: "observed", records: 24},
  {kind: "kept", records: 36, added: 12, action: "click", target: "More"},
  {kind: "dropped", records: 36, action: "scroll", target: null, reason: "no_gain"},
  {kind: "stopped", records: 36, reason: "model_done"},
];
assert.deepEqual(seen.map(correct.narrate), [
  "The page shows 24 records.", "I clicked “More”: +12 records.",
  "I scrolled “”, but it didn't help.", "I stopped with 36 records.",
]);
i18n.setLang("es");
assert.equal(correct.narrate(seen[1]), "Hice clic en «More»: +12 registros.");
"""
    )


def test_highlights_map_fields_to_five_distinct_outlines() -> None:
    run_node(
        IMPORTS
        + """
const raw = ["a", "b", "c", "d", "e", "f"].map((field, i) => ({field, matches: i, boxes: [{x: 10, y: 20, w: 50, h: 10}]}));
const model = pick.highlightModel(raw);
assert.deepEqual(model.map((m) => m.n), [1, 2, 3, 4, 5, 0]);
assert.deepEqual(model[5].boxes, []);
assert.deepEqual(pick.boxStyle({x: 50, y: 0, w: 300, h: 1000}, 200, 400),
  {left: "25.000%", top: "0.000%", width: "100.000%", height: "100.000%"});
"""
    )


def test_api_offer_and_hub_card_hide_technical_detail_until_advanced() -> None:
    run_node(
        IMPORTS
        + """
const offer = {host: "shop.example", path: "/api/items", records: 96, record_path: "/data/items", covers: ["name", "price"]};
assert.deepEqual(correct.offerLines(offer), ["It holds about 96 records.", "It brings: name, price."]);
i18n.setAdvanced(true);
assert.equal(correct.offerLines(offer)[2], "shop.example/api/items · records at /data/items");
const hub = {id: "shop-example", version: "3.0.0", last_verified: null,
  card: {fields: [{name: "name"}, {name: "price"}], engine_needs: {capabilities: ["stealth"]}}};
assert.ok(correct.hubLines(hub, "shop.example").includes("Hub recipe shop-example@3.0.0"));
i18n.setAdvanced(false);
const plain = correct.hubLines(hub, "shop.example");
assert.deepEqual(plain, ["Someone already built this for shop.example.", "Fields: name, price",
  "Version 3.0.0", "Asks permission for: stealth"]);
"""
    )


def test_no_hub_offer_goes_to_what_you_want_with_an_estimate() -> None:
    run_node(
        IMPORTS
        + """
globalThis.Node.prototype.before = function () {};
reply({"/api/wizard/lookup": [200, {ok: true, offers: [], hub: "ok"}],
       "/api/wizard/estimate": [200, {ok: true, requests_max: 1, minutes_min: 0, minutes_max: 0.05}]});
wizard.initWizard({});
document.getElementById("page-url").value = "https://shop.example/list";
await document.getElementById("wiz-next-1").click();
await new Promise((done) => setTimeout(done, 0));
assert.deepEqual(calls.map((c) => c.path), ["/api/wizard/lookup", "/api/wizard/estimate"]);
assert.deepEqual(calls[1].body, {url: "https://shop.example/list", more: false});
assert.equal(document.getElementById("wiz-step-2").hidden, false);
assert.equal(document.getElementById("wiz-step-1").hidden, true);
"""
    )
