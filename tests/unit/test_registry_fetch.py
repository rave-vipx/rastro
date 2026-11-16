"""N10, HUB-002..HUB-006, SEC-001, SEC-005, SEC-008: install and search over a registry URL.

A fake transport serves a copy of the synthetic ``examples/registry`` directory; the real
``WebAcquirer`` policy (SSRF, pinning, redirects, robots, size caps) runs in front of it.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.adapters.structured import JsonParser
from rastro.application.operator_trust import operator_trust
from rastro.application.registry_fetch import install_from_registry_url, search_registry_url
from rastro.domain.acquisition import AcquisitionError, HttpResponse
from rastro.domain.models import ConfigurationError, Outcome
from rastro.domain.recipe_bundle import MAX_METADATA_BYTES, canonical_json, sha256_hex
from rastro.domain.registry import RegistryError

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

EXAMPLE = Path(__file__).parents[2] / "examples/registry"
BASE = "https://registry.example.org/catalog/"
PACKAGE = "recipes/uy-sodre-agenda/1.0.0"
AT = datetime(2026, 11, 16, tzinfo=UTC)


def _parser_for(representation):
    return JsonParser() if representation == "json" else ScraplingParser()


class Resolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses or ("8.8.8.8",)

    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return self.addresses


class Site:
    """Serves ``files`` (path under the registry root) like a static host."""

    def __init__(
        self, root: Path, *, redirects: dict[str, str] | None = None, robots: bytes | None = None
    ) -> None:
        self.root = root
        self.robots = robots
        self.redirects = redirects or {}
        self.requests: list[str] = []

    async def get(self, target, *, headers, timeout, max_bytes, proxy):
        self.requests.append(target.url)
        path = target.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        if path == "robots.txt":
            if self.robots is not None:
                return HttpResponse(200, {"content-type": "text/plain"}, self.robots, 0)
            return HttpResponse(404, {}, b"", 0)
        if path in self.redirects:
            return HttpResponse(302, {"location": self.redirects[path]}, b"", 0)
        relative = path.removeprefix("catalog/")
        file = self.root / relative
        if not file.is_file():
            return HttpResponse(404, {}, b"", 0)
        body = file.read_bytes()
        if len(body) > max_bytes:
            raise AcquisitionError("body_too_large", Outcome.PERMANENT_FAILURE)
        return HttpResponse(200, {"content-type": "application/json"}, body, len(body))


@pytest.fixture
def served(tmp_path: Path) -> Path:
    root = tmp_path / "served"
    shutil.copytree(EXAMPLE, root)
    return root


def _search(site: Site, url: str = BASE, query: str = "sodre", resolver=None):
    return search_registry_url(url, query, transport=site, resolver=resolver or Resolver())


def _install(site: Site, tmp_path: Path, url: str = BASE, resolver=None):
    repository = SQLiteRecipeRepository(tmp_path / "store.sqlite")
    installed = install_from_registry_url(
        url,
        "uy-sodre-agenda",
        repository,
        _parser_for,
        at=AT,
        transport=site,
        resolver=resolver or Resolver(),
    )
    return installed, repository


def test_search_over_https_matches_the_local_registry(served: Path) -> None:
    entries = _search(Site(served))
    assert [(entry.id, entry.version) for entry in entries] == [("uy-sodre-agenda", "1.0.0")]
    assert _search(Site(served), query="nothing-matches") == ()


def test_install_over_https_stores_a_candidate_only(served: Path, tmp_path: Path) -> None:
    site = Site(served)
    installed, repository = _install(site, tmp_path)
    assert installed.imported.stored.metadata.state == "candidate"
    assert installed.trust.replay_verified is True
    assert installed.published_trust.replay_verified is True
    assert (installed.id, installed.version) == ("uy-sodre-agenda", "1.0.0")
    fetched = {url.removeprefix(BASE) for url in site.requests if "robots" not in url}
    assert fetched == {
        "index.json",
        f"{PACKAGE}/bundle.json",
        f"{PACKAGE}/recipe.json",
        f"{PACKAGE}/schema.json",
        f"{PACKAGE}/expected.json",
        f"{PACKAGE}/sample.html",
    }
    assert repository.history(installed.imported.stored.key).versions[-1].state == "candidate"


def test_install_leaves_a_job_and_auto_promote_untouched(served: Path, tmp_path: Path) -> None:
    job = tmp_path / "job.toml"
    job.write_text("format_version = 1\n[repair]\nauto_promote = false\n", encoding="utf-8")
    before = job.read_bytes()
    installed, _ = _install(Site(served), tmp_path)
    assert installed.imported.stored.metadata.state == "candidate"
    assert job.read_bytes() == before


@pytest.mark.parametrize(
    "url",
    [
        "http://registry.example.org/catalog/",
        "https://user:pass@registry.example.org/catalog/",
        "https://user@registry.example.org/catalog/",
        "https://registry.example.org/catalog/?token=s3cr3t-value",
        "https://registry.example.org/catalog/#frag",
        "https://registry.example.org/catalog/../x/",
        "https://registry.example.org/%2e%2e/x/",
        "ftp://registry.example.org/catalog/",
        "https:///catalog/",
        "registry.example.org/catalog",
    ],
)
def test_unsafe_registry_urls_are_refused_before_any_request(served: Path, url: str) -> None:
    site = Site(served)
    with pytest.raises(RegistryError) as error:
        _search(site, url)
    assert "s3cr3t" not in str(error.value) and "pass" not in str(error.value)
    assert site.requests == []


def test_a_non_default_port_needs_the_operator_grant(served: Path) -> None:
    url = "https://registry.example.org:8443/catalog/"
    with pytest.raises(RegistryError, match="port_not_allowed"):
        _search(Site(served), url)
    with operator_trust(allow_ports=[8443]):
        assert len(_search(Site(served), url)) == 1


def test_private_address_is_refused_without_the_operator_grant(served: Path) -> None:
    site = Site(served)
    with pytest.raises(RegistryError, match="destination_denied"):
        _search(site, resolver=Resolver("10.0.0.5"))
    assert site.requests == []
    with operator_trust(allow_private_network=True):
        assert len(_search(site, resolver=Resolver("10.0.0.5"))) == 1
    with operator_trust(allow_private_network=True), pytest.raises(RegistryError):
        _search(Site(served), resolver=Resolver("169.254.169.254"))


def test_redirects_stay_on_the_registry_host_and_are_rechecked(served: Path) -> None:
    for target in (
        "https://127.0.0.1/index.json",
        "https://169.254.169.254/latest/meta-data",
        "http://registry.example.org/catalog/index.json",
        "https://other.example.net/catalog/index.json",
    ):
        site = Site(served, redirects={"catalog/index.json": target})
        with pytest.raises(RegistryError) as error:
            _search(site)
        assert "169.254" not in str(error.value)
    shutil.copy(served / "index.json", served / "moved.json")
    follow = Site(served, redirects={"catalog/index.json": f"{BASE}moved.json"})
    assert len(_search(follow)) == 1
    assert follow.requests[-1] == f"{BASE}moved.json"


def test_bad_digest_writes_nothing(served: Path, tmp_path: Path) -> None:
    sample = served / PACKAGE / "sample.html"
    sample.write_bytes(sample.read_bytes() + b"<!-- tampered -->")
    store = tmp_path / "store.sqlite"
    with pytest.raises(ConfigurationError):
        install_from_registry_url(
            BASE,
            "uy-sodre-agenda",
            SQLiteRecipeRepository(store),
            _parser_for,
            at=AT,
            transport=Site(served),
            resolver=Resolver(),
        )
    assert _recipe_count(store) == 0


def _recipe_count(store: Path) -> int:
    with closing(sqlite3.connect(store)) as db:
        tables = [
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
            if "recipe" in row[0] and not row[0].startswith("sqlite")
        ]
        return sum(db.execute(f"select count(*) from {table}").fetchone()[0] for table in tables)


def test_oversize_index_is_refused(served: Path) -> None:
    (served / "index.json").write_bytes(b"[" + b" " * (MAX_METADATA_BYTES + 10) + b"]")
    with pytest.raises(RegistryError, match="body_too_large"):
        _search(Site(served))


def test_oversize_package_file_is_refused_and_writes_nothing(served: Path, tmp_path: Path) -> None:
    (served / PACKAGE / "sample.html").write_bytes(b"x" * 2_000_001)
    with pytest.raises(RegistryError, match="body_too_large"):
        _install(Site(served), tmp_path)
    assert _recipe_count(tmp_path / "store.sqlite") == 0


@pytest.mark.parametrize("bad_id", ["../evil", "/abs", "a/b", "UPPER", "x"])
def test_traversal_or_invalid_paths_in_the_index_are_refused(served: Path, bad_id: str) -> None:
    index = json.loads((served / "index.json").read_text(encoding="utf-8"))
    index[0]["id"] = bad_id
    (served / "index.json").write_bytes(canonical_json(index))
    site = Site(served)
    with pytest.raises(RegistryError, match=r"index\.json is invalid"):
        _search(site)
    assert len(site.requests) <= 2  # robots.txt and the index; no package path was built


def test_a_manifest_that_lists_other_files_is_refused(served: Path, tmp_path: Path) -> None:
    manifest_path = served / PACKAGE / "bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["../../escape.json"] = sha256_hex(b"x")
    manifest_path.write_bytes(canonical_json(manifest))
    site = Site(served)
    with pytest.raises(ConfigurationError):
        _install(site, tmp_path)
    assert not any("escape" in url for url in site.requests)


def test_an_index_entry_the_package_does_not_match_is_refused(served: Path, tmp_path: Path) -> None:
    manifest_path = served / PACKAGE / "bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "9.9.9"
    manifest_path.write_bytes(canonical_json(manifest))
    with pytest.raises(ConfigurationError):
        _install(Site(served), tmp_path)
    assert _recipe_count(tmp_path / "store.sqlite") == 0


def test_a_missing_package_is_an_explicit_error(served: Path, tmp_path: Path) -> None:
    shutil.rmtree(served / "recipes")
    with pytest.raises(RegistryError, match="http_permanent_failure"):
        _install(Site(served), tmp_path)


def test_a_missing_index_is_not_an_empty_registry(served: Path) -> None:
    (served / "index.json").unlink()
    with pytest.raises(RegistryError, match="http_permanent_failure"):
        _search(Site(served))


def test_robots_txt_is_enforced_for_the_registry_host(served: Path) -> None:
    site = Site(served, robots=b"User-agent: *\nDisallow: /\n")
    with pytest.raises(RegistryError, match="robots_denied") as error:
        _search(site)
    assert "--ignore-robots" in (error.value.hint or "")
    assert not any(url.endswith("index.json") for url in site.requests)
    with operator_trust(ignore_robots=["registry.example.org"]):
        assert len(_search(site)) == 1


def test_a_manifest_without_a_known_representation_is_refused(served: Path, tmp_path: Path) -> None:
    manifest_path = served / PACKAGE / "bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["representation"] = "../../x"
    manifest_path.write_bytes(canonical_json(manifest))
    site = Site(served)
    with pytest.raises(ConfigurationError):
        _install(site, tmp_path)
    assert not any(url.endswith(("recipe.json", "x")) for url in site.requests)
    assert _recipe_count(tmp_path / "store.sqlite") == 0


def test_a_transport_that_ignores_max_bytes_is_still_bounded(served: Path) -> None:
    class Greedy(Site):
        async def get(self, target, *, headers, timeout, max_bytes, proxy):
            if target.url.endswith("index.json"):
                body = b"[" + b" " * (3 * 1024 * 1024) + b"]"
                return HttpResponse(200, {"content-type": "application/json"}, body, len(body))
            return await super().get(
                target, headers=headers, timeout=timeout, max_bytes=max_bytes, proxy=proxy
            )

    with pytest.raises(RegistryError, match="body_too_large"):
        _search(Greedy(served))


def test_a_missing_http_extra_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "rastro.adapters.http", raising=False)
    monkeypatch.delitem(sys.modules, "rastro.adapters.dns", raising=False)
    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    with pytest.raises(ConfigurationError, match=r"rastro-engine\[http\].*--registry-url"):
        search_registry_url(BASE, "sodre")


def _robots_requests(site: Site) -> int:
    return sum(url.endswith("/robots.txt") for url in site.requests)


def test_one_install_fetches_robots_txt_once(served: Path, tmp_path: Path) -> None:
    site = Site(served)
    _install(site, tmp_path)
    assert len(site.requests) == 7  # six package files and one robots.txt (N10, SEC-008)
    assert _robots_requests(site) == 1


def test_separate_installs_do_not_share_robots_answers(served: Path, tmp_path: Path) -> None:
    site = Site(served)
    _install(site, tmp_path)
    _install(site, tmp_path)
    assert _robots_requests(site) == 2


def test_ignored_robots_host_is_never_asked(served: Path, tmp_path: Path) -> None:
    site = Site(served)
    with operator_trust(ignore_robots=["registry.example.org"]):
        _install(site, tmp_path)
    assert _robots_requests(site) == 0
