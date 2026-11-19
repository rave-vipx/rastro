"""N3/N4/N9, SEC-008: Studio bounds follow domain contracts without copied caps."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, StrictInt

from rastro.domain.jobs import Job
from rastro.domain.recipes import Pagination
from rastro.domain.scroll import ScrollSpec
from rastro.studio import bounds

# bounds.js builds its labels with t(); the sliced code gets the real dictionary (N8).
I18N = Path("src/rastro/studio/static/i18n.js").resolve().as_uri()
I18N_IMPORT = f'const {{ t }} = await import("{I18N}");\n'


def test_controls_follow_changed_domain_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    class ChangedScroll(ScrollSpec):
        max_steps: StrictInt = Field(default=8, ge=2, le=17, multiple_of=1)
        settle_ms: StrictInt = Field(default=600, ge=200, le=2400, multiple_of=100)

    class ChangedPagination(Pagination):
        max_pages: StrictInt = Field(default=7, ge=2, le=23)

    monkeypatch.setattr(bounds, "Pagination", ChangedPagination)
    monkeypatch.setattr(bounds, "ScrollSpec", ChangedScroll)
    payload = bounds.controls(Job.model_construct(sources=(), acquisition=None))
    assert payload["limits"]["scroll"] == {
        "max_steps": {"min": 2, "max": 17, "step": 1, "default": 8},
        "settle_ms": {"min": 200, "max": 2400, "step": 100, "default": 600},
    }
    assert payload["limits"]["pagination"]["max_pages"] == {
        "min": 2,
        "max": 23,
        "step": 1,
        "default": 7,
    }
    check_browser_controls(payload)

    from rastro.domain.spider import SpiderPolicy

    schema = SpiderPolicy.model_json_schema()["properties"]
    assert payload["limits"]["spider"]["max_depth"]["max"] == schema["max_depth"]["maximum"]
    pages = next(item for item in schema["max_pages"]["anyOf"] if item.get("type") == "integer")
    assert payload["limits"]["spider"]["max_pages"]["max"] == pages["maximum"]


def test_no_copied_numeric_bounds() -> None:
    root = Path("src/rastro/studio")
    for path in [root / "bounds.py", root / "routes_settings.py", *(root / "static").glob("*")]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(
            r"\b(?:1\s*[-\u2013]\s*(?:4|30|50|100)|100\s*[-\u2013]\s*3000)\b", text
        ), path
        assert not re.search(r'(?:min|max)=["\'](?:4|30|50|100|3000)["\']', text), path
        assert not re.search(
            r"(?:max_steps|settle_ms|max_depth|max_pages)\s*[:=]\s*(?:30|50|3000|4|100)\b",
            text,
            re.IGNORECASE,
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
            + I18N_IMPORT
            + fill
            + "const payload = "
            + json.dumps(payload)
            + ";"
            + """
fillBounds(payload);
for (const [kind, name, id] of [
  ["pagination", "max_pages", "pagination-cap"],
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
payload.limits.scroll.max_steps = {step: 1};
fillBounds(payload);
assert.equal($("scroll-steps").min, "");
assert.equal($("scroll-steps").max, "");
assert.equal($("scroll-steps-range").textContent, "");

""",
        ],
        check=True,
    )


def test_unbounded_integer_does_not_break_controls() -> None:
    """N3/N9: optional schema bounds do not make the controls payload fail."""

    class Unbounded(BaseModel):
        count: int

    assert bounds.numeric_limits(Unbounded) == {
        "count": {"min": None, "max": None, "step": 1, "default": None}
    }


def test_acquisition_controls_have_separate_rows() -> None:
    """N3/N9: link and scroll controls share a single-column layout."""
    from lxml import html

    root = Path("src/rastro/studio/static")
    page = html.fromstring((root / "index.html").read_text(encoding="utf-8"))
    for control in (
        "spider-enabled",
        "spider-depth",
        "spider-pages",
        "spider-same-host",
        "scroll-enabled",
        "scroll-steps",
    ):
        assert page.xpath(
            f'//*[@id="{control}"]/ancestor::fieldset[contains(@class, "bound-controls")]'
        )
    css = (root / "studio.css").read_text(encoding="utf-8")
    assert re.search(r"\.bound-controls\s*\{[^}]*display:\s*grid", css)
    assert re.search(r"\.bound-controls\s*\{[^}]*grid-template-columns:\s*1fr", css)
