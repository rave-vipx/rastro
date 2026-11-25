"""Hub view: copy parity, plain wording, DOM-free data mapping, permission prompt, tab fix."""

from __future__ import annotations

import re

from tests.unit.studio_js import STATIC, messages, run_node, view_messages

JARGON = re.compile(r"recipe|receta|selector|toml|job file|sqlite", re.IGNORECASE)


def test_hub_copy_has_equal_keys_and_no_jargon_outside_advanced() -> None:
    hub = view_messages()["hub"]
    assert set(hub["en"]) == set(hub["es"])  # type: ignore[arg-type]
    plain = [*hub["en"].values(), *hub["es"].values()]  # type: ignore[union-attr]
    assert [text for text in plain if JARGON.search(text)] == []
    assert any(k.startswith("hub.cap.") for k in hub["en"])  # type: ignore[union-attr]
    assert "Page actions" in messages(advanced=True)["en"]["hub.cap.page_actions"]


def test_every_capability_has_plain_copy_in_both_languages() -> None:
    run_node(
        """
const { CAPABILITIES } = await import("STATIC/hub_model.js");
const { MESSAGES } = await import("STATIC/i18n.js");
for (const name of CAPABILITIES) {
  for (const lang of ["en", "es"]) assert.ok(MESSAGES[lang][`hub.cap.${name}`], name);
}
"""
    )


def test_card_model_maps_an_offer_without_a_dom() -> None:
    run_node(
        """
const { cardModel, asksPermission, siteQuery } = await import("STATIC/hub_model.js");
const offer = { id: "shop-a", version: "2.0.0", registry: "hub",
  source_patterns: ["shop.example/p/*"],
  card: { fields: [{ name: "title", description: "Name" }],
    engine_needs: { render: "browser", representation: "html",
      capabilities: ["page_actions", "real_chrome", "<b>x</b>"] } },
  health: { passed: true, last_verified: "2026-11-20" } };
const m = cardModel(offer, new Date("2026-11-24T10:00:00Z"));
assert.equal(m.site, "shop.example"); assert.equal(m.spec, "shop-a@2.0.0");
assert.equal(m.health, "ok"); assert.equal(m.days, 4); assert.equal(m.browser, true);
assert.deepEqual(m.needs, { allowable: ["page_actions"], blocked: ["real_chrome"], unknown: 1 });
assert.equal(asksPermission(m), true);
const bare = cardModel({ id: "a", version: "1.0.0", source_patterns: ["a.example"],
  card: null, health: null });
assert.equal(bare.health, "none"); assert.equal(asksPermission(bare), false);
assert.equal(siteQuery(" shop.example "), "https://shop.example");
assert.equal(siteQuery("https://shop.example/x"), "https://shop.example/x");
assert.equal(siteQuery("  "), "");
"""
    )


def test_permission_prompt_offers_site_only_allowing() -> None:
    run_node(
        """
const { cardModel } = await import("STATIC/hub_model.js");
const { permissionPrompt } = await import("STATIC/hub_explore.js");
const model = cardModel({ id: "a", version: "1.0.0", source_patterns: ["shop.example/*"],
  card: { fields: [{ name: "t", description: "d" }],
    engine_needs: { render: "stealth", representation: "html", capabilities: ["stealth"] } },
  health: null });
let choice = "unset";
const box = permissionPrompt(model, (v) => { choice = v; });
const text = box.textContent;
assert.ok(text.includes("only for shop.example") || text.includes("Allow only for shop.example"));
const labels = buttons(box).map((b) => b.textContent);
assert.deepEqual(labels, ["Allow only for shop.example", "Install without allowing", "Cancel"]);
buttons(box)[0].click(); assert.equal(choice, "site");
buttons(box)[2].click(); assert.equal(choice, null);
"""
    )


def test_hub_view_hides_the_project_tabs() -> None:
    run_node(
        """
const dom = await import("STATIC/dom.js");
dom.state.file = "agenda.toml";
const nav = dom.$("project-nav"); const crumb = dom.$("crumb-project");
dom.switchView("hub"); assert.equal(nav.hidden, true); assert.equal(crumb.hidden, true);
dom.switchView("results"); assert.equal(nav.hidden, false); assert.equal(crumb.hidden, false);
"""
    )


def test_copy_is_loaded_with_fetch_not_json_modules() -> None:
    source = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert "fetch(" in source and 'type: "json"' not in source
