"""Preflight untrusted robots directives before Protego expands or logs them."""

import math
import re
from urllib.parse import urlsplit

from protego import Protego

from rastro.domain.acquisition import AcquisitionError, HttpResponse
from rastro.domain.models import Outcome


def parse_robots(body: bytes) -> Protego:
    try:
        text = body.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise AcquisitionError("invalid_robots", Outcome.ACCESS_RESTRICTION) from None
    directives: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        if len(line) > 2048 or len(directives) >= 2000:
            raise AcquisitionError("robots_limit", Outcome.ACCESS_RESTRICTION)
        key, separator, value = line.partition(":")
        if not separator:
            raise AcquisitionError("invalid_robots", Outcome.ACCESS_RESTRICTION)
        directives.append((key.strip().lower().replace(" ", "-"), value.strip()))
    agents = sum(key in {"user-agent", "useragent"} for key, _ in directives)
    # Protego duplicates rules across agents, wildcard aliases and anchored paths.
    # Deliberately overestimate expansion; reject before allocating parser objects.
    factor = 8 * max(1, agents)
    if factor * max(1, len(directives) - agents) > 10_000 or factor * len(text) > 4_000_000:
        raise AcquisitionError("robots_limit", Outcome.ACCESS_RESTRICTION)
    for key, value in directives:
        if not value:
            continue
        if key == "crawl-delay":
            try:
                delay = float(value)
            except ValueError:
                raise AcquisitionError("invalid_robots", Outcome.ACCESS_RESTRICTION) from None
            if not math.isfinite(delay) or delay < 0:
                raise AcquisitionError("invalid_robots", Outcome.ACCESS_RESTRICTION)
        elif key == "request-rate":
            rate = re.fullmatch(r"([0-9]{1,16})/([0-9]{1,16})([smhd]?)", value.lower())
            if rate is None or int(rate[1]) == 0 or int(rate[2]) == 0:
                raise AcquisitionError("invalid_robots", Outcome.ACCESS_RESTRICTION)
        elif key in {"allow", "disallow"}:
            # Protego 0.7 tolerates unparsable rule URLs; Rastro must not ignore a rule.
            try:
                urlsplit(value)
            except ValueError:
                raise AcquisitionError("invalid_robots", Outcome.ACCESS_RESTRICTION) from None
        elif key == "visit-time":
            # Time-window scheduling is outside this milestone; never ignore a restriction.
            raise AcquisitionError("unsupported_robots_rule", Outcome.ACCESS_RESTRICTION)
    try:
        return Protego.parse(text)
    except ValueError:
        raise AcquisitionError("invalid_robots", Outcome.ACCESS_RESTRICTION) from None


def parse_robots_response(response: HttpResponse) -> tuple[Protego, float]:
    """Validate a 200 robots.txt response and return its parsed rules and min interval."""
    if "html" in response.headers.get("content-type", "").lower():
        raise AcquisitionError("invalid_robots", Outcome.ACCESS_RESTRICTION)
    rules = parse_robots(response.body)
    delay = rules.crawl_delay("Rastro") or 0
    rate = rules.request_rate("Rastro")
    # Conservatively space requests by the whole window, avoiding bursts.
    return rules, max(delay, rate.seconds if rate else 0)
