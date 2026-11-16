"""Goal-driven candidate source proposals and approved-host acquisition (DISC-001..002)."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
from collections.abc import Sequence
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import ValidationError

from rastro.application.destinations import permitted_url, public_address
from rastro.application.model_choice import ModelChoice, resolve_model
from rastro.application.robots_cache import robots_cache
from rastro.domain.acquisition import (
    AcquisitionError,
    AcquisitionPolicy,
    AcquisitionResult,
)
from rastro.domain.acquisition_base import exact_host
from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest
from rastro.domain.json import strict_json
from rastro.domain.models import ConfigurationError, Contract, Limits, Source
from rastro.domain.page_outline import clip_untrusted
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.model import ModelPort
from rastro.ports.search import SearchHit, SearchProvider

MAX_SOURCE_PROPOSALS = 10
MAX_PAGES_PER_HOST = 3
SEARXNG_ENV = "RASTRO_SEARXNG_URL"
type SourceOrigin = Literal["search", "model"]
_SYSTEM = (
    "Propose up to 10 public HTTP(S) web page URLs relevant to the user's extraction goal. "
    'Return only JSON matching {"sources": [{"url": "https://...", "title": "..."}]}.'
)
_ITEM = {"type": "object", "properties": {"url": {"type": "string"}, "title": {"type": "string"}}}
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"sources": {"type": "array", "items": {**_ITEM, "required": ["url"]}}},
    "required": ["sources"],
}


class SourceProposal(Contract):
    """One proposed source URL and its exact host; never auto-approved."""

    url: str
    host: str
    title: str = ""
    origin: SourceOrigin


def clean_proposal(raw_url: str, raw_title: str, origin: SourceOrigin) -> SourceProposal | None:
    if not raw_url or any(ord(ch) <= 32 or ord(ch) == 127 for ch in raw_url):
        return None
    try:
        clean_url = Source(url=raw_url).evidence_url
        parts = urlsplit(clean_url)
        if parts.port not in (None, 443 if parts.scheme == "https" else 80):
            return None
        host = exact_host(parts.hostname or "")
    except (ValidationError, ValueError):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host == "localhost" or host.endswith(".invalid"):
            return None
    else:
        try:
            public_address(host)
        except AcquisitionError:
            return None
    return SourceProposal(
        url=clean_url, host=host, title=clip_untrusted(raw_title, 200), origin=origin
    )


def _dedupe(
    items: list[tuple[str, str]], origin: SourceOrigin, limit: int
) -> tuple[SourceProposal, ...]:
    cap, seen, kept = min(limit, MAX_SOURCE_PROPOSALS), set[str](), list[SourceProposal]()
    for raw_url, raw_title in items[:64]:
        if (p := clean_proposal(raw_url.strip(), raw_title, origin)) and p.url not in seen:
            seen.add(p.url)
            kept.append(p)
            if len(kept) >= cap:
                break
    return tuple(kept)


async def _propose_from_model(
    goal: str, model: ModelPort, policy: InferencePolicy, limit: int
) -> tuple[SourceProposal, ...]:
    cap = min(limit, MAX_SOURCE_PROPOSALS)
    body = json.dumps({"goal": clip_untrusted(goal, 500), "limit": cap}, ensure_ascii=False)
    req = ModelRequest(system=_SYSTEM, content=body, response_schema=_SCHEMA)
    try:
        async with asyncio.timeout(policy.timeout_seconds):
            out = await model.generate(req, policy)
        if len(out.encode("utf-8")) > policy.max_response_bytes:
            raise ModelError("model_response_limit")
        parsed = strict_json(out)
    except (TimeoutError, ModelError, ValueError, RecursionError):
        raise ConfigurationError("Model failed to return valid JSON source proposals") from None
    raw = parsed.get("sources", parsed.get("urls")) if isinstance(parsed, dict) else parsed
    if not isinstance(raw, list):
        raise ConfigurationError("Model source proposals must contain a 'sources' list")
    pairs = [
        (e, "")
        if isinstance(e, str)
        else (e["url"], e["title"] if isinstance(e.get("title"), str) else "")
        for e in raw[:64]
        if isinstance(e, str) or (isinstance(e, dict) and isinstance(e.get("url"), str))
    ]
    return _dedupe(pairs, "model", cap)


def _model_port(model: ModelChoice, allow_remote: bool) -> ModelPort | None:
    env_model = "env" if os.environ.get("RASTRO_MODEL", "").strip() else None
    return resolve_model(model or env_model, allow_remote=allow_remote)


def _resolve_backend(
    search: SearchProvider | Literal["searxng", "model"] | None,
    provider: SearchProvider | None,
    model: ModelChoice,
    allow_remote: bool,
) -> SearchProvider | ModelPort:
    if isinstance(search, SearchProvider):
        return search
    if search == "model":
        if (port := _model_port(model, allow_remote)) is None:
            raise ConfigurationError(
                "No model for --search model: pass --model or set RASTRO_MODEL"
            )
        return port
    if provider is not None:
        return provider
    if search == "searxng" or os.environ.get(SEARXNG_ENV, "").strip():
        from rastro.adapters.searxng import SearXNGSearchProvider

        return SearXNGSearchProvider(allow_remote=allow_remote)
    if (port := _model_port(model, allow_remote)) is not None:
        return port
    raise ConfigurationError(
        f"No search provider or model configured: set {SEARXNG_ENV} or pass --search model"
    )


async def propose_sources_async(goal: str, **opts: Any) -> tuple[SourceProposal, ...]:
    cleaned, limit = goal.strip(), int(opts.get("limit", MAX_SOURCE_PROPOSALS))
    if not cleaned or len(cleaned) > 500 or not 1 <= limit <= 50:
        raise ConfigurationError("Goal text must be 1..500 chars and limit 1..50")
    cap = min(limit, MAX_SOURCE_PROPOSALS)
    backend = _resolve_backend(
        opts.get("search"),
        opts.get("provider"),
        opts.get("model"),
        bool(opts.get("allow_remote", False)),
    )
    if isinstance(backend, SearchProvider):
        hits: tuple[SearchHit, ...] = await backend.search(cleaned, cap)
        return _dedupe([(h.url, h.title) for h in hits], "search", cap)
    return await _propose_from_model(cleaned, backend, opts.get("policy") or InferencePolicy(), cap)


def propose_sources(goal: str, **kwargs: Any) -> tuple[SourceProposal, ...]:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(propose_sources_async(goal, **kwargs))


def default_acquirer(policy: AcquisitionPolicy) -> AcquisitionPort:
    try:
        from rastro.adapters.dns import SystemResolver
        from rastro.adapters.http import CurlTransport
        from rastro.application.acquisition import WebAcquirer
    except ModuleNotFoundError as err:
        if err.name not in {"curl_cffi", "protego"}:
            raise
        raise ConfigurationError("Install rastro-engine[http] for HTTP acquisition") from None
    return WebAcquirer(policy, transport=CurlTransport(), resolver=SystemResolver())


def acquire_approved_pages(
    urls: Sequence[str],
    approved_hosts: Sequence[str],
    *,
    acquisition: AcquisitionPort | None = None,
    limits: Limits | None = None,
) -> tuple[tuple[str, ...], AcquisitionPolicy, tuple[AcquisitionResult, ...]]:
    try:
        hosts = tuple(dict.fromkeys(exact_host(h.strip()) for h in approved_hosts if h.strip()))
    except ValueError:
        raise ConfigurationError("Approved hosts must be exact hostnames or IP addresses") from None
    if not hosts:
        raise ConfigurationError("At least one approved host is required")
    per_host: dict[str, list[str]] = {h: [] for h in hosts}
    kept: list[str] = []
    for raw in urls:
        if (p := clean_proposal(raw, "", "search")) and p.host in per_host:
            bucket = per_host[p.host]
            if p.url not in bucket and len(bucket) < MAX_PAGES_PER_HOST:
                bucket.append(p.url)
                kept.append(p.url)
    allow_http = any(urlsplit(u).scheme == "http" for u in kept)
    policy = AcquisitionPolicy(allowed_hosts=hosts, allow_http=allow_http)
    if not kept:
        return hosts, policy, ()
    acquirer, max_b = acquisition or default_acquirer(policy), (limits or Limits()).max_html_bytes

    async def _fetch_all() -> tuple[AcquisitionResult, ...]:
        for u in kept:
            permitted_url(u, policy)
        return tuple(
            [
                await acquirer.acquire(Source(url=u), max_bytes=max_b, representation="html")
                for u in kept
            ]
        )

    with robots_cache(), asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return hosts, policy, runner.run(_fetch_all())
