"""Browser navigation behind the existing robots, pacing and destination policy."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import replace
from time import perf_counter

from rastro.application.acquisition import WebAcquirer
from rastro.application.acquisition_state import (
    PAGINATION_SCOPE,
    _Host,
    _Operation,
    charge_extra_requests,
    check_page_request,
)
from rastro.application.browser_page_steps import run_page_steps
from rastro.application.browser_policy import BROWSER_REGION, canonical_url, page_policy
from rastro.application.browser_proxy import BROWSER_PROXY
from rastro.application.browser_scroll_step import scroll_step
from rastro.application.browser_sessions import SOURCE_SESSIONS
from rastro.application.capability_gate import policy_capabilities, require
from rastro.application.destinations import permitted_url, pin_destination
from rastro.application.network_recipes import acquire_captured, record_captured
from rastro.application.operator_trust import current_trust
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
    HttpResponse,
    PinnedTarget,
)
from rastro.domain.models import ConfigurationError, Outcome, Representation, Source
from rastro.domain.page_steps import PageStep
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
        environ: Mapping[str, str] | None = None,
    ):
        # First, before the renderer is looked at: no grant, no stealth browser (ADR 0037).
        require(policy_capabilities(policy), allow_stealth=allow_stealth)
        if policy.auth:
            raise ConfigurationError("auth headers are HTTP-engine only")
        super().__init__(
            policy.model_copy(update={"max_retries": 0}),
            transport=transport,
            resolver=resolver,
            environ=environ,
        )
        self._renderer = renderer
        self._http = WebAcquirer(policy, transport=transport, resolver=resolver, environ=environ)
        self._http._hosts = self._hosts

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation = "html"
    ) -> AcquisitionResult:
        if representation == "json":  # the page's own JSON, captured while it renders
            return await acquire_captured(
                self.policy, lambda: self.acquire(source, max_bytes=max_bytes)
            )
        if representation != "html":
            raise ConfigurationError("Browser modes require the HTML or JSON representation")
        token = _render_seconds.set(0)
        region = BROWSER_REGION.set((source.locale, source.timezone))  # the job's [region]
        try:
            result = await super().acquire(
                source, max_bytes=max_bytes, representation=representation
            )
            return replace(result, render_seconds=_render_seconds.get(), render=self.policy.render)
        finally:
            BROWSER_REGION.reset(region)
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
                    operation.challenge_solved |= result.challenge_solved
                    if result.status != Outcome.SUCCESS:
                        raise AcquisitionError(
                            result.issue_code or "browser_failure", result.status
                        )
                    body = (result.body or "").encode("utf-8")
                    if len(body) > max_bytes:
                        raise AcquisitionError("response_too_large", Outcome.PERMANENT_FAILURE)
                    if not body:
                        raise AcquisitionError("empty_document", Outcome.INVALID_DATA)
                    return HttpResponse(
                        result.http_status or 0,
                        {"content-type": "text/html; charset=utf-8"},
                        body,
                        len(body),
                    )
            finally:
                host.next_request = max(host.next_request, perf_counter() + host.interval)

    async def _render(
        self,
        target: PinnedTarget,
        targets: tuple[PinnedTarget, ...],
        operation: _Operation,
        url: str,
        max_bytes: int,
    ) -> AcquisitionResult:
        """Render under what is left of the shared budget, charging what the browser did."""
        policy = page_policy(operation.policy, url)
        allowance = self.policy.max_requests - operation.requests + 1
        scope = PAGINATION_SCOPE.get()
        if scope is not None:  # the navigation is already charged: it stays inside the allowance
            allowance = min(allowance, scope.remaining + 1)
        policy = policy.model_copy(update={"max_requests": allowance})
        token = BROWSER_PROXY.set(operation.proxy)  # the target was pinned and checked above
        try:
            result = await self._render_once(target, targets, policy, max_bytes)
        except AcquisitionError as error:
            self._charge(operation, error.requests)
            raise
        except BaseException:  # a timeout or crash lost the browser's count: charge the allowance
            self._charge(operation, allowance)
            raise
        finally:
            BROWSER_PROXY.reset(token)
        self._charge(operation, result.requests)
        return result

    @staticmethod
    def _charge(operation: _Operation, requests: int) -> None:
        extra = max(0, requests - 1)  # the navigation itself was charged before the render
        operation.requests += extra
        charge_extra_requests(extra)

    async def _render_once(
        self,
        target: PinnedTarget,
        targets: tuple[PinnedTarget, ...],
        policy: AcquisitionPolicy,
        max_bytes: int,
    ) -> AcquisitionResult:
        """Render in the source's open context when there is one, else in a fresh one."""
        scope = SOURCE_SESSIONS.get()
        if scope is not None and isinstance(self._renderer, BrowserSessions):
            session = await scope.reuse(self._renderer, target, targets, policy)
            if session is not None:
                scope.mark_rendered(self._renderer, None)  # unknown until this render returns
                try:
                    result = await session.render(target, policy=policy, max_bytes=max_bytes)
                    record_captured(session)
                except BaseException:
                    await scope.discard(self._renderer)  # its final host is unknown: never reuse
                    raise
                if await scope.leave_foreign(self._renderer, result.url):
                    return result  # a redirect carried the context to another host: drop it
                if result.status == Outcome.SUCCESS:
                    scope.mark_rendered(self._renderer, canonical_url(result.url, policy))
                return result
        return await self._renderer.render(
            target, policy=policy, max_bytes=max_bytes, targets=targets
        )

    def renders_in_browser(self, url: str) -> bool:
        return True

    def may_render_in_browser(self, url: str) -> bool:
        return True

    def scrolls(self, url: str) -> bool:
        return self.policy.render != "stealth"  # Rastro does not drive scroll in a stealth page

    def scroll_skip_code(self, url: str) -> str:
        return "scroll_unsupported_stealth"

    async def scroll(self, source: Source, *, settle_ms: int, max_bytes: int) -> AcquisitionResult:
        """One scroll step in the source's open context, under the same budget and pacing."""
        return await scroll_step(
            self._renderer,
            self.policy,
            self._hosts,
            self._slots,
            source,
            settle_ms=settle_ms,
            max_bytes=max_bytes,
        )

    def runs_steps(self, url: str) -> bool:
        return True  # every render mode keeps the source's page open for its steps

    async def run_steps(
        self, source: Source, steps: tuple[PageStep, ...], *, max_bytes: int
    ) -> AcquisitionResult:
        """Run a recipe's page steps in the source's open page, under the same budget."""
        policy = self.policy.with_grants(current_trust().network_grants())

        async def robots(url: str) -> None:
            await self._robots(url, _Operation(url, policy))

        return await run_page_steps(
            self._renderer,
            self.policy,
            self._hosts,
            self._slots,
            source,
            steps,
            max_bytes=max_bytes,
            robots=robots,
        )
