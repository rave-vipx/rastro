"""Bounded, labelled page outline for agents and models (AGENT-002)."""

from rastro.application.page_outline import HEADER, outline_page


def test_outline_collapses_repeats_and_skips_scripts() -> None:
    html = (
        "<html><head><title>T</title><script>ignore()</script></head><body>"
        '<ul id="list"><li class="item a">One</li><li class="item a">Two</li><br></ul>'
        "<script>alert('x')</script><p>Tail</p></body></html>"
    )
    lines = outline_page(html).splitlines()
    assert lines[0] == HEADER
    assert "    ul#list" in lines
    assert '      li.item.a x2 "One"' in lines
    assert "ignore" not in "\n".join(lines) and "alert" not in "\n".join(lines)
    assert '    p "Tail"' in lines


def test_outline_tolerates_malformed_markup_and_clips_text() -> None:
    html = "<div><span>" + "x" * 500 + "</div></span></p><div>after</div>"
    text = outline_page(html)
    assert "div x2" in text
    assert max(len(line) for line in text.splitlines()) < 120


def test_outline_is_bounded_by_chars_and_nodes() -> None:
    html = "".join(f'<div class="c{n}">v</div>' for n in range(2000))
    assert len(outline_page(html, max_chars=500)) <= 500
    assert outline_page(html, max_nodes=10).endswith("(outline truncated)")
