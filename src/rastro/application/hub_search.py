"""Offer hub recipes for a new URL: a domain match is a candidate, never an active recipe.

The hub is any registry (the public default, a self-hosted URL or a local checkout) and its
files are untrusted (ADR 0037): the index, cards and health go through the same validation
and network policy as ``rastro recipe search``. An offer touches no recipe store; installing
one runs every registry gate and stores a candidate that still has to pass promotion.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from rastro.application.recipe_hub import ParserFor
from rastro.application.recipe_registry import install_from_registry
from rastro.application.registry_install import InstalledPackage
from rastro.application.registry_store import (
    CARD_FILE,
    HEALTH_FILE,
    MAX_CARD_BYTES,
    HubCard,
    PackageHealth,
    check_registry_root,
    load_card,
    load_health,
    load_index,
    parse_card,
    parse_health,
)
from rastro.application.registry_url import DEFAULT_HUB_URL, RegistryLocation, parse_registry_url
from rastro.domain.models import ConfigurationError, Limits
from rastro.domain.recipe_bundle import MAX_METADATA_BYTES
from rastro.domain.registry import RECIPES_DIR, RegistryEntry, semver_key
from rastro.ports.acquisition import HttpTransport, Resolver
from rastro.ports.recipes import RecipeRepository

MAX_CANDIDATES = 5
_MAX_URL = 2048


@dataclass(frozen=True)
class HubCandidate:
    """One package offered for a URL's domain; ``state`` is always ``candidate``."""

    entry: RegistryEntry
    registry: str
    card: HubCard | None
    health: PackageHealth | None
    state: Literal["candidate"] = "candidate"

    def to_json(self) -> dict[str, Any]:
        return {
            **self.entry.model_dump(mode="json"),
            "registry": self.registry,
            "state": self.state,
            "card": None if self.card is None else self.card.model_dump(mode="json"),
            "health": None if self.health is None else self.health.model_dump(mode="json"),
        }


def url_domain(url: str) -> str:
    """``host[:port]`` of an http(s) URL, lower-cased; the path and query are ignored."""
    try:
        parts = urlsplit(url) if isinstance(url, str) and len(url) <= _MAX_URL else None
        port = parts.port if parts else None
    except ValueError:
        parts = None
    if parts is None or parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ConfigurationError("A hub search needs an http(s) URL with a host")
    return f"{parts.hostname}:{port}" if port else parts.hostname


def _on_domain(entry: RegistryEntry, domain: str) -> bool:
    return any(pattern.partition("/")[0].lower() == domain for pattern in entry.source_patterns)


def _latest(entries: Sequence[RegistryEntry], domain: str) -> list[RegistryEntry]:
    newest: dict[str, RegistryEntry] = {}
    for entry in entries:
        if not _on_domain(entry, domain):
            continue
        held = newest.get(entry.id)
        if held is None or semver_key(entry.version) > semver_key(held.version):
            newest[entry.id] = entry
    return [newest[key] for key in sorted(newest)][:MAX_CANDIDATES]


def _health_for(entry: RegistryEntry, health: Sequence[PackageHealth]) -> PackageHealth | None:
    return next((h for h in health if (h.id, h.version) == (entry.id, entry.version)), None)


def hub_candidates(
    url: str,
    *,
    registry: Path | str | None = None,
    limits: Limits | None = None,
    transport: HttpTransport | None = None,
    resolver: Resolver | None = None,
) -> tuple[HubCandidate, ...]:
    """Packages whose source patterns are on ``url``'s domain, newest version of each.

    ``registry`` is a local directory or an https registry URL; the public hub by default.
    """
    domain = url_domain(url)
    if isinstance(registry, Path):
        return _local(registry, domain)
    location = parse_registry_url(registry or DEFAULT_HUB_URL)
    from rastro.application.registry_fetch import registry_fetcher, run_fetch

    fetcher = registry_fetcher(location, transport, resolver)
    return run_fetch(_remote(location, domain, fetcher, limits or Limits()))


def _local(registry: Path, domain: str) -> tuple[HubCandidate, ...]:
    check_registry_root(registry, create=False)
    selected = _latest(load_index(registry), domain)
    health = load_health(registry) if selected else ()
    return tuple(
        HubCandidate(
            entry,
            str(registry),
            load_card(registry, entry.id, entry.version),
            _health_for(entry, health),
        )
        for entry in selected
    )


async def _remote(
    location: RegistryLocation, domain: str, fetcher: Any, limits: Limits
) -> tuple[HubCandidate, ...]:
    from rastro.application.registry_fetch import FetchBudget, fetch_index

    budget = FetchBudget(
        MAX_METADATA_BYTES * 2 + MAX_CARD_BYTES * MAX_CANDIDATES, 2 + MAX_CANDIDATES
    )
    selected = _latest(await fetch_index(location, fetcher, budget), domain)
    if not selected:
        return ()
    raw_health = await budget.get_optional(fetcher, location.base + HEALTH_FILE, MAX_METADATA_BYTES)
    health = parse_health(raw_health) if raw_health is not None else ()
    offers = []
    for entry in selected:
        card_url = f"{location.base}{RECIPES_DIR}/{entry.id}/{entry.version}/{CARD_FILE}"
        raw_card = await budget.get_optional(fetcher, card_url, MAX_CARD_BYTES)
        card = parse_card(raw_card) if raw_card is not None else None
        offers.append(HubCandidate(entry, location.base, card, _health_for(entry, health)))
    return tuple(offers)


def install_candidate(
    candidate: HubCandidate,
    repository: RecipeRepository,
    parser_for: ParserFor,
    *,
    at: datetime,
    limits: Limits | None = None,
    transport: HttpTransport | None = None,
    resolver: Resolver | None = None,
) -> InstalledPackage:
    """Install the offered version through every registry gate; the result is a candidate."""
    spec = f"{candidate.entry.id}@{candidate.entry.version}"
    if not candidate.registry.startswith("https://"):
        return install_from_registry(
            Path(candidate.registry), spec, repository, parser_for, at=at, limits=limits
        )
    from rastro.application.registry_fetch import install_from_registry_url

    return install_from_registry_url(
        candidate.registry,
        spec,
        repository,
        parser_for,
        at=at,
        limits=limits,
        transport=transport,
        resolver=resolver,
    )
