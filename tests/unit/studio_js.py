"""Run Studio's real browser modules under Node with a tiny fake DOM (tests only).

Tests using ``run_node`` are skipped when Node is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "rastro" / "studio" / "static"

FAKE_DOM = """
class Text { constructor(s) { this.text = String(s); } get textContent() { return this.text; } }
class Node {
  constructor(tag) {
    Object.assign(this, { tagName: tag.toUpperCase(), children: [], attrs: {}, dataset: {},
      className: "", listeners: {}, hidden: false, disabled: false, value: "", checked: false });
  }
  append(...items) {
    for (const x of items) this.children.push(typeof x === "string" ? new Text(x) : x);
  }
  remove() {}
  replaceChildren(...items) { this.children = []; this.append(...items); }
  set textContent(v) { this.children = [new Text(v)]; }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(k, f) { this.listeners[k] = f; }
  click() { if (this.listeners.click) return this.listeners.click({ stopPropagation() {} }); }
  focus() { document.focused = this; }
  querySelector() { return null; }
  get tHead() { return this.children.find((c) => c.tagName === "THEAD"); }
  get tBodies() { return this.children.filter((c) => c.tagName === "TBODY"); }
}
import { readFileSync } from "node:fs";
const byId = {};
// Studio loads its copy with fetch(); here file: URLs are read from disk.
const diskFetch = async (url) => ({ ok: true, status: 200,
  json: async () => JSON.parse(readFileSync(new URL(url), "utf8")) });
globalThis.fetch = diskFetch;
const events = [];
globalThis.Node = Node;
globalThis.Option = class extends Node {
  constructor(text, value) { super("option"); this.textContent = text; this.value = value; }
};
globalThis.CustomEvent = class {
  constructor(type, init) { this.type = type; this.detail = init?.detail; }
};
globalThis.document = {
  createElement: (tag) => new Node(tag),
  createTextNode: (s) => new Text(s),
  getElementById: (id) => (byId[id] ??= new Node("div")),
  querySelectorAll: () => [],
  addEventListener() {},
  dispatchEvent: (event) => events.push(event),
  documentElement: new Node("html"),
};
globalThis.window = { opened: [], open(...args) { this.opened.push(args); } };
const walk = (n, out = []) => {
  out.push(n); for (const c of n.children || []) walk(c, out); return out;
};
const tags = (n) => walk(n).map((x) => x.tagName).filter(Boolean);
const classes = (n) => walk(n).map((x) => x.className || "").join(" ");
const buttons = (n) => walk(n).filter((x) => x.tagName === "BUTTON");
// fetch answers from a {path: [status, body-or-null]} table; null body = not JSON.
const calls = [];
const reply = (table) => {
  globalThis.fetch = async (path, options) => {
    if (String(path).startsWith("file:")) return diskFetch(path);
    calls.push({ path, body: options?.body ? JSON.parse(options.body) : undefined });
    const [status, body] = table[path.split("?")[0]] ?? [404, null];
    return { ok: status < 400, status,
      json: async () => { if (body === null) throw new SyntaxError("not json"); return body; } };
  };
};
"""


def view_messages() -> dict[str, dict[str, object]]:
    """Each per-view dictionary under ``static/i18n``, by file stem."""
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((STATIC / "i18n").glob("*.json"))
    }


def messages(*, advanced: bool = False) -> dict[str, dict[str, str]]:
    """All views merged per language; ``advanced`` overlays the technical wording."""
    merged: dict[str, dict[str, str]] = {"en": {}, "es": {}}
    for data in view_messages().values():
        for lang in merged:
            merged[lang].update(data[lang])  # type: ignore[arg-type]
            if advanced:
                merged[lang].update(data["advanced"][lang])  # type: ignore[index]
    return merged


def run_node(body: str, **data: object) -> None:
    """Run ``body`` as an ES module; ``STATIC/`` is the static folder, ``data`` become consts."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed for the standalone JS assertion")
    consts = "".join(f"const {name} = {json.dumps(value)};\n" for name, value in data.items())
    imports = "import assert from 'node:assert/strict';\n" + FAKE_DOM + consts
    script = imports + body.replace("STATIC/", STATIC.as_uri() + "/")
    done = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr[-2000:]
