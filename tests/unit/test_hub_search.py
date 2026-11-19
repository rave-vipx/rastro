"""Hub search: a new URL is matched by domain against a registry and offered as candidates.

The registry is untrusted input. Offers never touch a recipe store; installing one runs the
same gates as any registry install and leaves a candidate. A fake transport serves a copy of
the synthetic ``examples/registry`` directory behind the real network policy.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.structured import JsonParser
from rastro.application.hub_search import hub_candidates, install_candidate
from rastro.application.operator_trust import operator_trust
from rastro.application.registry_url import DEFAULT_HUB_URL, parse_registry_url
from rastro.domain.acquisition import HttpResponse
from rastro.domain.models import ConfigurationError
from rastro.domain.registry import RegistryError

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

EXAMPLE = Path(__file__).parents[2] / "examples/registry"
LISTING = "https://listing.example/cards/page-2"
SELF_HOSTED = "https://recipes.example.org/hub/"
AT = datetime(2026, 11, 19, tzinfo=UTC)


def _parser_for(representation):
    return JsonParser() if representation == "json" else ScraplingParser()


class Resolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses or ("8.8.8.8",)

    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return self.addresses


class Site:
    """Serves ``root`` under any registry base path, like a static host."""

    def __init__(self, root: Path, base: str) -> None:
        self.root = root
        self.prefix = base.split("://", 1)[1].split("/", 1)[1]
        self.requests: list[str] = []

    async def get(self, target, *, headers, timeout, max_bytes, proxy):
        self.requests.append(target.url)
        path = target.url.split("://", 1)[1].split("/", 1)[1]
        file = self.root / path.removeprefix(self.prefix)
        if path == "robots.txt" or not file.is_file():
            return HttpResponse(404, {}, b"", 0)
        body = file.read_bytes()
        return HttpResponse(200, {"content-type": "application/json"}, body, len(body))


@pytest.fixture
def served(tmp_path: Path) -> Path:
    root = tmp_path / "served"
    shutil.copytree(EXAMPLE, root)
    return root


def _count(store: Path) -> int:
    if not store.exists():
        return 0
    with closing(sqlite3.connect(store)) as db:
        names = db.execute("select name from sqlite_master where type='table'").fetchall()
        tables = [n for (n,) in names if "recipe" in n and not n.startswith("sqlite")]
        return sum(db.execute(f"select count(*) from {t}").fetchone()[0] for t in tables)


def test_default_hub_url_is_a_valid_registry_url() -> None:
    assert parse_registry_url(DEFAULT_HUB_URL).base == DEFAULT_HUB_URL


def test_new_url_offers_hub_candidate(served: Path, tmp_path: Path) -> None:
    """A domain match is offered as a candidate; nothing is installed or activated."""
    site = Site(served, DEFAULT_HUB_URL)
    offers = hub_candidates(LISTING, transport=site, resolver=Resolver())
    assert [(o.entry.id, o.state) for o in offers] == [("synthetic-listing", "candidate")]
    assert offers[0].registry == DEFAULT_HUB_URL
    assert offers[0].card is None and offers[0].health is None
    assert offers[0].to_json()["state"] == "candidate"
    assert hub_candidates("https://elsewhere.example/", transport=site, resolver=Resolver()) == ()
    local = hub_candidates(LISTING, registry=served)
    assert [o.entry.id for o in local] == ["synthetic-listing"]
    assert not list(tmp_path.glob("*.sqlite"))


def test_domain_match_ignores_path_but_not_port_or_subdomain(served: Path) -> None:
    assert [o.entry.id for o in hub_candidates("https://LISTING.example/", registry=served)] == [
        "synthetic-listing"
    ]
    assert hub_candidates("https://listing.example:8443/cards", registry=served) == ()
    assert hub_candidates("https://www.listing.example/cards", registry=served) == ()


@pytest.mark.parametrize("bad", ["", "listing.example/cards", "ftp://listing.example/", "x" * 3000])
def test_an_unusable_new_url_is_a_configuration_error(served: Path, bad: str) -> None:
    with pytest.raises(ConfigurationError):
        hub_candidates(bad, registry=served)


def test_install_stays_candidate_until_gates(served: Path, tmp_path: Path) -> None:
    """Installing an offer stores a candidate only; nothing serves until promotion gates pass."""
    site = Site(served, DEFAULT_HUB_URL)
    [offer] = hub_candidates(LISTING, transport=site, resolver=Resolver())
    repository = SQLiteRecipeRepository(tmp_path / "store.sqlite")
    installed = install_candidate(
        offer, repository, _parser_for, at=AT, transport=site, resolver=Resolver()
    )
    key = installed.imported.stored.key
    assert installed.imported.stored.metadata.state == "candidate"
    assert [v.state for v in repository.history(key).versions] == ["candidate"]
    assert repository.get(key) is None
    [local] = hub_candidates(LISTING, registry=served)
    again = install_candidate(local, repository, _parser_for, at=AT)
    assert again.imported.reused is True


def test_hub_bad_digest_writes_nothing(served: Path, tmp_path: Path) -> None:
    recipe = served / "recipes/synthetic-listing/1.0.0/recipe.json"
    recipe.write_bytes(recipe.read_bytes() + b" ")
    site = Site(served, DEFAULT_HUB_URL)
    [offer] = hub_candidates(LISTING, transport=site, resolver=Resolver())
    store = tmp_path / "store.sqlite"
    with pytest.raises(ConfigurationError):
        install_candidate(
            offer,
            SQLiteRecipeRepository(store),
            _parser_for,
            at=AT,
            transport=site,
            resolver=Resolver(),
        )
    assert _count(store) == 0


def _health(root: Path, **overrides: object) -> None:
    package = {
        "id": "synthetic-listing",
        "version": "1.0.0",
        "last_verified": "2026-11-18",
        "rastro_version": "1.0.0",
        "passed": True,
        **overrides,
    }
    (root / "health.json").write_text(json.dumps({"format": 1, "packages": [package]}))


def test_self_hosted_layout_still_works(served: Path) -> None:
    """Any https registry with the same layout works, with the hub's CI health when present."""
    _health(served)
    site = Site(served, SELF_HOSTED)
    [offer] = hub_candidates(LISTING, registry=SELF_HOSTED, transport=site, resolver=Resolver())
    assert offer.registry == SELF_HOSTED
    assert offer.health is not None and offer.health.passed is True
    assert offer.to_json()["health"]["last_verified"] == "2026-11-18"
    [local] = hub_candidates(LISTING, registry=served)
    assert local.health == offer.health


@pytest.mark.parametrize(
    "overrides", [{"passed": "yes"}, {"version": "../x"}, {"extra": 1}, {"rastro_version": "1"}]
)
def test_invalid_health_is_refused_not_ignored(served: Path, overrides: dict) -> None:
    _health(served, **overrides)
    with pytest.raises(RegistryError, match=r"health\.json"):
        hub_candidates(LISTING, registry=served)


def test_invalid_card_is_refused(served: Path) -> None:
    card = served / "recipes/synthetic-listing/1.0.0/card.json"
    card.write_text(json.dumps({"format": 1, "fields": [{"name": "x", "description": "\x1b[2J"}]}))
    with pytest.raises(RegistryError, match=r"card\.json"):
        hub_candidates(LISTING, registry=served)


def test_private_hub_address_needs_the_operator_grant(served: Path) -> None:
    site = Site(served, SELF_HOSTED)
    with pytest.raises(RegistryError, match="destination_denied"):
        hub_candidates(
            LISTING, registry=SELF_HOSTED, transport=site, resolver=Resolver("127.0.0.1")
        )
    assert site.requests == []
    with operator_trust(allow_private_network=True):
        offers = hub_candidates(
            LISTING, registry=SELF_HOSTED, transport=site, resolver=Resolver("127.0.0.1")
        )
    assert len(offers) == 1


def test_unsafe_hub_urls_are_refused_before_any_request(served: Path) -> None:
    site = Site(served, SELF_HOSTED)
    for url in ("http://recipes.example.org/hub/", "https://u:p@recipes.example.org/"):
        with pytest.raises(RegistryError):
            hub_candidates(LISTING, registry=url, transport=site, resolver=Resolver())
    assert site.requests == []
