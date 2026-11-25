"""The CrawlThrottle adapters: the engine's adaptive throttle and the stdlib fallback."""

from __future__ import annotations

import sys

import pytest

from rastro.adapters.scrapling_throttle import ScraplingThrottle, default_throttle
from rastro.application.adaptive_throttle import AdaptiveThrottle


def _engine_available() -> bool:
    try:
        import scrapling.spiders.throttle  # noqa: F401
    except ImportError:
        return False
    return True


ENGINE = pytest.param(
    ScraplingThrottle,
    marks=pytest.mark.skipif(not _engine_available(), reason="needs the browser extra"),
)
THROTTLES = [ENGINE, AdaptiveThrottle]


@pytest.mark.parametrize("make", THROTTLES)
def test_autothrottle_backoff_on_429(make):
    throttle = make()
    assert throttle.delay_for("shop.example", 1.0) == 1.0
    doubled = throttle.record("shop.example", latency=0.1, ok=False, retry_after=None)
    assert doubled == 2.0
    assert throttle.delay_for("shop.example", 1.0) == 2.0
    asked = throttle.record("shop.example", latency=0.1, ok=False, retry_after=7.0)
    assert asked == 7.0


@pytest.mark.parametrize("make", THROTTLES)
def test_the_delay_never_drops_below_the_floor_or_rises_past_the_cap(make):
    throttle = make(max_delay=10.0)
    assert throttle.delay_for("a.example", 0.5) == 0.5
    assert throttle.record("a.example", latency=0.01, ok=True, retry_after=None) == 0.5
    assert throttle.record("a.example", latency=0.01, ok=False, retry_after=3600.0) == 10.0
    assert throttle.delay_for("b.example", 0.0) == 0.0  # hosts are independent


@pytest.mark.parametrize("make", THROTTLES)
def test_a_slow_host_slows_the_pace(make):
    throttle = make()
    throttle.delay_for("slow.example", 0.0)
    assert throttle.record("slow.example", latency=4.0, ok=True, retry_after=None) == 4.0
    assert throttle.record("slow.example", latency=2.0, ok=True, retry_after=None) == 3.0


def test_without_the_engine_the_stdlib_throttle_paces(monkeypatch):
    monkeypatch.setitem(sys.modules, "scrapling.spiders.throttle", None)
    assert isinstance(default_throttle(), AdaptiveThrottle)
