"""Validate every hop before passing a pinned destination to the transport."""

import ipaddress
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, PinnedTarget
from rastro.domain.models import Outcome, Source
from rastro.ports.acquisition import Resolver

_TRANSITION_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)


# What the operator's private-network grant adds. Link-local (cloud metadata), unspecified,
# multicast, reserved and mapped/transition forms are never reachable (SEC-001).
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
    )
)


# Cloud metadata services that sit inside ranges the private grant opens (AWS IMDS over
# IPv6 in a ULA, Alibaba Cloud in CGNAT). IPv4 169.254.169.254 is already link-local.
_METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(value) for value in ("fd00:ec2::254", "100.100.100.200")
)


def public_address(value: str, *, allow_private: bool = False) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise AcquisitionError("destination_denied", Outcome.ACCESS_RESTRICTION) from None
    never = (
        address.is_multicast
        or address.is_unspecified
        or address.is_link_local
        or address in _METADATA_ADDRESSES
        or "%" in value
        or (
            isinstance(address, ipaddress.IPv6Address)
            and (address.ipv4_mapped is not None or any(address in n for n in _TRANSITION_NETWORKS))
        )
    )
    public = address.is_global and not address.is_reserved
    local = allow_private and any(address in network for network in _PRIVATE_NETWORKS)
    if never or not (public or local):
        raise AcquisitionError("destination_denied", Outcome.ACCESS_RESTRICTION)
    return str(address)


def permitted_url(url: str, policy: AcquisitionPolicy) -> tuple[str, str, int]:
    try:
        canonical = Source(url=url).url
    except ValidationError:
        raise AcquisitionError("invalid_url", Outcome.ACCESS_RESTRICTION) from None
    parsed = urlsplit(canonical)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in policy.allowed_hosts:
        raise AcquisitionError("host_not_allowed", Outcome.ACCESS_RESTRICTION)
    if parsed.scheme != "https" and not policy.allow_http:
        raise AcquisitionError("https_required", Outcome.ACCESS_RESTRICTION)
    standard = 443 if parsed.scheme == "https" else 80
    port = parsed.port or standard
    if port != standard and port not in policy.grants.ports:
        raise AcquisitionError("port_not_allowed", Outcome.ACCESS_RESTRICTION)
    netloc = f"[{host}]" if ":" in host else host
    if port != standard:
        netloc = f"{netloc}:{port}"  # a granted port must reach the transport and robots
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, "")), host, port


async def pin_destination(url: str, policy: AcquisitionPolicy, resolver: Resolver) -> PinnedTarget:
    canonical, host, port = permitted_url(url, policy)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        addresses = await resolver.resolve(host, port)
    else:
        addresses = (str(address),)
    if not addresses:
        raise AcquisitionError("dns_failure", Outcome.TRANSIENT_FAILURE)
    # A mixed public/private DNS answer is rejected in its entirety.
    validated = tuple(
        dict.fromkeys(
            public_address(value, allow_private=policy.grants.private_network)
            for value in addresses
        )
    )
    return PinnedTarget(canonical, host, port, validated[0], validated[1:])
