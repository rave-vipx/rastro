"""Studio redesign stage 1: tokens, theme, es/en dictionary and app shell."""

import re

from lxml import html

from tests.unit.studio_js import STATIC, messages, run_node


def _messages() -> dict[str, dict[str, str]]:
    return messages()


def _page() -> html.HtmlElement:
    return html.fromstring((STATIC / "index.html").read_text(encoding="utf-8"))


def test_english_and_spanish_have_the_same_keys() -> None:
    """One dictionary, both languages complete; English is the default."""
    messages = _messages()
    assert set(messages) == {"en", "es"}
    assert set(messages["en"]) == set(messages["es"])
    assert all(value.strip() for lang in messages.values() for value in lang.values())


def test_every_static_text_key_exists_and_none_is_orphaned() -> None:
    """Each data-i18n key in the page is in the dictionary, and each entry is used,
    by the page, by a t("key") call, or by a key family a script builds (t(`code.${...}`))."""
    page = _page()
    used = {
        value
        for attr in ("data-i18n", "data-i18n-placeholder", "data-i18n-label")
        for value in page.xpath(f"//@{attr}")
    }
    keys = set(_messages()["en"])
    assert used - keys == set()
    scripts = "".join(p.read_text(encoding="utf-8") for p in STATIC.glob("*.js"))
    used |= set(re.findall(r'"([a-z]\w*(?:\.\w+)+)"', scripts))  # t("a.b"), t(x ? "a.b" : ...)
    families = tuple(re.findall(r"\bt\(`([\w.]+)\$\{", scripts))
    assert keys - used - {k for k in keys if families and k.startswith(families)} == set()


def test_spanish_copy_uses_voseo() -> None:
    """Rioplatense Spanish ("Usá páginas…"), not "Pega" or "usted"."""
    spanish = _messages()["es"]
    assert spanish["wizard.url.intro"].startswith("Usá")
    assert not any(re.search(r"\busted\b", v, re.IGNORECASE) for v in spanish.values())


def test_every_tab_has_a_view_and_one_starts_selected() -> None:
    """Main navigation (Projects, Hub) and project tabs (Results, Sites, Alerts, Settings)
    each switch to an existing view; Projects starts selected."""
    page = _page()
    tabs = page.xpath("//button[contains(concat(' ', @class, ' '), ' tab ')]")
    views = [tab.get("data-view") for tab in tabs]
    assert views[:2] == ["home", "hub"]
    assert views[2:6] == ["results", "sources", "alerts", "settings"]
    for view in views:
        assert page.get_element_by_id(f"view-{view}") is not None
    assert [tab.get("aria-pressed") for tab in tabs].count("true") == 1
    assert tabs[0].get("aria-pressed") == "true"


def test_shell_has_language_theme_and_advanced_controls() -> None:
    """Shell basics: skip link, labelled navigation, the Advanced switch and live regions."""
    page = _page()
    switch = page.get_element_by_id("pref-advanced")
    assert switch.get("role") == "switch" and switch.get("aria-checked") == "false"
    assert page.get_element_by_id("pref-lang") is not None
    assert page.get_element_by_id("pref-theme") is not None
    assert page.xpath("//a[@class='skip' and @href='#main']")
    assert all(nav.get("aria-label") for nav in page.xpath("//nav"))


def test_shell_renders_each_view_placeholder() -> None:
    """Every view switches in alone; the project tabs hide on Projects."""
    page = _page()
    views = [v.get("id")[5:] for v in page.xpath("//main/*[starts-with(@id, 'view-')]")]
    assert {"home", "hub", "results", "sources", "alerts", "settings", "repair", "wizard"} <= set(
        views
    )
    run_node(
        """
const shown = {};
const sections = views.map((name) => {
  const node = document.getElementById(`view-${name}`);
  node.id = `view-${name}`;
  return node;
});
document.querySelectorAll = (sel) => (sel.startsWith("main") ? sections : []);
const { state, switchView, $ } = await import("STATIC/dom.js");
state.file = "pages/shop.toml";
for (const name of views) {
  switchView(name);
  assert.deepEqual(sections.filter((s) => !s.hidden).map((s) => s.id), [`view-${name}`]);
  shown[name] = !$("project-nav").hidden;
}
assert.equal(shown.home, false);
assert.equal(shown.results, true);
""",
        views=views,
    )


def test_existing_controls_keep_their_ids() -> None:
    """No behaviour change: every id the Studio scripts look up is still in the page."""
    page_ids = set(_page().xpath("//@id"))
    scripts = "".join(p.read_text(encoding="utf-8") for p in STATIC.glob("*.js"))
    looked_up = set(re.findall(r'\$\("([a-z][a-z0-9-]*)"\)', scripts))
    dynamic = {"registry-section", "registry-results", "registry-status", "registry-query"}
    assert looked_up - page_ids - dynamic == set(), looked_up - page_ids


def test_no_script_parses_markup() -> None:
    """Guardrail: third-party text is set with textContent, never innerHTML/outerHTML."""
    for path in STATIC.glob("*.js"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"\.(inner|outer)HTML\b|insertAdjacentHTML", source), path.name


def _tokens(css: str, selector: str) -> dict[str, str]:
    start = css.index(selector)
    block = css[css.index("{", start) + 1 : css.index("}", start)]
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-f]{3,6})", block))


def _luminance(color: str) -> float:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


PAIRS = [
    ("text", "bg"), ("text", "surface"), ("text-2", "surface"), ("text-3", "surface"),
    ("text-3", "bg"), ("text-2", "surface-2"), ("accent", "surface"), ("on-accent", "accent"),
    ("ok", "surface"), ("warn", "surface"), ("fail", "surface"),
    ("ok", "ok-soft"), ("warn", "warn-soft"), ("fail", "fail-soft"),
    ("added", "surface"), ("changed", "surface"), ("removed", "surface"),
    ("added", "added-soft"), ("changed", "changed-soft"), ("removed", "removed-soft"),
    ("accent", "accent-soft"), ("text-2", "surface-3"),
]  # fmt: skip
TAG_TEXT = {"light": "#ffffff", "dark": "#111111"}  # field highlight labels sit on --hl-N


def test_light_and_dark_tokens_meet_aa_contrast() -> None:
    """Accessibility: every text/background token pair reaches 4.5:1 in both themes."""
    css = (STATIC / "studio.css").read_text(encoding="utf-8")
    themes = {"light": _tokens(css, ":root {"), "dark": _tokens(css, ':root[data-theme="dark"]')}
    failures = [
        f"{name}: {fg} on {bg} = {_contrast(tokens[fg], tokens[bg]):.2f}"
        for name, tokens in themes.items()
        for fg, bg in PAIRS
        if _contrast(tokens[fg], tokens[bg]) < 4.5
    ]
    failures += [
        f"{name}: label on hl-{n} = {_contrast(TAG_TEXT[name], tokens[f'hl-{n}']):.2f}"
        for name, tokens in themes.items()
        for n in range(1, 6)
        if _contrast(TAG_TEXT[name], tokens[f"hl-{n}"]) < 4.5
    ]
    assert failures == []


def test_dark_tokens_apply_for_system_and_explicit_choice() -> None:
    """Theme: dark follows the system unless light is chosen, and applies when chosen."""
    css = (STATIC / "studio.css").read_text(encoding="utf-8")
    system = css.split("@media (prefers-color-scheme: dark)", 1)[1]
    assert system.lstrip().startswith('{\n  :root:not([data-theme="light"])')
    assert _tokens(css, ':root:not([data-theme="light"])') == _tokens(
        css, ':root[data-theme="dark"]'
    )
    assert "@import" not in css and "url(" not in css  # no web fonts, no CDN


def test_loading_and_error_states_speak_to_screen_readers() -> None:
    """Shared states: loading reads as polite text over a hidden skeleton; an error is an
    alert with what happened, what to do and one retry button."""
    run_node("""
const { loadingState, errorState } = await import("STATIC/ui.js");
const loading = loadingState();
assert.equal(loading.getAttribute("aria-busy"), "true");
const [sketch, status] = loading.children;
assert.equal(sketch.getAttribute("aria-hidden"), "true");
assert.equal(status.getAttribute("aria-live"), "polite");
assert.equal(status.textContent, "Loading…");
let retried = 0;
const retry = () => { retried += 1; };
const error = errorState("Could not reach shop.example", "Check the address.", retry);
assert.equal(error.getAttribute("role"), "alert");
assert.ok(error.textContent.includes("Could not reach shop.example"));
const button = buttons(error);
assert.equal(button.length, 1);
assert.equal(button[0].textContent, "Try again");
await button[0].click();
assert.equal(retried, 1);
""")
