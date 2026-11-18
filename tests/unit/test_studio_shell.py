"""Studio redesign stage 1: tokens, theme, es/en dictionary and app shell (N1, N5, N8)."""

import json
import re
from pathlib import Path

from lxml import html

STATIC = Path(__file__).resolve().parents[2] / "src" / "rastro" / "studio" / "static"


def _messages() -> dict[str, dict[str, str]]:
    source = (STATIC / "i18n.js").read_text(encoding="utf-8")
    block = source.split("/* i18n:begin */", 1)[1].split("/* i18n:end */", 1)[0]
    return json.loads(block)


def _page() -> html.HtmlElement:
    return html.fromstring((STATIC / "index.html").read_text(encoding="utf-8"))


def test_english_and_spanish_have_the_same_keys() -> None:
    """N8: one dictionary, both languages complete; English is the default."""
    messages = _messages()
    assert set(messages) == {"en", "es"}
    assert set(messages["en"]) == set(messages["es"])
    assert all(value.strip() for lang in messages.values() for value in lang.values())


def test_every_static_text_key_exists_and_none_is_orphaned() -> None:
    """N8: each data-i18n key in the page is in the dictionary, and each entry is used,
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
    """N8: Rioplatense Spanish ("Pegá la URL"), not "Pega" or "usted"."""
    spanish = _messages()["es"]
    assert spanish["wizard.url.intro"].startswith("Pegá")
    assert not any(re.search(r"\busted\b", v, re.IGNORECASE) for v in spanish.values())


def test_every_tab_has_a_view_and_one_starts_selected() -> None:
    """N1: the shell's tabs each switch to an existing view; Results is first."""
    page = _page()
    tabs = page.xpath("//button[contains(@class, 'tab')]")
    views = [tab.get("data-view") for tab in tabs]
    assert views[0] == "results"
    for view in views:
        assert page.get_element_by_id(f"view-{view}") is not None
    assert [tab.get("aria-pressed") for tab in tabs].count("true") == 1


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
]  # fmt: skip


def test_light_and_dark_tokens_meet_aa_contrast() -> None:
    """N8 accessibility: every text/background token pair reaches 4.5:1 in both themes."""
    css = (STATIC / "studio.css").read_text(encoding="utf-8")
    themes = {"light": _tokens(css, ":root {"), "dark": _tokens(css, ':root[data-theme="dark"]')}
    failures = [
        f"{name}: {fg} on {bg} = {_contrast(tokens[fg], tokens[bg]):.2f}"
        for name, tokens in themes.items()
        for fg, bg in PAIRS
        if _contrast(tokens[fg], tokens[bg]) < 4.5
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
