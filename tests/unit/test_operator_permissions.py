"""Operator network permissions: destinations, ports, trust sources and the auth schema."""

from __future__ import annotations

import pytest

from rastro import AcquisitionPolicy
from rastro.application.destinations import permitted_url, pin_destination, public_address
from rastro.application.operator_trust import (
    current_trust,
    env_hosts,
    env_ports,
    operator_trust,
)
from rastro.cli_trust import hoist_trust_flags
from rastro.domain.acquisition import AcquisitionError
from rastro.domain.acquisition_auth import NetworkGrants
from rastro.domain.jobs import parse_job
from rastro.domain.model_trust import OperatorTrust
from rastro.domain.models import ConfigurationError

PRIVATE = [
    "10.0.0.1",
    "172.16.0.1",
    "192.168.1.1",
    "127.0.0.1",
    "100.64.0.1",
    "100.100.100.201",
    "::1",
    "fc00::1",
    "fd12:3456::1",
]
ALWAYS_DENIED = [
    "169.254.169.254",
    "100.100.100.200",
    "fd00:ec2::254",
    "169.254.0.1",
    "fe80::1",
    "fe80::1%en0",
    "0.0.0.0",
    "::",
    "224.0.0.1",
    "ff02::1",
    "255.255.255.255",
    "240.0.0.1",
    "::ffff:127.0.0.1",
    "::ffff:10.0.0.1",
    "64:ff9b::a00:1",
    "2002:a00:1::",
    "2001::1",
    "not-an-ip",
]


@pytest.mark.parametrize("address", PRIVATE)
def test_private_destination_needs_the_grant(address):
    with pytest.raises(AcquisitionError, match="destination_denied"):
        public_address(address)
    assert public_address(address, allow_private=True) == address


@pytest.mark.parametrize("address", ALWAYS_DENIED)
def test_metadata_and_special_ranges_stay_denied_with_the_grant(address):
    with pytest.raises(AcquisitionError, match="destination_denied"):
        public_address(address, allow_private=True)


class Resolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses

    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return self.addresses


def _granted(**grants) -> AcquisitionPolicy:
    return AcquisitionPolicy(allowed_hosts=("example.com",), allow_http=True).with_grants(
        NetworkGrants(**grants)
    )


@pytest.mark.asyncio
async def test_pinning_uses_the_grant_and_rejects_mixed_answers():
    plain = AcquisitionPolicy(allowed_hosts=("example.com",))
    with pytest.raises(AcquisitionError, match="destination_denied"):
        await pin_destination("https://example.com/", plain, Resolver("10.0.0.5"))
    policy = _granted(private_network=True)
    target = await pin_destination("https://example.com/", policy, Resolver("10.0.0.5"))
    assert target.address == "10.0.0.5"
    for answer in (("8.8.8.8", "169.254.169.254"), ("10.0.0.5", "fe80::1")):
        with pytest.raises(AcquisitionError, match="destination_denied"):
            await pin_destination("https://example.com/", policy, Resolver(*answer))
    with pytest.raises(AcquisitionError, match="host_not_allowed"):
        await pin_destination("https://other.test/", policy, Resolver("10.0.0.5"))


@pytest.mark.parametrize("url", ["https://example.com:8443/", "http://example.com:8443/"])
def test_extra_ports_need_the_grant_for_either_scheme(url):
    with pytest.raises(AcquisitionError, match="port_not_allowed"):
        permitted_url(url, _granted())
    assert permitted_url(url, _granted(ports=frozenset({8443})))[2] == 8443
    with pytest.raises(AcquisitionError, match="port_not_allowed"):
        permitted_url(url, _granted(ports=frozenset({9000})))


def test_standard_ports_still_work_when_a_port_is_granted():
    policy = _granted(ports=frozenset({8443}))
    assert permitted_url("https://example.com/", policy)[2] == 443
    assert permitted_url("http://example.com/", policy)[2] == 80


@pytest.mark.parametrize("bad", ["0", "65536", "-1", "abc", "1.5", "80 80", True, 3.5])
def test_garbage_ports_are_rejected(bad):
    with pytest.raises(ConfigurationError):
        env_ports([bad])


def test_ports_and_hosts_parse_and_normalise():
    assert env_ports(["8443", " 8080 ", 9000, ""]) == frozenset({8443, 8080, 9000})
    assert env_hosts(["A.Example", "b.example.", ""]) == frozenset({"a.example", "b.example"})
    for bad in ("*.example", "a.example/path", "-bad", "a b"):
        with pytest.raises(ConfigurationError):
            env_hosts([bad])
    with pytest.raises(ConfigurationError):
        env_ports("8443")  # a bare string is not a list


def test_trust_merges_and_reads_environment_and_scope(monkeypatch):
    left = OperatorTrust(allow_private_network=True, allowed_ports=frozenset({1}))
    right = OperatorTrust(allowed_ports=frozenset({2}), ignore_robots=frozenset({"a.example"}))
    merged = left.merged(right)
    assert merged.network_grants() == NetworkGrants(
        private_network=True, ports=frozenset({1, 2}), ignore_robots=frozenset({"a.example"})
    )
    assert current_trust().network_grants() == NetworkGrants()
    monkeypatch.setenv("RASTRO_ALLOW_PRIVATE_NETWORK", "yes")
    monkeypatch.setenv("RASTRO_ALLOWED_PORTS", "8443,8080")
    monkeypatch.setenv("RASTRO_IGNORE_ROBOTS", "A.example, b.example")
    grants = current_trust().network_grants()
    assert grants.private_network and grants.ports == {8443, 8080}
    assert grants.ignore_robots == {"a.example", "b.example"}
    monkeypatch.setenv("RASTRO_ALLOWED_PORTS", "8443,nope")
    with pytest.raises(ConfigurationError):
        current_trust()
    monkeypatch.delenv("RASTRO_ALLOWED_PORTS")
    monkeypatch.delenv("RASTRO_ALLOW_PRIVATE_NETWORK")
    monkeypatch.delenv("RASTRO_IGNORE_ROBOTS")
    with operator_trust(allow_private_network=True, allow_ports=[8443], ignore_robots=["x.test"]):
        assert current_trust().network_grants() == NetworkGrants(
            private_network=True, ports=frozenset({8443}), ignore_robots=frozenset({"x.test"})
        )
    assert current_trust().network_grants() == NetworkGrants()
    with pytest.raises(ConfigurationError), operator_trust(allow_ports=[0]):
        pass


def test_cli_flags_are_hoisted_before_the_subcommand():
    argv = ["job", "run", "j.toml", "--allow-port", "8443", "--allow-private-network"]
    argv += ["--ignore-robots=a.example", "--allow-port=8080"]
    assert hoist_trust_flags(argv) == [
        "--allow-port",
        "8443",
        "--allow-private-network",
        "--ignore-robots=a.example",
        "--allow-port=8080",
        "job",
        "run",
        "j.toml",
    ]


@pytest.mark.parametrize(
    "block",
    [
        "allow_private_network = true",
        "allowed_ports = [8443]",
        "allow_ports = [8443]",
        "ignore_robots = ['a.example']",
    ],
)
def test_a_job_file_cannot_grant_network_permissions(block):
    tail = """
[[sources]]
url = "https://example.com/"
[[fields]]
name = "title"
type = "string"
"""
    with pytest.raises((ValueError, ConfigurationError)):
        parse_job(
            f'format_version = 1\n[acquisition]\nallowed_hosts = ["example.com"]\n{block}\n{tail}',
            format="toml",
        )
    with pytest.raises((ValueError, ConfigurationError)):
        parse_job(
            f'format_version = 1\n{block}\n[acquisition]\nallowed_hosts = ["example.com"]{tail}',
            format="toml",
        )


@pytest.mark.parametrize(
    "header",
    [
        "Host",
        "cookie",
        "Set-Cookie",
        "Content-Length",
        "Transfer-Encoding",
        "Connection",
        "Proxy-Authorization",
        "User-Agent",
        "Accept",
        "Accept-Language",
        "If-None-Match",
        "If-Modified-Since",
        "Bad Header",
        "X:Y",
        "X\r\nY",
        "",
    ],
)
def test_auth_header_name_rules(header):
    with pytest.raises(ValueError):
        AcquisitionPolicy(
            allowed_hosts=("example.com",),
            auth=[{"host": "example.com", "header": header, "value_env": "RASTRO_TOKEN"}],
        )


@pytest.mark.parametrize(
    "entry",
    [
        {"host": "other.test", "value_env": "RASTRO_TOKEN"},
        {"host": "example.com", "value_env": "bad name"},
        {"host": "example.com", "value_env": "RASTRO_TOKEN", "scheme": "Bearer\r\nX: y"},
        {"host": "example.com", "value_env": "RASTRO_TOKEN", "scheme": "two words"},
        {"host": "example.com", "value_env": "RASTRO_TOKEN", "unknown": 1},
    ],
)
def test_auth_entry_rules(entry):
    with pytest.raises(ValueError):
        AcquisitionPolicy(allowed_hosts=("example.com",), auth=[entry])


def test_auth_defaults_and_duplicates():
    policy = AcquisitionPolicy(
        allowed_hosts=("example.com",),
        auth=[{"host": "EXAMPLE.com", "value_env": "RASTRO_TOKEN", "scheme": "Bearer"}],
    )
    entry = policy.auth[0]
    assert (entry.host, entry.header, entry.scheme) == ("example.com", "Authorization", "Bearer")
    with pytest.raises(ValueError):
        AcquisitionPolicy(
            allowed_hosts=("example.com",),
            auth=[
                {"host": "example.com", "value_env": "RASTRO_A"},
                {"host": "example.com", "header": "authorization", "value_env": "RASTRO_B"},
            ],
        )


@pytest.mark.asyncio
async def test_request_headers_never_reach_a_proxy_in_the_tunnel_setup(monkeypatch):
    """With a proxy, libcurl must not copy custom headers to the CONNECT request."""
    from curl_cffi import CurlOpt
    from curl_cffi.requests import AsyncSession, Response

    from rastro.adapters.http import CurlTransport
    from rastro.domain.acquisition import PinnedTarget, ProxyRoute

    seen: dict[int, object] = {}

    async def get(session, url, **kwargs):
        seen.update(session.curl_options)
        result = Response()
        result.primary_ip = "192.0.2.10"
        result.status_code = 200
        return result

    monkeypatch.setattr(AsyncSession, "get", get)
    target = PinnedTarget("https://example.com/", "example.com", 443, "8.8.8.8")
    await CurlTransport().get(
        target,
        headers={"Authorization": "x"},
        timeout=1,
        max_bytes=10,
        proxy=ProxyRoute("p", "http://proxy.example:3128"),
    )
    assert seen[CurlOpt.HEADEROPT] == 1  # CURLHEADER_SEPARATE


def test_cli_flags_become_process_wide_trust_and_garbage_is_refused(capsys):
    from rastro.application.operator_trust import set_process_trust
    from rastro.cli import _build_parser, main
    from rastro.cli_trust import apply_trust

    argv = hoist_trust_flags(
        [
            *("job", "schema", "--allow-private-network", "--allow-port", "8443"),
            *("--allow-port=8080", "--ignore-robots", "A.example"),
        ]
    )
    previous = apply_trust(_build_parser().parse_args(argv), argv)
    try:
        assert current_trust().network_grants() == NetworkGrants(
            private_network=True,
            ports=frozenset({8443, 8080}),
            ignore_robots=frozenset({"a.example"}),
        )
    finally:
        set_process_trust(previous)
    assert current_trust().network_grants() == NetworkGrants()
    assert main(["job", "schema", "--allow-port", "abc"]) != 0
    assert main(["job", "schema", "--ignore-robots", "*.example"]) != 0
    capsys.readouterr()
    help_text = _build_parser().format_help()
    assert "--allow-private-network" in help_text and "--ignore-robots" in help_text
