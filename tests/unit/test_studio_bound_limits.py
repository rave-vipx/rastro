"""N3/N9, SEC-008: Studio bounds follow domain contracts without copied caps."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import Field, StrictInt

from rastro.domain.jobs import Job
from rastro.domain.scroll import ScrollSpec
from rastro.studio import bounds


def test_controls_follow_changed_domain_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    class ChangedScroll(ScrollSpec):
        max_steps: StrictInt = Field(default=8, ge=2, le=17, multiple_of=1)
        settle_ms: StrictInt = Field(default=600, ge=200, le=2400, multiple_of=100)

    monkeypatch.setattr(bounds, "ScrollSpec", ChangedScroll)
    payload = bounds.controls(Job.model_construct(sources=(), acquisition=None))
    assert payload["limits"]["scroll"] == {
        "max_steps": {"min": 2, "max": 17, "step": 1, "default": 8},
        "settle_ms": {"min": 200, "max": 2400, "step": 100, "default": 600},
    }
    check_browser_controls(payload)

    from rastro.domain.spider import SpiderPolicy

    schema = SpiderPolicy.model_json_schema()["properties"]
    for name in ("max_depth", "max_pages"):
        assert payload["limits"]["spider"][name]["max"] == schema[name]["maximum"]


def test_no_copied_numeric_bounds() -> None:
    root = Path("src/rastro/studio")
    for path in [root / "bounds.py", *(root / "static").glob("*")]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(
            r"\b(?:1\s*[-\u2013]\s*(?:4|30|100)|100\s*[-\u2013]\s*3000)\b", text
        ), path
        assert not re.search(r'(?:min|max)=["\'](?:4|30|100|3000)["\']', text), path
        assert not re.search(
            r"(?:max_steps|settle_ms|max_depth|max_pages)\s*[:=]\s*(?:30|3000|4|100)\b", text
        ), path


def check_browser_controls(payload: dict) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed for the standalone JS assertion")
    source = Path("src/rastro/studio/static/bounds.js").read_text(encoding="utf-8")
    fill = source[source.index("let spider") : source.index("async function save")].replace(
        "export ", ""
    )
    subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            """
import assert from 'node:assert/strict';
const nodes = {};
const $ = id => nodes[id] ??= {};
const show = (id, text) => { $(id).textContent = text; };
"""
            + fill
            + "const payload = "
            + json.dumps(payload)
            + ";"
            + """
fillBounds(payload);
for (const [kind, name, id] of [
  ["spider", "max_depth", "spider-depth"], ["spider", "max_pages", "spider-pages"],
  ["scroll", "max_steps", "scroll-steps"], ["scroll", "settle_ms", "scroll-settle"],
]) {
  const limit = payload.limits[kind][name];
  assert.equal($(id).min, limit.min);
  assert.equal($(id).max, limit.max);
  assert.equal($(id).step, limit.step);
  assert.equal($(id).value, limit.default);
  assert.equal(
    $(id + "-range").textContent, `(${limit.min}\u2013${limit.max}, step ${limit.step})`);
}
fillBounds({...payload, scroll: {max_steps: 6, settle_ms: 800}});
assert.equal($("scroll-steps").value, 6);
assert.equal($("scroll-settle").value, 800);
""",
        ],
        check=True,
    )
