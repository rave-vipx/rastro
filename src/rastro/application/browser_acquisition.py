"""Browser navigation behind the existing robots, pacing and destination policy."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import replace
from time import perf_counter
from urllib.parse import urlsplit

from rastro.application.acquisition import WebAcquirer
from rastro.application.acquisition_state import (
    PAGINATION_SCOPE,
    _Host,
    _Operation,
    check_page_request,
)
from rastro.application.browser_sessions import SOURCE_SESSIONS
from rastro.application.destinations import permitted_url, pin_destination
from rastro.application.operator_trust import current_trust
from rastro.application.stealth_grant import require_stealth_grant
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    HttpResponse,
    PinnedTarget,
)
from rastro.domain.models import ConfigurationError, Outcome, Representation, Source
from rastro.ports.acquisition import HttpTransport, Resolver
from rastro.ports.browser import BrowserRenderer, BrowserSessions

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
        # First, before the renderer is looked at: no grant, no stealth browser (ADR 0034).
        require_stealth_grant(
            stealth=policy.render == "stealth",
            solve_challenges=policy.solve_challenges,
            allow=allow_stealth,
        )
        if policy.egress_profiles:
            raise ConfigurationError("Browser modes do not support egress profiles")
        if policy.auth:
            raise ConfigurationError("auth headers are HTTP-engine only")
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
                    target = await pin_destination(url, operation.policy, self._resolver)
                    targets = [target]
                    for hostname in self.policy.allowed_hosts:
                        if hostname != target.host:
                            authority = f"[{hostname}]" if ":" in hostname else hostname
                            targets.append(
                                await pin_destination(
                                    f"https://{authority}/", operation.policy, self._resolver
                                )
                            )
                    started = perf_counter()
                    try:
                        result = await self._render(
                            target, tuple(targets), operation, url, max_bytes
                        )
                    finally:
                        _render_seconds.set(perf_counter() - started)
                    permitted_url(result.url, operation.policy)
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

    async def _render(
        self,
        target: PinnedTarget,
        targets: tuple[PinnedTarget, ...],
        operation: _Operation,
        url: str,
        max_bytes: int,
    ) -> AcquisitionResult:
        """Render in the source's open context when there is one, else in a fresh one."""
        policy = operation.policy.model_copy(
            update={
                "allow_http": operation.policy.allow_http and urlsplit(url).scheme == "http",
                "max_requests": self.policy.max_requests - operation.requests + 1,
            }
        )
        scope = SOURCE_SESSIONS.get()
        if scope is not None and isinstance(self._renderer, BrowserSessions):
            session = await scope.reuse(self._renderer, target, targets, policy)
            if session is not None:
                return await session.render(target, policy=policy, max_bytes=max_bytes)
        return await self._renderer.render(
            target, policy=policy, max_bytes=max_bytes, targets=targets
        )

    def renders_in_browser(self, url: str) -> bool:
        return True

    def scrolls(self, url: str) -> bool:
        return self.policy.render != "stealth"  # Rastro does not drive scroll in a stealth page

    def scroll_skip_code(self, url: str) -> str:
        return "scroll_unsupported_stealth"

    async def scroll(self, source: Source, *, settle_ms: int, max_bytes: int) -> AcquisitionResult:
        """One scroll step in the source's open context, under the same budget and pacing."""
        scope = SOURCE_SESSIONS.get()
        session = scope.current(self._renderer) if scope is not None else None
        if session is None:
            raise ConfigurationError("Scroll needs a browser renderer that keeps one session")
        policy = self.policy.with_grants(current_trust().network_grants())
        started = perf_counter()
        try:
            _, hostname, _ = permitted_url(source.url, policy)
            host = self._hosts[hostname]
            async with host.lock, self._slots:
                await asyncio.sleep(max(0, host.next_request - perf_counter()))
                check_page_request(source.url)
                budget = PAGINATION_SCOPE.get()
                step = policy.model_copy(
                    update={"max_requests": budget.remaining + 1 if budget else policy.max_requests}
                )
                try:
                    async with asyncio.timeout(
                        self.policy.request_timeout_seconds + settle_ms / 1000
                    ):
                        result = await session.scroll(
                            policy=step, max_bytes=max_bytes, settle_ms=settle_ms
                        )
                finally:
                    host.next_request = perf_counter() + host.interval
            permitted_url(result.url, policy)
            if result.status != Outcome.SUCCESS or not result.body:
                raise AcquisitionError(
                    result.issue_code or "empty_document",
                    result.status if result.status != Outcome.SUCCESS else Outcome.INVALID_DATA,
                )
            if len(result.body.encode("utf-8")) > max_bytes:
                raise AcquisitionError("response_too_large", Outcome.PERMANENT_FAILURE)
            return replace(result, acquisition_seconds=perf_counter() - started)
        except TimeoutError:
            return _scroll_failure(source, Outcome.TRANSIENT_FAILURE, "operation_timeout")
        except AcquisitionError as error:
            return _scroll_failure(source, error.outcome, error.code)


def _scroll_failure(source: Source, status: Outcome, code: str) -> AcquisitionResult:
    return AcquisitionResult(source.url, None, status, code, egress=source.egress)
