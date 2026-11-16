"""Counting matched elements stays linear in the number of matches (security audit H2).

The count used to compute an XPath per match (``getpath``), which walks siblings, so 200 000
sibling ``<p>`` elements took quadratic time.
"""

import gc
import statistics
import time

from rastro.adapters.scrapling import _parse_html, element_count

SMALL, LARGE = 10_000, 160_000  # 16x the matches: linear is ~16x, quadratic ~256x
RUNS = 5
# Linear stays near 16x; 3x headroom absorbs a noisy CI runner, quadratic (256x) still fails.
MAX_RATIO = 16 * 3
FLOOR = 0.002  # seconds; keeps a timer-resolution small run from inflating the ratio


def _median_seconds(count: int) -> float:
    page = _parse_html("<html><body>" + "<p>z</p>" * count + "</body></html>")
    samples = []
    for _ in range(RUNS):
        gc.collect()
        gc.disable()  # a collection pause in one run must not skew the ratio
        try:
            started = time.perf_counter()
            assert element_count(page, "p::text") == count
            samples.append(time.perf_counter() - started)
        finally:
            gc.enable()
    return statistics.median(samples)


def test_many_sibling_matches_count_in_linear_time() -> None:
    small, large = _median_seconds(SMALL), _median_seconds(LARGE)
    assert large < 5
    assert large < max(small, FLOOR) * MAX_RATIO


def test_selector_list_matching_one_element_twice_counts_once() -> None:
    page = _parse_html("<div><p class='a'>x</p><p>y</p></div>")
    assert element_count(page, "p.a::text, p::text") == 2
    assert element_count(page, "::text") == 1
