"""Search and install from a registry URL (HUB-002..HUB-006, SEC-001, SEC-005, SEC-008).

The bytes come through ``WebAcquirer`` (https only, public addresses unless the operator
granted otherwise, DNS pinning, every redirect re-checked, robots enforced). They are written
to a throw-away directory and go through the same gates as a local registry: digests, selector
policy, offline replay, then a candidate. Any failure leaves the recipe store untouched.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Coroutine
from datetime import datetime
from pathlib import Path
from typing import Any

from rastro.application.acquisition import WebAcquirer
from rastro.application.bundle_files import load_json, write_all
from rastro.application.recipe_hub import ParserFor
from rastro.application.registry_install import InstalledPackage, install_package_dir
from rastro.application.registry_store import parse_index
from rastro.application.registry_url import RegistryLocation, parse_registry_url, registry_policy
from rastro.application.robots_cache import robots_cache
from rastro.domain.models import ConfigurationError, Limits, Outcome, Representation, Source
from rastro.domain.recipe_bundle import (
    EXPECTED_FILE,
    MANIFEST_FILE,
    MAX_METADATA_BYTES,
    RECIPE_FILE,
    SAMPLE_FILES,
    SCHEMA_FILE,
    BundleIntegrityError,
)
from rastro.domain.registry import (
    INDEX_FILE,
    RECIPES_DIR,
    RegistryEntry,
    RegistryError,
    matches_query,
    parse_package_spec,
    select_entry,
    semver_key,
)
from rastro.ports.acquisition import HttpTransport, Resolver
from rastro.ports.recipes import RecipeRepository
from rastro.ports.registry_fetch import RegistryFetcher

MAX_PACKAGE_FILES = 6  # index.json plus bundle.json, recipe, schema, expected and the sample

_HINTS = {
    "robots_denied": "The registry host's robots.txt disallows Rastro; the operator may pass "
    "--ignore-robots HOST for a host they have permission to use",
    "port_not_allowed": "The operator may pass --allow-port N for a registry on another port",
    "destination_denied": "Only public addresses are reachable; the operator of a private "
    "registry may pass --allow-private-network",
}


class RegistryFetchError(RegistryError):
    """A registry file could not be fetched; ``code`` is the short acquisition category."""

    def __init__(self, code: str) -> None:
        super().__init__(f"Registry fetch failed: {code}", hint=_HINTS.get(code))
        self.code = code


class WebRegistryFetcher:
    """``RegistryFetcher`` over a policy-bound ``WebAcquirer``; errors carry a category only."""

    def __init__(self, acquirer: WebAcquirer) -> None:
        self._acquirer = acquirer

    async def fetch(self, url: str, *, max_bytes: int) -> bytes:
        result = await self._acquirer.acquire_raw(Source(url=url), max_bytes=max_bytes)
        if result.status != Outcome.SUCCESS or result.raw_body is None:
            raise RegistryFetchError(result.issue_code or str(result.status))
        return result.raw_body


class FetchBudget:
    """At most ``files`` files and ``total`` bytes for one search or install."""

    def __init__(self, total: int, files: int = MAX_PACKAGE_FILES) -> None:
        self.files = 0
        self.max_files = files
        self.remaining = total

    async def get(self, fetcher: RegistryFetcher, url: str, cap: int) -> bytes:
        if self.files >= self.max_files or self.remaining <= 0:
            raise RegistryError("Registry download budget exceeded; nothing was installed")
        self.files += 1
        limit = min(cap, self.remaining)
        data = await fetcher.fetch(url, max_bytes=limit)
        if len(data) > limit:  # a custom transport may ignore max_bytes
            raise RegistryError("Registry fetch failed: body_too_large")
        self.remaining -= len(data)
        return data

    async def get_optional(self, fetcher: RegistryFetcher, url: str, cap: int) -> bytes | None:
        """``None`` when the host answers that the file is not there (an HTTP 4xx); every
        other refusal (address, port, robots, size) still raises."""
        try:
            return await self.get(fetcher, url, cap)
        except RegistryFetchError as error:
            if error.code == "http_permanent_failure":
                return None
            raise


def registry_fetcher(
    location: RegistryLocation, transport: HttpTransport | None, resolver: Resolver | None
) -> RegistryFetcher:
    if transport is None or resolver is None:
        try:
            from rastro.adapters.dns import SystemResolver
            from rastro.adapters.http import CurlTransport
        except ModuleNotFoundError as error:
            if error.name not in {"curl_cffi", "protego"}:
                raise
            raise ConfigurationError("Install rastro-engine[http] to use --registry-url") from None
        transport, resolver = transport or CurlTransport(), resolver or SystemResolver()
    acquirer = WebAcquirer(registry_policy(location), transport=transport, resolver=resolver)
    return WebRegistryFetcher(acquirer)


def run_fetch[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """One search or install shares one robots.txt answer per origin."""
    with robots_cache(), asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coroutine)


def _budget(limits: Limits) -> FetchBudget:
    return FetchBudget(MAX_METADATA_BYTES * 5 + max(limits.max_html_bytes, limits.max_json_bytes))


async def fetch_index(
    location: RegistryLocation, fetcher: RegistryFetcher, budget: FetchBudget
) -> tuple[RegistryEntry, ...]:
    raw = await budget.get(fetcher, location.base + INDEX_FILE, MAX_METADATA_BYTES)
    return parse_index(raw)


def search_registry_url(
    url: str,
    query: str,
    *,
    limits: Limits | None = None,
    transport: HttpTransport | None = None,
    resolver: Resolver | None = None,
) -> tuple[RegistryEntry, ...]:
    """Entries of the registry at ``url`` matching ``query`` (same rules as a local search)."""
    location = parse_registry_url(url)
    budget = _budget(limits or Limits())
    entries = run_fetch(
        fetch_index(location, registry_fetcher(location, transport, resolver), budget)
    )
    matched = [entry for entry in entries if matches_query(entry, query)]
    return tuple(sorted(matched, key=lambda item: (item.id, semver_key(item.version))))


def _representation(manifest: bytes) -> Representation:
    raw = load_json(manifest)
    for representation in SAMPLE_FILES:
        if isinstance(raw, dict) and raw.get("representation") == representation:
            return representation
    raise BundleIntegrityError("Bundle manifest is invalid")


async def _download(
    location: RegistryLocation,
    pkg_id: str,
    pinned: str | None,
    limits: Limits,
    fetcher: RegistryFetcher,
    destination: Path,
) -> RegistryEntry:
    budget = _budget(limits)
    selected = select_entry(await fetch_index(location, fetcher, budget), pkg_id, pinned)
    root = f"{location.base}{RECIPES_DIR}/{selected.id}/{selected.version}/"
    manifest = await budget.get(fetcher, root + MANIFEST_FILE, MAX_METADATA_BYTES)
    representation = _representation(manifest)
    sample = SAMPLE_FILES[representation]
    contents = {MANIFEST_FILE: manifest}
    for name in (RECIPE_FILE, SCHEMA_FILE, EXPECTED_FILE, sample):
        cap = limits.max_input_bytes(representation) if name == sample else MAX_METADATA_BYTES
        contents[name] = await budget.get(fetcher, root + name, cap)
    write_all(destination, contents)
    return selected


def install_from_registry_url(
    url: str,
    spec: str,
    repository: RecipeRepository,
    parser_for: ParserFor,
    *,
    at: datetime,
    limits: Limits | None = None,
    engine_version: str | None = None,
    transport: HttpTransport | None = None,
    resolver: Resolver | None = None,
) -> InstalledPackage:
    """Download ``ID[@VERSION]`` into a temporary directory, then run every local install gate.

    The result is a candidate; nothing is promoted and no job setting is touched. A refused
    download or a failed gate leaves the recipe store as it was.
    """
    pkg_id, pinned = parse_package_spec(spec)
    location = parse_registry_url(url)
    limits = limits or Limits()
    fetcher = registry_fetcher(location, transport, resolver)
    with tempfile.TemporaryDirectory(prefix="rastro-registry-") as scratch:
        package_dir = Path(scratch) / "package"
        selected = run_fetch(_download(location, pkg_id, pinned, limits, fetcher, package_dir))
        return install_package_dir(
            package_dir,
            selected,
            repository,
            parser_for,
            at=at,
            limits=limits,
            engine_version=engine_version,
        )


__all__ = [
    "FetchBudget",
    "RegistryFetchError",
    "WebRegistryFetcher",
    "install_from_registry_url",
    "registry_fetcher",
    "run_fetch",
    "search_registry_url",
]
