"""The CrawlThrottle adapter over the engine's adaptive throttle."""

from __future__ import annotations

from rastro.adapters.scrapling_throttle import ScraplingThrottle


def test_autothrottle_backoff_on_429():
    throttle = ScraplingThrottle()
    assert throttle.delay_for("shop.example", 1.0) == 1.0
    doubled = throttle.record("shop.example", latency=0.1, ok=False, retry_after=None)
    assert doubled == 2.0
    assert throttle.delay_for("shop.example", 1.0) == 2.0
    asked = throttle.record("shop.example", latency=0.1, ok=False, retry_after=7.0)
    assert asked == 7.0


def test_the_delay_never_drops_below_the_floor_or_rises_past_the_cap():
    throttle = ScraplingThrottle(max_delay=10.0)
    assert throttle.delay_for("a.example", 0.5) == 0.5
    assert throttle.record("a.example", latency=0.01, ok=True, retry_after=None) == 0.5
    assert throttle.record("a.example", latency=0.01, ok=False, retry_after=3600.0) == 10.0
    assert throttle.delay_for("b.example", 0.0) == 0.0  # hosts are independent
