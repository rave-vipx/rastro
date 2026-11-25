"""Studio copy split per view, English and Spanish, with technical wording behind Advanced."""

from __future__ import annotations

import re

from lxml import html

from rastro.studio.routes_jobs import I18N_ASSETS
from tests.unit.studio_js import STATIC, messages, run_node, view_messages

# Words a newcomer should not meet unless they turned Advanced on.
JARGON = re.compile(
    r"recipe|receta|selector|toml|job file|archivos? del? job|sqlite", re.IGNORECASE
)


def test_each_view_file_has_both_languages_with_the_same_keys() -> None:
    views = view_messages()
    assert {"core", "wizard", "results", "health", "settings", "hub", "run"} <= set(views)
    seen: set[str] = set()
    for name, data in views.items():
        assert set(data) == {"en", "es", "advanced"}, name
        en, es, advanced = data["en"], data["es"], data["advanced"]
        assert isinstance(en, dict) and isinstance(es, dict) and isinstance(advanced, dict)
        assert set(en) == set(es), (name, set(en) ^ set(es))
        assert set(advanced) == {"en", "es"}, name
        assert set(advanced["en"]) == set(advanced["es"]), name
        assert set(advanced["en"]) <= set(en), name  # every technical text has a plain one
        assert not seen & set(en), (name, seen & set(en))  # one key, one file
        seen |= set(en)
        texts = [*en.values(), *es.values(), *advanced["en"].values(), *advanced["es"].values()]
        assert all(isinstance(v, str) and v.strip() for v in texts), name


def test_normal_copy_has_no_jargon_and_advanced_keeps_it() -> None:
    normal = messages()
    leaks = [f"{lang}:{k}" for lang, d in normal.items() for k, v in d.items() if JARGON.search(v)]
    assert leaks == []
    advanced = messages(advanced=True)
    assert "SQLite" in advanced["en"]["home.exportFormats"]
    assert "SQLite" not in normal["en"]["home.exportFormats"]


def _visible_text(node: html.HtmlElement) -> list[str]:
    if node.get("data-adv") is not None or node.tag in {"script", "style"}:
        return []
    texts = [node.text or ""]
    for child in node:
        texts += _visible_text(child)
        texts.append(child.tail or "")
    return texts


def test_page_shows_no_jargon_outside_advanced_sections() -> None:
    page = html.fromstring((STATIC / "index.html").read_text(encoding="utf-8"))
    body = page.find("body")
    assert body is not None
    found = [t.strip() for t in _visible_text(body) if JARGON.search(t)]
    assert found == []
    assert page.xpath("//*[@data-adv]"), "technical sections are marked for Advanced"


def test_loader_imports_every_view_file_and_each_is_served() -> None:
    source = (STATIC / "i18n.js").read_text(encoding="utf-8")
    listed = re.search(r"const NAMES = \[([^\]]*)\]", source)
    assert listed is not None
    imported = set(re.findall(r'"(\w+)"', listed.group(1)))
    assert imported == set(view_messages())
    assert {f"{name}.json" for name in imported} == I18N_ASSETS


def test_advanced_switch_changes_wording_and_language_keeps_it() -> None:
    run_node("""
const i18n = await import("STATIC/i18n.js");
const seen = [];
i18n.onLangChange(() => seen.push(i18n.lang()));
assert.equal(i18n.advanced(), false);
assert.ok(!i18n.t("home.exportFormats").includes("SQLite"));
i18n.setAdvanced(true);
assert.equal(document.documentElement.dataset.advanced, "on");
assert.ok(i18n.t("home.exportFormats").includes("SQLite"));
i18n.setLang("es");
assert.ok(i18n.t("home.exportFormats").includes("SQLite"));
assert.equal(i18n.t("nav.sources"), "Sitios");
i18n.setAdvanced(false);
assert.equal(document.documentElement.dataset.advanced, undefined);
assert.ok(!i18n.t("home.exportFormats").includes("SQLite"));
assert.equal(i18n.t("brand"), "Rastro Studio");
assert.deepEqual(seen, ["en", "es", "es"]);
""")
