"""Why a discovered link is not fetched, decided before a page slot is spent."""

from __future__ import annotations

from rastro.application.acquisition_state import PaginationScope, pagination_boundary
from rastro.application.destinations import permitted_url
from rastro.application.operator_trust import current_trust
from rastro.application.spider_links import Link, path_matches, pattern_matches
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy
from rastro.domain.spider import SpiderPolicy
from rastro.domain.spider_report import SpiderOutcome

_FILTERS: dict[str, SpiderOutcome] = {
    "invalid_next": "invalid_url",
    "off_host": "off_host",
    "https_downgrade": "https_downgrade",
}
_DESTINATION: dict[str, SpiderOutcome] = {
    "invalid_url": "invalid_url",
    "host_not_allowed": "off_host",
    "https_required": "https_downgrade",
    "port_not_allowed": "port_not_allowed",
}


def link_refusal(
    link: Link,
    scope: PaginationScope,
    spider: SpiderPolicy,
    policy: AcquisitionPolicy | None,
    *,
    paths: bool = True,  # False for a child sitemap: allow/deny are for pages
) -> SpiderOutcome | None:
    boundary = pagination_boundary(link.url, scope.host, scope.secure, scope.hosts)
    if boundary:
        return _FILTERS[boundary]
    refusal = _destination_refusal(link, policy)
    if refusal:
        return refusal
    if paths and (
        path_matches(link.path, spider.deny)
        or (spider.deny_pattern and pattern_matches(link.path, spider.deny_pattern))
        or (spider.allow and not path_matches(link.path, spider.allow))
        or (spider.allow_pattern and not pattern_matches(link.path, spider.allow_pattern))
    ):
        return "path_denied"
    return None


def _destination_refusal(link: Link, policy: AcquisitionPolicy | None) -> SpiderOutcome | None:
    """What the fetch would refuse anyway."""
    if policy is None:
        return None
    try:
        permitted_url(link.url, policy.with_grants(current_trust().network_grants()))
    except AcquisitionError as error:
        return _DESTINATION.get(error.code, "failed")
    return None
