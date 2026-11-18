"""Studio Settings groups, Registry (URL), narrow-screen layout and Spanish copy (N8, N10, N12)."""

from __future__ import annotations

import re
from pathlib import Path

from lxml import html

from tests.unit.studio_js import STATIC, messages, run_node

ROOT = Path(__file__).resolve().parents[2]


def _page() -> html.HtmlElement:
    return html.fromstring((STATIC / "index.html").read_text(encoding="utf-8"))


def test_settings_are_folded_groups() -> None:
    """Brief section 7: next page, links, scroll, browser, network, connect, registry."""
    view = _page().get_element_by_id("view-settings")
    summaries = [d.find("summary").get("data-i18n") for d in view.findall(".//details")]
    assert summaries == [
        "settings.title", "spider.legend", "scroll.title", "settings.browser",
        "settings.network", "connect.title", "registry.title",
    ]  # fmt: skip
    for control in ("pagination-cap", "spider-depth", "scroll-steps", "allow-stealth", "connect"):
        assert _page().get_element_by_id(control).xpath("ancestor::details"), control


def test_settings_commands_are_real_cli_flags_only() -> None:
    """Design adaptation: every command shown in the page exists in the CLI."""
    trust = (ROOT / "src/rastro/cli_trust.py").read_text(encoding="utf-8")
    shown = " ".join(pre.text_content() for pre in _page().iter("pre"))
    for flag in re.findall(r"rastro studio (--[a-z-]+)", shown):
        assert f'"{flag}"' in trust, flag
    page_and_scripts = shown + "".join(p.read_text() for p in STATIC.glob("*.js"))
    for invented in ("rastro serve", "rastro export", "rastro recipes "):
        assert invented not in page_and_scripts


def test_registry_is_a_url_with_local_publish() -> None:
    """N10: "Registry (URL)", search/install from a folder or HTTPS, publish to a local folder."""
    english = messages()["en"]
    assert english["registry.title"] == "Registry (URL)"
    assert "does not run git" in english["registry.publishNote"]
    labels = " ".join(v for k, v in english.items() if k.startswith("registry."))
    assert "GitHub" not in labels and "Open pull request" not in labels
    assert _page().get_element_by_id("settings-registry").xpath("ancestor::details")


def test_narrow_screens_turn_tables_into_labelled_cards() -> None:
    """Brief: works from 360 px; results and before/after tables become cards with labels."""
    css = (STATIC / "studio.css").read_text(encoding="utf-8")
    narrow = css.split("@media (max-width: 40rem)", 1)[1]
    assert re.search(r"table\.results td::before[^{]*\{[^}]*content:\s*attr\(data-label\)", narrow)
    assert "min-height: 44px" in narrow and ".panel {" in narrow
    for script in ("results.js", "fix.js"):
        assert "dataset.label" in (STATIC / script).read_text(encoding="utf-8"), script


def test_views_follow_the_language_switch() -> None:
    """N8: text built in JS (sources, alerts, home) comes from the dictionary, in Spanish too."""
    run_node("""
const dom = await import("STATIC/dom.js");
dom.state.file = "agenda.toml";
const { setLang } = await import("STATIC/i18n.js");
setLang("es");
reply({
  "/api/sources": [200, {sources: [{url: "https:" + "//a.example/x", status: "capped",
    issue_code: null, last_fetch: null, read: 20, rejected: 0, cap: 20, warnings: []}]}],
  "/api/projects": [200, {projects: [], other_databases: [], truncated: false}],
});
const { loadSources } = await import("STATIC/sources.js");
await loadSources();
const sources = document.getElementById("sources-list").textContent;
assert.ok(sources.includes("Límite alcanzado") && sources.includes("Se leyeron 20 fichas"));
assert.ok(sources.includes("Subir el límite") && sources.includes("nunca"));
const home = await import("STATIC/home.js");
home.initHome({ openJob: async () => {}, switchView: () => {} });
await home.loadHome();
assert.ok(document.getElementById("home-list").textContent.includes("Empezar desde una URL"));
""")


def test_spanish_entries_are_translated_not_copied() -> None:
    """N8: Spanish strings differ from English except names, codes and placeholders."""
    lang = messages()
    same = {k for k in lang["en"] if lang["en"][k] == lang["es"][k]}
    allowed = {k for k in same if not re.search(r"[a-z]{4,} [a-z]{4,}", lang["en"][k])}
    assert same - allowed == set()
