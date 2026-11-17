"""Studio redesign views: results, provenance, sources, alerts, fix, wizard, home (N1-N12).

Static checks run everywhere; behaviour checks run the real modules under Node with a tiny
fake DOM and are skipped when Node is not installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from lxml import html

from rastro.studio.routes_jobs import ASSETS

STATIC = Path(__file__).resolve().parents[2] / "src" / "rastro" / "studio" / "static"

FAKE_DOM = """
class Text { constructor(s) { this.text = String(s); } get textContent() { return this.text; } }
class Node {
  constructor(tag) {
    Object.assign(this, { tagName: tag.toUpperCase(), children: [], attrs: {}, dataset: {},
      className: "", listeners: {}, hidden: false, disabled: false, value: "" });
  }
  append(...items) {
    for (const x of items) this.children.push(typeof x === "string" ? new Text(x) : x);
  }
  replaceChildren(...items) { this.children = []; this.append(...items); }
  set textContent(v) { this.children = [new Text(v)]; }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(k, f) { this.listeners[k] = f; }
  get tHead() { return this.children.find((c) => c.tagName === "THEAD"); }
  get tBodies() { return this.children.filter((c) => c.tagName === "TBODY"); }
}
const byId = {};
globalThis.Node = Node;
globalThis.document = {
  createElement: (tag) => new Node(tag),
  createTextNode: (s) => new Text(s),
  getElementById: (id) => (byId[id] ??= new Node("div")),
  querySelectorAll: () => [],
  documentElement: new Node("html"),
};
globalThis.window = { opened: [], open(...args) { this.opened.push(args); } };
const walk = (n, out = []) => {
  out.push(n); for (const c of n.children || []) walk(c, out); return out;
};
const tags = (n) => walk(n).map((x) => x.tagName).filter(Boolean);
const classes = (n) => walk(n).map((x) => x.className || "").join(" ");
"""


def _scripts() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in STATIC.glob("*.js")}


def _messages() -> dict[str, dict[str, str]]:
    source = (STATIC / "i18n.js").read_text(encoding="utf-8")
    return json.loads(source.split("/* i18n:begin */", 1)[1].split("/* i18n:end */", 1)[0])


def run_node(body: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed for the standalone JS assertion")
    imports = "import assert from 'node:assert/strict';\n" + FAKE_DOM
    script = imports + body.replace("STATIC/", STATIC.as_uri() + "/")
    subprocess.run([node, "--input-type=module", "-e", script], check=True, timeout=60)


def test_every_script_and_stylesheet_is_served() -> None:
    """Each static module is in the served allow-list, and nothing else is."""
    assert {p.name for p in STATIC.iterdir() if p.suffix in {".js", ".css"}} == ASSETS


def test_every_js_message_key_exists() -> None:
    """N8: each t("key") the scripts build is in the English and Spanish dictionary."""
    keys = set(_messages()["en"])
    sources = [src for name, src in _scripts().items() if name != "i18n.js"]
    used = {k for src in sources for k in re.findall(r'\bt\("([\w.]+)"', src)}
    assert used - keys == set()


def test_every_script_module_stays_small() -> None:
    """docs/development/clean-code.md: view modules stay under the size budget (data excluded)."""
    for name, source in _scripts().items():
        if name != "i18n.js":
            assert source.count("\n") <= 250, name


def test_text_helper_interpolates_and_falls_back() -> None:
    """N8: t() fills {placeholders} as text, and unknown keys show the key, not nothing."""
    run_node("""
const { t, setLang } = await import("STATIC/i18n.js");
assert.equal(t("results.range", {from: 1, to: 25, total: 74}), "Records 1\u201325 of 74");
setLang("es");
assert.equal(t("results.range", {from: 1, to: 25, total: 74}), "Registros 1\u201325 de 74");
assert.equal(t("no.such.key"), "no.such.key");
""")


def test_cell_model_keeps_each_empty_reason_and_summarizes_tiers() -> None:
    """N5, DATA-004: empty cells keep their reason; several prices become a summary."""
    run_node("""
const { cellModel, tiers } = await import("STATIC/cells.js");
assert.deepEqual(cellModel(null, "not_published"), {kind: "not_published"});
assert.deepEqual(cellModel(null, "source_failed"), {kind: "source_failed"});
assert.deepEqual(cellModel("", undefined), {kind: "empty"});
assert.deepEqual(cellModel({state: "source_failed", text: null}), {kind: "source_failed"});
assert.deepEqual(cellModel({state: "free", text: "Gratis"}), {kind: "free", text: "Gratis"});
assert.deepEqual(cellModel("Sin costo / $ 160 / $ 199"), {kind: "tiers", text: "Sin costo", n: 3});
const two = cellModel([{precio: "$ 160"}, {precio: "$ 199"}]);
assert.deepEqual(two, {kind: "tiers", text: "$ 160", n: 2});
assert.equal(cellModel("https:" + "//x.example/a.png").kind, "url");
assert.deepEqual(cellModel("$ 450"), {kind: "text", text: "$ 450"});
assert.equal(tiers("$ 450"), null);
""")


def test_every_status_rendering_has_a_word_and_a_shape() -> None:
    """N5 accessibility: status is a shape plus words, never color alone."""
    run_node("""
const { chip, emptyCell } = await import("STATIC/ui.js");
for (const reason of ["not_published", "source_failed", null]) {
  const node = emptyCell(reason);
  assert.ok(node.textContent.trim().length > 3, String(reason));
  assert.match(classes(node), /ico-(minus|octagon)/);
}
for (const tone of ["ok", "warn", "fail"]) {
  const node = chip(tone, "word");
  assert.equal(node.textContent, "word");
  assert.match(classes(node), /ico-(circle|triangle|octagon)/);
}
""")


def test_links_and_images_from_data_never_load_on_their_own() -> None:
    """Brief guardrail: a data URL is text plus an explicit open button, never <img> or <a>."""
    run_node("""
const { blockedUrl } = await import("STATIC/ui.js");
const node = blockedUrl("https:" + "//cdn.example/poster.jpg");
assert.ok(!tags(node).includes("IMG") && !tags(node).includes("A"));
assert.equal(window.opened.length, 0);
const button = walk(node).find((n) => n.tagName === "BUTTON");
button.listeners.click({ stopPropagation() {} });
assert.deepEqual(window.opened[0].slice(1), ["_blank", "noopener,noreferrer"]);
const unsafe = blockedUrl("javascript:alert(1)");
assert.ok(!walk(unsafe).some((n) => n.tagName === "BUTTON"));
""")


def test_results_controls_are_in_the_page() -> None:
    """N5: search, filters, pager and export controls exist and are labelled."""
    page = html.fromstring((STATIC / "index.html").read_text(encoding="utf-8"))
    for control in ("rs-search", "rs-source", "rs-state", "rs-date", "rs-per-page"):
        node = page.get_element_by_id(control)
        assert node.xpath("ancestor::label"), control
    for button in ("prev-page", "next-page", "export-json", "export-html"):
        assert page.get_element_by_id(button).tag == "button"
