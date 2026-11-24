"""Guided create beyond the first preview: a crawl estimate, the site's own data, exploration.

``estimate`` is offline: requests and minutes from the URL's policy (or a saved job), never
a fetch. ``offers`` renders the page once in a browser that captures JSON responses and
lists those that look like records ("this page loads its data from an API"), with the asked
fields a JSON recipe over each one would read. ``explore`` lets the configured model look
for page steps that reveal more records on a job the wizard just wrote; the narration comes
back with the result and nothing is written until ``explore/keep`` (digest-checked).
Page text and captured data are untrusted; a remote model needs ``allow_remote`` per call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.application.atomic_files import write_atomic
from rastro.application.browser_sessions import source_session
from rastro.application.crawl_estimate import estimate
from rastro.application.job_from_url import prepare_url_policy
from rastro.application.job_ladder_run import job_web_acquirer
from rastro.application.job_loader import load_job
from rastro.application.job_render import render_job
from rastro.application.navigation_explorer import ExplorationEvent, ExplorationResult
from rastro.application.navigation_job import explore_job
from rastro.application.network_recipes import (
    capture_offers,
    collecting,
    offer_policy,
    recipe_from_capture,
)
from rastro.application.operator_trust import operator_trust
from rastro.application.robots_cache import robots_cache
from rastro.domain.job_fields import StringField
from rastro.domain.jobs import Job, UrlJobSource
from rastro.domain.models import ConfigurationError, Limits, Outcome, Source
from rastro.domain.spider import SpiderPolicy
from rastro.ports.engine import CapturedResponse
from rastro.ports.model import ModelPort
from rastro.studio import files
from rastro.studio.web import Refused, Studio, body, flag, optional_text, reply, text

MAX_OFFERS = 5
MAX_FIELDS = 30
UNTRUSTED = "untrusted_source_data"


def _url_job(url: str, more: bool) -> Job:
    """A throwaway job for the estimate: the URL alone, or a bounded crawl from it."""
    canonical, policy = prepare_url_policy(url)
    spider = SpiderPolicy(mode="spider", links="a::attr(href)") if more else None
    return Job(
        format_version=1,
        sources=(UrlJobSource(url=canonical),),
        fields=(StringField(name="value"),),
        acquisition=policy,
        discover_recipes=True,
        spider=spider,
    )


def _captured(url: str, studio: Studio) -> tuple[CapturedResponse, ...]:
    """Render ``url`` once in a browser that keeps the JSON responses it loads (run only)."""
    from rastro.browser import browser_acquirer

    canonical, policy = prepare_url_policy(url)
    capturing = offer_policy(policy.model_copy(update={"render": "browser"}))
    acquirer = studio.acquisition or browser_acquirer(capturing)

    async def run() -> tuple[CapturedResponse, ...]:
        with collecting() as seen:
            async with source_session():
                page = await acquirer.acquire(
                    Source(url=canonical), max_bytes=Limits().max_html_bytes, representation="html"
                )
        return tuple(seen) if page.status == Outcome.SUCCESS else ()

    with robots_cache(), asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(run())


def _covers(response: CapturedResponse, fields: tuple[str, ...]) -> list[str]:
    from rastro.adapters.structured import JsonDiscovery

    if not fields:
        return []
    found = recipe_from_capture(response, fields, discovery=JsonDiscovery(), limits=Limits())
    return sorted(found[0].fields) if found else []


def _offers(responses: tuple[CapturedResponse, ...], fields: tuple[str, ...]) -> list[Any]:
    by_path = {(r.host, r.path): r for r in responses}
    ranked = sorted(capture_offers(responses), key=lambda o: o.records, reverse=True)
    return [
        {
            "host": offer.host,
            "path": offer.path,
            "records": offer.records,
            "record_path": offer.record_path,
            "covers": _covers(by_path[(offer.host, offer.path)], fields),
        }
        for offer in ranked[:MAX_OFFERS]
        if offer.records > 0
    ]


def _explore(job: Job, model: ModelPort, allow_remote: bool) -> tuple[Job, ExplorationResult]:
    """Explore the job's first page in a browser render (the steps replay only there)."""
    assert job.acquisition is not None
    rendered = job.model_copy(
        update={"acquisition": job.acquisition.model_copy(update={"render": "browser"})}
    )
    with operator_trust(allow_remote=allow_remote):
        acquisition = job_web_acquirer(rendered)
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            found = runner.run(
                explore_job(
                    rendered, model=model, acquisition=acquisition, allow_remote=allow_remote
                )
            )
    return rendered, found


def _explored_text(job: Job, found: ExplorationResult) -> str:
    dataset = job.dataset
    return render_job(
        list(job.fields),
        found.recipe,
        tuple(s for s in job.sources if isinstance(s, UrlJobSource)),
        policy=job.acquisition,
        dataset=dataset.path if dataset is not None else None,
        identity=tuple(dataset.identity) if dataset is not None else (),
        recipe_memory=job.recipe_memory,
    )


def _event(event: ExplorationEvent) -> dict[str, Any]:
    return {**event.model_dump(mode="json"), "message": event.message}


def field_names(data: dict[str, Any]) -> tuple[str, ...]:
    raw = data.get("fields", [])
    if not isinstance(raw, list) or len(raw) > MAX_FIELDS:
        raise Refused(400, "invalid_fields")
    return tuple(StringField(name=name).name for name in raw if isinstance(name, str))


def routes(studio: Studio) -> list[Route]:
    pending: dict[Path, tuple[str, str]] = {}  # job path -> (digest when explored, new text)

    async def estimated(request: Request) -> Response:
        data = await body(request)
        file = optional_text(data, "file")
        if file is not None:
            job = load_job(studio.job_path(file))
        else:
            job = _url_job(text(data, "url"), flag(data, "more"))
        found = estimate(job)
        return reply({"ok": True, **found.model_dump(mode="json")})

    async def offers(request: Request) -> Response:
        data = await body(request)
        url, fields = text(data, "url"), field_names(data)
        prepare_url_policy(url)  # a bad URL is the caller's error before any render
        try:
            responses = await run_in_threadpool(_captured, url, studio)
        except (ImportError, OSError, ValueError, ConfigurationError):
            return reply({"ok": True, "data_trust": UNTRUSTED, "offers": [], "browser": False})
        listed = await run_in_threadpool(_offers, responses, fields)
        return reply({"ok": True, "data_trust": UNTRUSTED, "offers": listed, "browser": True})

    async def explore(request: Request) -> Response:
        data = await body(request)
        path = studio.job_path(text(data, "file"))
        allow_remote = flag(data, "allow_remote")
        if studio.model is None:
            raise Refused(409, "no_model_configured")
        if studio.model_remote and not allow_remote:
            raise Refused(409, "remote_model_needs_consent")
        original = files.read_job_bytes(path)
        job = load_job(path)
        if job.acquisition is None:
            raise Refused(400, "needs_url_source")
        rendered, found = await run_in_threadpool(_explore, job, studio.model, allow_remote)
        kept = bool(found.recipe.steps) and found.records_after > found.records_before
        if kept:
            pending[path] = (files.digest(original), _explored_text(rendered, found))
        else:
            pending.pop(path, None)
        return reply(
            {
                "ok": True,
                "data_trust": UNTRUSTED,
                "events": [_event(e) for e in found.events],
                "before": found.records_before,
                "after": found.records_after,
                "model_calls": found.model_calls,
                "stop_reason": found.stop_reason,
                "steps": [step.model_dump(mode="json") for step in found.recipe.steps],
                "kept": kept,
            }
        )

    async def keep(request: Request) -> Response:
        path = studio.job_path(text(await body(request), "file"))
        if path not in pending:
            raise Refused(409, "nothing_explored")
        expected, new_text = pending.pop(path)
        write_atomic(path, new_text.encode("utf-8"), expected_digest=expected)
        return reply({"ok": True, "sha256": files.digest(files.read_job_bytes(path))})

    return [
        studio.route("/api/wizard/estimate", estimated, post=True),
        studio.route("/api/wizard/offers", offers, post=True),
        studio.route("/api/wizard/explore", explore, post=True),
        studio.route("/api/wizard/explore/keep", keep, post=True),
    ]
