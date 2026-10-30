import logging
from urllib.parse import urlsplit

import pytest

from rastro import AcquisitionPolicy, Outcome, Rastro, Source
from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.acquisition import WebAcquirer
from rastro.domain.acquisition import HttpResponse
from rastro.domain.memory import RecipeNotFoundError

pytestmark = pytest.mark.asyncio
USER, PASSWORD = "synthetic-proxy-user", "synthetic-proxy-password"
ENVIRON = {"RASTRO_TEST_PROXY_USER": USER, "RASTRO_TEST_PROXY_PASSWORD": PASSWORD}
PROFILES = {
    "eu-office": {
        "proxy_url": "http://proxy.internal.example:3128",
        "username_env": "RASTRO_TEST_PROXY_USER",
        "password_env": "RASTRO_TEST_PROXY_PASSWORD",
    },
    "open": "https://relay.example:8443",
}
PAGE = (
    b'<script type="application/ld+json">'
    b'{"@type":"Product","name":"Synthetic lamp","offers":{"price":"12.50"}}</script>'
)


class Resolver:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    async def resolve(self, host, port):
        self.calls.append(host)
        return self.answers.get(host, ("8.8.8.8",))


def response(status=200, body=PAGE, **headers):
    return HttpResponse(status, {"content-type": "text/html", **headers}, body, len(body))


class Transport:
    """Records every transfer; it also accepts transports written before egress profiles."""

    def __init__(self, *pages):
        self.pages = list(pages or [response()])
        self.calls = []

    async def get(self, target, **kwargs):
        self.calls.append((target, kwargs))
        if urlsplit(target.url).path == "/robots.txt":
            return response(404, b"")
        return self.pages.pop(0)


def acquirer(transport, resolver=None, environ=ENVIRON):
    policy = AcquisitionPolicy(
        allowed_hosts=("example.com", "other.test"),
        min_interval_seconds=0,
        backoff_seconds=0.001,
        egress_profiles=PROFILES,
    )
    return WebAcquirer(
        policy, transport=transport, resolver=resolver or Resolver(), environ=environ
    )


async def test_direct_egress_never_passes_a_proxy_option():
    transport = Transport()
    result = await acquirer(transport).acquire(Source(url="https://example.com/"), max_bytes=500)
    assert result.status == Outcome.SUCCESS and result.egress == "direct"
    assert transport.calls and all("proxy" not in options for _, options in transport.calls)


async def test_selected_profile_routes_every_transfer_after_local_validation():
    transport, resolver = Transport(), Resolver()
    result = await acquirer(transport, resolver).acquire(
        Source(url="https://example.com/a", egress="eu-office"), max_bytes=500
    )
    assert result.status == Outcome.SUCCESS and result.egress == "eu-office"
    assert resolver.calls == ["example.com", "example.com"]  # robots and page
    for target, options in transport.calls:
        route = options["proxy"]
        assert target.address == "8.8.8.8"
        assert (route.name, route.url) == ("eu-office", "http://proxy.internal.example:3128")
        assert (route.username, route.password) == (USER, PASSWORD)


async def test_profile_without_credentials_needs_no_environment():
    transport = Transport()
    result = await acquirer(transport, environ={}).acquire(
        Source(url="https://example.com/", egress="open"), max_bytes=500
    )
    route = transport.calls[-1][1]["proxy"]
    assert result.status == Outcome.SUCCESS
    assert (route.url, route.username, route.password) == ("https://relay.example:8443", None, None)


async def test_process_environment_is_the_default_secret_source(monkeypatch):
    monkeypatch.setenv("RASTRO_TEST_PROXY_USER", USER)
    monkeypatch.setenv("RASTRO_TEST_PROXY_PASSWORD", PASSWORD)
    transport = Transport()
    await acquirer(transport, environ=None).acquire(
        Source(url="https://example.com/", egress="eu-office"), max_bytes=500
    )
    assert transport.calls[-1][1]["proxy"].password == PASSWORD


@pytest.mark.parametrize(
    ("egress", "environ", "code"),
    [
        ("unknown", ENVIRON, "unsupported_egress"),
        ("eu-office", {}, "egress_credentials_unavailable"),
        ("eu-office", {"RASTRO_TEST_PROXY_USER": USER}, "egress_credentials_unavailable"),
        (
            "eu-office",
            {**ENVIRON, "RASTRO_TEST_PROXY_PASSWORD": "a\r\nb"},
            "egress_credentials_unavailable",
        ),
        ("eu-office", {**ENVIRON, "RASTRO_TEST_PROXY_USER": ""}, "egress_credentials_unavailable"),
    ],
)
async def test_configuration_failures_happen_before_dns_or_network(egress, environ, code):
    transport, resolver = Transport(), Resolver()
    result = await acquirer(transport, resolver, environ).acquire(
        Source(url="https://example.com/", egress=egress), max_bytes=500
    )
    assert (result.status, result.issue_code) == (Outcome.PERMANENT_FAILURE, code)
    assert resolver.calls == [] and transport.calls == []
    assert result.requests == 0 and result.egress == egress


@pytest.mark.parametrize(
    ("status", "code", "direct_code"),
    [
        (403, "access_restricted", "host_access_restricted"),
        (429, "rate_limited", "retry_after_pending"),
    ],
)
async def test_restriction_through_proxy_never_falls_back_to_another_egress(
    status, code, direct_code
):
    transport = Transport(response(status, b"", **{"retry-after": "60"}), response())
    client = acquirer(transport)
    result = await client.acquire(
        Source(url="https://example.com/", egress="eu-office"), max_bytes=500
    )
    assert result.status == Outcome.ACCESS_RESTRICTION
    assert result.issue_code == code
    assert result.retries == 0 and result.requests == 2
    assert all(options["proxy"].name == "eu-office" for _, options in transport.calls)
    # The host restriction is shared: switching to direct egress cannot bypass it.
    direct = await client.acquire(Source(url="https://example.com/"), max_bytes=500)
    assert direct.status == Outcome.ACCESS_RESTRICTION
    assert direct.issue_code == direct_code
    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    ("location", "answers", "code"),
    [
        ("https://evil.test/", {}, "host_not_allowed"),
        ("https://other.test/", {"other.test": ("8.8.8.8", "10.0.0.1")}, "destination_denied"),
        ("https://169.254.169.254/latest", {}, "host_not_allowed"),
        ("http://other.test/", {}, "https_required"),
    ],
)
async def test_each_redirect_hop_is_validated_locally_under_proxy(location, answers, code):
    transport = Transport(response(302, b"", location=location))
    result = await acquirer(transport, Resolver(answers)).acquire(
        Source(url="https://example.com/", egress="eu-office"), max_bytes=500
    )
    assert (result.status, result.issue_code) == (Outcome.ACCESS_RESTRICTION, code)
    assert {urlsplit(target.url).hostname for target, _ in transport.calls} == {"example.com"}


async def test_private_target_is_denied_before_sending_to_proxy():
    transport = Transport()
    result = await acquirer(transport, Resolver({"example.com": ("127.0.0.1",)})).acquire(
        Source(url="https://example.com/", egress="eu-office"), max_bytes=500
    )
    assert result.issue_code == "destination_denied" and transport.calls == []


async def test_secrets_never_reach_results_logs_or_recipe_memory(tmp_path, schema, recipe, caplog):
    transport = Transport()
    client = acquirer(transport)
    engine = Rastro(
        acquisition=client,
        policy=client.policy,
        repository=SQLiteRecipeRepository(tmp_path / "recipes.db"),
    )
    source = Source(url="https://example.com/p", egress="eu-office")
    with caplog.at_level(logging.DEBUG):
        result = await engine.extract(source=source, schema=schema, recipe=recipe)
    assert result.status == Outcome.SUCCESS and result.metrics.egress == "eu-office"
    assert transport.calls[-1][1]["proxy"].password == PASSWORD
    stored = (tmp_path / "recipes.db").read_bytes()
    artifacts = [
        result.model_dump_json(),
        repr(result),
        client.policy.model_dump_json(),
        repr(client.policy),
        repr(transport.calls[-1][1]["proxy"]),
        caplog.text,
    ]
    for secret in (USER, PASSWORD):
        assert all(secret not in artifact for artifact in artifacts)
        assert secret.encode() not in stored
    assert "proxy.internal.example" not in result.model_dump_json()
    assert b"proxy.internal.example" not in stored and b"eu-office" in stored


async def test_recipe_memory_is_isolated_by_egress_profile(tmp_path, schema, recipe):
    repository = SQLiteRecipeRepository(tmp_path / "recipes.db")
    transport = Transport(response(), response())
    client = acquirer(transport)
    engine = Rastro(acquisition=client, policy=client.policy, repository=repository)
    first = await engine.extract(
        source=Source(url="https://example.com/p"), schema=schema, recipe=recipe
    )
    reused = await engine.extract(source=Source(url="https://example.com/p"), schema=schema)
    assert first.status == reused.status == Outcome.SUCCESS and reused.recipe_reused
    assert reused.metrics.egress == "direct"
    calls = len(transport.calls)
    # A direct recipe is never reused for a proxied origin; nothing is fetched either.
    with pytest.raises(RecipeNotFoundError):
        await engine.extract(
            source=Source(url="https://example.com/p", egress="eu-office"), schema=schema
        )
    assert len(transport.calls) == calls


async def test_supplied_html_metrics_record_the_same_egress_as_recipe_memory(
    tmp_path, schema, recipe
):
    repository = SQLiteRecipeRepository(tmp_path / "recipes.db")
    engine = Rastro(repository=repository)
    source = Source(url="https://example.com/p", egress="eu-office")
    html = PAGE.decode()
    explicit = engine.extract_html(html, source=source, schema=schema, recipe=recipe)
    reused = await engine.extract_html_async(html, source=source, schema=schema)
    assert explicit.status == reused.status == Outcome.SUCCESS and reused.recipe_reused
    assert explicit.metrics.egress == reused.metrics.egress == "eu-office"
    direct = Rastro().extract_html(
        html, source=Source(url="https://example.com/p"), schema=schema, recipe=recipe
    )
    assert direct.metrics.egress == "direct"


async def test_json_acquisition_through_profile_isolates_egress_and_representation(
    tmp_path, schema
):
    from rastro import Recipe, RecipeKey

    body = b'{"items": [{"name": "Synthetic lamp", "price": "12.50"}]}'
    api = response(body=body, **{"content-type": "application/json"})
    transport = Transport(api, api)
    repository = SQLiteRecipeRepository(tmp_path / "recipes.db")
    client = acquirer(transport)
    engine = Rastro(acquisition=client, policy=client.policy, repository=repository)
    source = Source(url="https://example.com/api", egress="eu-office")
    recipe = Recipe(
        strategy="json", record_path="/items", fields={"name": "/name", "price": "/price"}
    )
    first = await engine.extract(source=source, schema=schema, recipe=recipe, representation="json")
    reused = await engine.extract(source=source, schema=schema, representation="json")
    assert first.status == reused.status == Outcome.SUCCESS and reused.recipe_reused
    assert reused.metrics.egress == "eu-office"
    assert reused.records[0].evidence["price"].method == "json"
    for target, options in transport.calls:
        assert options["proxy"].name == "eu-office"
        if urlsplit(target.url).path != "/robots.txt":
            assert options["headers"]["Accept"] == "application/json"
    keys = {
        (egress, representation): RecipeKey.for_source(
            source.model_copy(update={"egress": egress}), schema, representation=representation
        )
        for egress in ("direct", "eu-office")
        for representation in ("html", "json")
    }
    assert len({key.canonical() for key in keys.values()}) == 4
    assert repository.get(keys["eu-office", "json"]) is not None
    assert all(repository.get(keys[k]) is None for k in keys if k != ("eu-office", "json"))
    calls = len(transport.calls)
    # Neither the direct JSON nor the proxied HTML context reuses this recipe or fetches.
    for other, representation in (
        (Source(url="https://example.com/api"), "json"),
        (source, "html"),
    ):
        with pytest.raises(RecipeNotFoundError):
            await engine.extract(source=other, schema=schema, representation=representation)
    assert len(transport.calls) == calls
