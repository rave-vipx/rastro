"""Dry-run crawl estimate of requests and duration without network fetches."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.jobs import Job, UrlJobSource
from rastro.domain.sources import Contract

__all__ = ["CrawlEstimate", "estimate"]

_DEFAULT_MAX_REQUESTS = 30
_DEFAULT_INTERVAL = 1.0
_DEFAULT_CONCURRENCY = 4
_DEFAULT_REQUEST_TIMEOUT = 15.0
_DEFAULT_OPERATION_TIMEOUT = 60.0
_MIN_TRANSFER_SECONDS = 0.05
_MAX_TRANSFER_SECONDS = 1.0


class CrawlEstimate(Contract):
    """Upper-bound request count and estimated run duration in minutes, computed offline."""

    requests_max: int
    minutes_min: float
    minutes_max: float


def estimate(job: Job) -> CrawlEstimate:
    """Compute a dry-run estimate of requests and duration from caps and configured rates.

    Never performs network I/O or touches the filesystem.
    """
    policy = job.acquisition
    url_sources = [source for source in job.sources if isinstance(source, UrlJobSource)]
    if not url_sources:
        return CrawlEstimate(requests_max=0, minutes_min=0.0, minutes_max=0.0)

    per_source_caps: list[tuple[str, int]] = []
    for source in url_sources:
        host = (urlsplit(source.url).hostname or "").lower().rstrip(".")
        cap = _source_request_cap(job, source, policy)
        per_source_caps.append((host, cap))

    requests_max = sum(cap for _, cap in per_source_caps)
    if requests_max <= 0:
        return CrawlEstimate(requests_max=0, minutes_min=0.0, minutes_max=0.0)

    by_host: dict[str, int] = {}
    for host, cap in per_source_caps:
        by_host[host] = by_host.get(host, 0) + cap

    global_concurrency = policy.max_concurrency if policy is not None else _DEFAULT_CONCURRENCY
    req_timeout = policy.request_timeout_seconds if policy is not None else _DEFAULT_REQUEST_TIMEOUT
    op_timeout = (
        policy.operation_timeout_seconds if policy is not None else _DEFAULT_OPERATION_TIMEOUT
    )
    retries = policy.max_retries if policy is not None else 0
    backoff = policy.backoff_seconds if policy is not None else 0.0

    host_min_seconds: list[float] = []
    host_max_seconds: list[float] = []
    for host, count in by_host.items():
        interval = _host_interval(policy, host)
        pacing = max(0, count - 1) * interval
        min_sec = pacing + count * _MIN_TRANSFER_SECONDS
        per_req_max = min(req_timeout, _MAX_TRANSFER_SECONDS + retries * max(backoff, 0.25))
        max_sec = pacing + count * max(interval, per_req_max)
        if job.spider is not None and job.spider.mode == "spider":
            budget = getattr(job.spider, "time_budget_seconds", None)
            if isinstance(budget, int | float) and budget > 0:
                max_sec = min(max_sec, float(budget) * len(url_sources))
        else:
            max_sec = min(max_sec, op_timeout * len(url_sources))
        host_min_seconds.append(min_sec)
        host_max_seconds.append(max(min_sec, max_sec))

    # Requests to distinct hosts can run in parallel up to global max_concurrency;
    # requests to the same host are serialized by the host's pacing lock.
    parallel_min = sum(host_min_seconds) / max(1, min(global_concurrency, len(by_host)))
    total_min_seconds = max(max(host_min_seconds), parallel_min)
    total_max_seconds = max(total_min_seconds, sum(host_max_seconds))

    minutes_min = round(total_min_seconds / 60.0, 4)
    minutes_max = round(max(minutes_min, total_max_seconds / 60.0), 4)
    return CrawlEstimate(
        requests_max=requests_max,
        minutes_min=minutes_min,
        minutes_max=minutes_max,
    )


def _source_request_cap(job: Job, source: UrlJobSource, policy: AcquisitionPolicy | None) -> int:
    budget = policy.max_requests if policy is not None else _DEFAULT_MAX_REQUESTS
    spider = job.spider
    if spider is not None and spider.mode == "spider":
        max_pages = spider.max_pages
        if isinstance(max_pages, int):
            return min(budget, max_pages)
        return budget
    recipe = source.recipe or job.recipe
    pages = recipe.pagination.max_pages if recipe and recipe.pagination else 1
    scroll_steps = job.scroll.max_steps if job.scroll is not None else 0
    return min(budget, pages * (1 + scroll_steps))


def _host_interval(policy: AcquisitionPolicy | None, host: str) -> float:
    if policy is None:
        return _DEFAULT_INTERVAL
    hosts: Any = getattr(policy, "hosts", None)
    if isinstance(hosts, dict) and host in hosts:
        limit = hosts[host]
        override = getattr(limit, "min_interval_seconds", None)
        if isinstance(override, int | float):
            return float(override)
    return float(policy.min_interval_seconds)
