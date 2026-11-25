"""Environment-sourced request headers declared in ``[[acquisition.auth]]``.

The job file names a host, a header and an environment variable; the variable name must
pass the same rule as every other secret reference. The value is read when a request is
built, sent only to that exact host over HTTPS (or loopback HTTP with the operator's
private-network grant), and never to robots.txt, a proxy or another host. It is held in
no policy, result, log or error: failures never echo the variable name or its value.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

from rastro.application.egress_trust import allowed_env_names
from rastro.application.operator_trust import current_trust
from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy
from rastro.domain.acquisition_auth import AcquisitionAuth, NetworkGrants
from rastro.domain.jobs import Job, UrlJobSource
from rastro.domain.models import ConfigurationError, Outcome
from rastro.domain.secret_env import check_secret_env_name

AuthHeaders = dict[str, dict[str, str]]


def _read(entry: AcquisitionAuth, environ: Mapping[str, str]) -> str | None:
    value = environ.get(entry.value_env, "")
    if not value or len(value) > 8192 or not all(32 <= ord(c) < 127 for c in value):
        return None  # empty, oversized, or any control or non-ASCII character
    if value != value.strip():
        return None
    return f"{entry.scheme} {value}" if entry.scheme else value


def resolve_auth(policy: AcquisitionPolicy, environ: Mapping[str, str] | None) -> AuthHeaders:
    """Host -> header -> value for this operation; ``auth_unavailable`` never echoes names."""
    env = os.environ if environ is None else environ
    resolved: AuthHeaders = {}
    for entry in policy.auth:
        value = _read(entry, env)
        if value is None:
            raise AcquisitionError("auth_unavailable", Outcome.PERMANENT_FAILURE)
        resolved.setdefault(entry.host, {})[entry.header] = value
    return resolved


def _loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def request_auth(
    auth: AuthHeaders, url: str, address: str, *, proxied: bool, grants: NetworkGrants
) -> dict[str, str]:
    """Headers for one request: only the exact host, only over a secure or loopback route."""
    parts = urlsplit(url)
    entries = auth.get((parts.hostname or "").lower())
    if not entries:
        return {}
    local = grants.private_network and not proxied and _loopback(address)
    if parts.scheme != "https" and not local:
        raise AcquisitionError("auth_requires_https", Outcome.ACCESS_RESTRICTION)
    return dict(entries)


def check_job_auth(
    job: Job,
    *,
    allow_env: Iterable[str] = (),
    allow_private_network: bool = False,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Refuse an unusable ``[[acquisition.auth]]`` before any request; errors never echo."""
    policy = job.acquisition
    if policy is None or not policy.auth:
        return
    env = os.environ if environ is None else environ
    if _renders(job):
        raise ConfigurationError(
            "auth headers are HTTP-engine only; remove [[acquisition.auth]] or render = http",
            location="acquisition.auth",
        )
    allowed = allowed_env_names(allow_env, env)
    local_ok = allow_private_network or current_trust().allow_private_network
    for index, entry in enumerate(policy.auth):
        where = f"acquisition.auth.{index}"
        check_secret_env_name(entry.value_env, allowed, location=f"{where}.value_env")
        if _read(entry, env) is None:
            raise ConfigurationError(
                "An [[acquisition.auth]] variable is missing, empty or not a valid header value",
                location=f"{where}.value_env",
            )
        for source in job.sources:
            parts = urlsplit(source.url) if isinstance(source, UrlJobSource) else None
            if (
                parts is not None
                and (parts.hostname or "").lower() == entry.host
                and parts.scheme != "https"
                and not (local_ok and _loopback_host(entry.host))
            ):
                raise ConfigurationError(
                    "auth headers are sent only over HTTPS (or loopback with "
                    "--allow-private-network)",
                    location=where,
                )


def _loopback_host(host: str) -> bool:
    return host == "localhost" or _loopback(host)


def _renders(job: Job) -> bool:
    assert job.acquisition is not None
    modes = {job.acquisition.render}
    modes |= {
        item.render
        for item in job.sources
        if isinstance(item, UrlJobSource) and "render" in item.model_fields_set
    }
    return bool(modes - {"http"}) or job.acquisition.solve_challenges
