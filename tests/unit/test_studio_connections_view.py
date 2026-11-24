"""Settings view data mapping (no DOM), copy parity and Advanced-only terms."""
# ruff: noqa: E501

from __future__ import annotations

import json

from tests.unit.studio_js import STATIC, run_node

COPY = json.loads((STATIC / "i18n/settings.json").read_text(encoding="utf-8"))


def test_copy_has_the_same_keys_in_both_languages() -> None:
    assert COPY["en"].keys() == COPY["es"].keys()
    assert COPY["advanced"]["en"].keys() == COPY["advanced"]["es"].keys()
    assert COPY["advanced"]["en"].keys() <= COPY["en"].keys()


def test_plain_copy_keeps_technical_terms_in_advanced() -> None:
    plain = " ".join(v for k, v in COPY["en"].items() if k.startswith("conn."))
    for term in ("proxy", "robots", "stealth", "environment variable", "egress"):
        assert term not in plain.lower(), term


def test_data_mapping_without_a_dom() -> None:
    run_node(
        """
const d = await import("STATIC/connections-data.js");
const opts = d.modelOptions({detected: [{spec: "ollama:m", provider: "ollama", models: ["m", "n"], cost: "free"}]});
assert.equal(opts[0].cost.key, "conn.model.free");
assert.equal(opts[0].others, 1);
assert.equal(d.costOf(0.02).vars.usd, "0.02");
assert.equal(d.costOf(null).key, "conn.model.costUnknown");
assert.equal(d.channelState({configured: false}).key, "conn.chan.missing");
assert.equal(d.channelState({configured: true, last: null}).key, "conn.chan.ready");
assert.equal(d.channelState({configured: true, last: {sent: true, at: "x"}}).tone, "ok");
assert.equal(d.channelState({configured: true, last: {sent: false, at: "x", reason: "bad"}}).vars.why, "bad");
assert.equal(d.scheduleLine({set: false}).key, "conn.sched.none");
assert.equal(d.scheduleLine({set: true, words: "every day", next: ["a", "b", "c", "d"]}).runs.length, 3);
assert.deepEqual(d.proxyRows({proxies: [{name: "p", host: "h", port: 1, env: ["U"]}], allowed: false}),
  [{name: "p", address: "h:1", env: ["U"], canTest: false}]);
assert.equal(d.grantRows({origins: [{origin: "mcp", capabilities: [{name: "stealth", on: true, locked: false}]}]})[0].id, "mcp:stealth");
assert.equal(d.currentModel({current: null}).key, "conn.model.none");
"""
    )
