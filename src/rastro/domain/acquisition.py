"""Trusted acquisition policy and transport-independent network contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from rastro.domain.acquisition_auth import AcquisitionAuth, NetworkGrants
from rastro.domain.acquisition_base import ENVIRONMENT_NAME, RedactedContract, exact_host
from rastro.domain.browser_controls import HostLimits
from rastro.domain.models import EGRESS_NAME, Outcome
from rastro.domain.source_notes import BlockedHint, RenderMode


class EgressProfile(RedactedContract):
    """One administrator-declared HTTP(S) proxy. Credentials are environment references."""

    proxy_url: str = Field(min_length=1, max_length=2048)
    username_env: str | None = Field(default=None, pattern=ENVIRONMENT_NAME)
    password_env: str | None = Field(default=None, pattern=ENVIRONMENT_NAME)

    @field_validator("proxy_url")
    @classmethod
    def normalize_proxy(cls, value: str) -> str:
        if any(ord(character) <= 32 or ord(character) == 127 for character in value):
            raise ValueError("Proxy URLs cannot contain whitespace or control characters")
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("Proxy URLs require a valid port") from None
        if parsed.netloc.endswith(":") or port == 0:
            raise ValueError("Proxy URLs require a valid port")
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
        ):
            raise ValueError(
                "Proxy URLs must be http(s)://host[:port] without credentials or paths"
            )
        host = exact_host(parsed.hostname)
        netloc = f"[{host}]" if ":" in host else host
        return f"{parsed.scheme.lower()}://{netloc}" + (f":{port}" if port else "")

    @model_validator(mode="after")
    def paired_credentials(self) -> Self:
        if self.password_env is not None and self.username_env is None:
            raise ValueError("A proxy password reference requires a username reference")
        return self


class AcquisitionPolicy(RedactedContract):
    """An exact host allowlist asserts the caller's permission to acquire a source."""

    allowed_hosts: tuple[str, ...] = Field(min_length=1, max_length=64)
    render: Literal["http", "browser", "stealth"] = "http"
    solve_challenges: bool = False
    robots: Literal["obey", "ignore"] = Field(
        default="obey",
        exclude_if=lambda value: value == "obey",
        description='"ignore" neither fetches nor enforces robots.txt for this job: the '
        "operator asserts permission. Jobs from agent tools, bundles, registry recipes and "
        "model output need the robots_ignore grant.",
    )
    allow_http: bool = False
    session: bool = False
    max_concurrency: int = Field(default=4, ge=1, le=32)
    max_requests: int = Field(default=30, ge=1, le=100)
    max_sources: int = Field(default=100, ge=1, le=1_000)
    min_interval_seconds: float = Field(default=1, ge=0, le=60, allow_inf_nan=False)
    request_timeout_seconds: float = Field(default=15, gt=0, le=120, allow_inf_nan=False)
    operation_timeout_seconds: float = Field(default=60, gt=0, le=600, allow_inf_nan=False)
    max_redirects: int = Field(default=5, ge=0, le=10)
    max_retries: int = Field(default=2, ge=0, le=3)
    backoff_seconds: float = Field(default=1, gt=0, le=30, allow_inf_nan=False)
    max_retry_delay_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    egress_profiles: dict[str, EgressProfile] = Field(default_factory=dict, max_length=16)
    rotate_egress: tuple[str, ...] = Field(
        default=(),
        max_length=16,
        exclude_if=lambda value: value == (),
        description="Egress profile names to rotate over, in order: each transfer of a source "
        'whose egress is "direct" uses the next one, and a proxy failure retries on the next.',
    )
    hosts: dict[str, HostLimits] = Field(
        default_factory=dict,
        max_length=64,
        exclude_if=lambda value: value == {},
        description="Pacing and concurrency for one allowed host, overriding the job-wide "
        "min_interval_seconds and max_concurrency for it.",
    )
    auth: tuple[AcquisitionAuth, ...] = Field(default=(), max_length=8)
    _grants: NetworkGrants = PrivateAttr(default_factory=NetworkGrants)

    @property
    def grants(self) -> NetworkGrants:
        return self._grants

    def with_grants(self, grants: NetworkGrants) -> Self:
        """A copy carrying the operator's grants; they are never part of the serialised policy."""
        copy = self.model_copy()
        copy._grants = grants
        return copy

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(exact_host(host) for host in hosts))

    @field_validator("egress_profiles", mode="before")
    @classmethod
    def expand_profiles(cls, profiles: Any) -> Any:
        if not isinstance(profiles, dict):
            return profiles
        for name in profiles:
            if not isinstance(name, str) or not re.fullmatch(EGRESS_NAME, name):
                raise ValueError("Egress names must be short lowercase identifiers")
            if name == "direct":
                raise ValueError("'direct' is the built-in egress and cannot be redefined")
        # A bare string is shorthand for a credential-free proxy URL.
        return {
            name: {"proxy_url": value} if isinstance(value, str) else value
            for name, value in profiles.items()
        }

    @field_validator("hosts")
    @classmethod
    def normalize_host_limits(cls, hosts: dict[str, HostLimits]) -> dict[str, HostLimits]:
        return {exact_host(host): limits for host, limits in hosts.items()}

    @model_validator(mode="after")
    def check_rotation_and_hosts(self) -> Self:
        names = self.rotate_egress
        if len(set(names)) != len(names) or not set(names) <= self.egress_profiles.keys():
            raise ValueError("rotate_egress lists distinct names of declared egress profiles")
        if not self.hosts.keys() <= set(self.allowed_hosts):
            raise ValueError("Each hosts entry must name an allowed host")
        return self

    @model_validator(mode="after")
    def check_auth(self) -> Self:
        seen: set[tuple[str, str]] = set()
        for entry in self.auth:
            key = (entry.host, entry.header.lower())
            if entry.host not in self.allowed_hosts or key in seen:
                raise ValueError("Each auth header needs an allowed host and may appear once")
            seen.add(key)
        return self

    @model_validator(mode="after")
    def check_timeouts(self) -> Self:
        if self.request_timeout_seconds > self.operation_timeout_seconds:
            raise ValueError("Request timeout must not exceed the operation timeout")
        return self


@dataclass(frozen=True)
class PinnedTarget:
    """A validated destination and remaining public addresses in DNS order."""

    url: str
    host: str
    port: int
    address: str
    alternates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProxyRoute:
    """A resolved proxy for one transfer; credentials never appear in its repr."""

    name: str
    url: str
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str] = field(repr=False)
    body: bytes
    bytes_received: int


@dataclass(frozen=True)
class AcquisitionResult:
    """``body`` is the decoded HTML or JSON document, or None when acquisition failed."""

    url: str
    body: str | None
    status: Outcome
    issue_code: str | None
    http_status: int | None = None
    requests: int = 0
    retries: int = 0
    redirects: int = 0
    bytes_received: int = 0
    network_seconds: float = 0
    acquisition_seconds: float = 0
    retry_after_seconds: float | None = None
    egress: str = "direct"
    render_seconds: float = 0
    robots_overridden: bool = False
    render: RenderMode = "http"
    proxy_rotation: bool = False
    proxies_used: int = 0  # distinct rotated profiles; names and counts only, never routes
    challenge_solved: bool = False
    blocked: BlockedHint | None = None
    raw_body: bytes | None = field(default=None, repr=False)  # set only by acquire_raw


class AcquisitionError(Exception):
    """A safe failure category; never contains a raw URL or transport exception.

    ``connect_failure`` proves connection setup failed before an HTTP exchange. ``requests``
    is how many transfers a browser let through before it failed (they still spend the budget).
    """

    def __init__(
        self, code: str, outcome: Outcome, *, connect_failure: bool = False, requests: int = 0
    ) -> None:
        self.code = code
        self.outcome = outcome
        self.connect_failure = connect_failure
        self.requests = requests
        super().__init__(code)
