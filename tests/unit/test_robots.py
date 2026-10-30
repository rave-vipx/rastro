import logging

import pytest

from rastro.application.robots import parse_robots
from rastro.domain.acquisition import AcquisitionError


@pytest.mark.parametrize(
    "body",
    [
        b"# comment\n\nUser-agent: *\nDisallow: \nAllow: /",  # comments and empty directives
        b"User-agent: *\nCrawl-delay: .01\nRequest-rate: 2/1m",  # supported extensions
        b"Useragent: Rastro\nDisallow: /no",  # recognized agent alias
    ],
)
def test_bounded_valid_robots(body):
    assert parse_robots(body).can_fetch("https://example.com/yes", "Rastro")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"User-agent *", "invalid_robots"),
        (b"User-agent: *\nDisallow: HTTPS://[", "invalid_robots"),
        (b"User-agent: *\nAllow: x://[", "invalid_robots"),
        (b"User-agent: *\nDisallow: /" + b"x" * 2048, "robots_limit"),
        (b"Sitemap: /a\n" * 2001, "robots_limit"),
        (b"User-agent: *\nCrawl delay: secret-token", "invalid_robots"),
        (b"User-agent: *\nRequest rate: invalid-secret", "invalid_robots"),
        (b"User-agent: *\nRequest-rate: 1/10 1200-1400", "invalid_robots"),
        (b"User-agent: *\nVisit time: secret-token", "unsupported_robots_rule"),
    ],
    ids=[
        "missing-colon",
        "invalid-disallow-url",
        "invalid-allow-url",
        "long-line",
        "too-many-lines",
        "invalid-crawl-delay",
        "invalid-request-rate",
        "timed-request-rate",
        "visit-time",
    ],
)
def test_untrusted_directives_fail_without_dependency_log_disclosure(body, code, caplog):
    with caplog.at_level(logging.DEBUG), pytest.raises(AcquisitionError, match=code):
        parse_robots(body)
    assert "secret" not in caplog.text
