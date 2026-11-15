"""Browser navigation behind the existing robots, pacing and destination policy."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import replace
from time import perf_counter
from urllib.parse import urlsplit

from rastro.application.acquisition import WebAcquirer
from rastro.application.acquisition_state import _Host, _Operation, check_page_request
from rastro.application.destinations import permitted_url, pin_destination
from rastro.application.operator_trust import current_trust
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    HttpResponse,
)
from rastro.domain.models import ConfigurationError, Outcome, Representation, Source
from rastro.ports.acquisition import HttpTransport, Resolver
from rastro.ports.browser import BrowserRenderer

_render_seconds: ContextVar[float] = ContextVar("render_seconds", default=0)


class BrowserAcquirer(WebAcquirer):
    def __init__(
        self,
        policy: AcquisitionPolicy,
        *,
        renderer: BrowserRenderer,
        transport: HttpTransport,
        resolver: Resolver,
        allow_stealth: bool = False,
    ):
        if (policy.render == "stealth" or policy.solve_challenges) and not (
            allow_stealth is True or current_trust().allow_stealth
        ):
            raise ConfigurationError(
                "Stealth requires --allow-stealth, allow_stealth=True or RASTRO_ALLOW_STEALTH=1"
            )
        if policy.egress_profiles:
            raise ConfigurationError("Browser modes do not support egress profiles")
        super().__init__(
            policy.model_copy(update={"max_retries": 0}), transport=transport, resolver=resolver
        )
        self._renderer = renderer
        self._http = WebAcquirer(policy, transport=transport, resolver=resolver)
        self._http._hosts = self._hosts

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation = "html"
    ) -> AcquisitionResult:
        if source.egress != "direct" or representation != "html":
            raise ConfigurationError("Browser modes require direct egress and HTML representation")
        token = _render_seconds.set(0)
        try:
            result = await super().acquire(
                source, max_bytes=max_bytes, representation=representation
            )
            return replace(result, render_seconds=_render_seconds.get())
        finally:
            _render_seconds.reset(token)

    async def _robots(self, url: str, operation: _Operation) -> None:
        await self._http._robots(url, operation)

    async def _pinned_get(
        self, url: str, host: _Host, operation: _Operation, *, max_bytes: int
    ) -> HttpResponse:
        async with self._slots:
            check_page_request(url)
            operation.requests += 1
            try:
                async with asyncio.timeout(self.policy.request_timeout_seconds):
                    target = await pin_destination(url, self.policy, self._resolver)
                    targets = [target]
                    for hostname in self.policy.allowed_hosts:
                        if hostname != target.host:
                            authority = f"[{hostname}]" if ":" in hostname else hostname
                            targets.append(
                                await pin_destination(
                                    f"https://{authority}/", self.policy, self._resolver
                                )
                            )
                    started = perf_counter()
                    try:
                        result = await self._renderer.render(
                            target,
                            policy=self.policy.model_copy(
                                update={
                                    "allow_http": self.policy.allow_http
                                    and urlsplit(url).scheme == "http",
                                    "max_requests": self.policy.max_requests
                                    - operation.requests
                                    + 1,
                                }
                            ),
                            max_bytes=max_bytes,
                            targets=tuple(targets),
                        )
                    finally:
                        _render_seconds.set(perf_counter() - started)
                    permitted_url(result.url, self.policy)
                    operation.url = result.url
                    if result.url != url:
                        await self._robots(result.url, operation)
                    if result.status != Outcome.SUCCESS:
                        raise AcquisitionError(
                            result.issue_code or "browser_failure", result.status
                        )
                    body = (result.body or "").encode("utf-8")
                    if len(body) > max_bytes:
                        raise AcquisitionError("response_too_large", Outcome.PERMANENT_FAILURE)
                    if not body:
                        raise AcquisitionError("empty_document", Outcome.INVALID_DATA)
                    operation.requests += max(0, result.requests - 1)
                    return HttpResponse(
                        result.http_status or 0,
                        {"content-type": "text/html; charset=utf-8"},
                        body,
                        len(body),
                    )
            finally:
                host.next_request = perf_counter() + host.interval
