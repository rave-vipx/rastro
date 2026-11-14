"""Counting matched elements stays linear in the number of matches (security audit H2).

The count used to compute an XPath per match (``getpath``), which walks siblings, so 200 000
sibling ``<p>`` elements took quadratic time.
"""

import time

from rastro.adapters.scrapling import _parse_html, element_count


def _seconds(count: int) -> float:
    page = _parse_html("<html><body>" + "<p>z</p>" * count + "</body></html>")
    started = time.perf_counter()
    assert element_count(page, "p::text") == count
    return time.perf_counter() - started


def test_many_sibling_matches_count_in_linear_time() -> None:
    small = _seconds(50_000)
    large = _seconds(200_000)
    assert large < 5
    # 4x the matches: linear is ~4x, the old quadratic count was ~16x.
    assert large < max(small, 0.05) * 9


def test_selector_list_matching_one_element_twice_counts_once() -> None:
    page = _parse_html("<div><p class='a'>x</p><p>y</p></div>")
    assert element_count(page, "p.a::text, p::text") == 2
    assert element_count(page, "::text") == 1
