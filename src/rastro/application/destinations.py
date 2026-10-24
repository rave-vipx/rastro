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


def public_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise AcquisitionError("destination_denied", Outcome.ACCESS_RESTRICTION) from None
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or "%" in value
        or (
            isinstance(address, ipaddress.IPv6Address)
            and (address.ipv4_mapped is not None or any(address in n for n in _TRANSITION_NETWORKS))
        )
    ):
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
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port != (443 if parsed.scheme == "https" else 80):
        raise AcquisitionError("port_not_allowed", Outcome.ACCESS_RESTRICTION)
    netloc = f"[{host}]" if ":" in host else host
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
    validated = tuple(public_address(value) for value in addresses)
    return PinnedTarget(canonical, host, port, validated[0])
