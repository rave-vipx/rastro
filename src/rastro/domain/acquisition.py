"""Trusted acquisition policy and transport-independent network contracts."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from rastro.domain.models import EGRESS_NAME, Contract, Outcome

_ENVIRONMENT_NAME = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"


def _exact_host(host: str) -> str:
    host = host.lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if len(host) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        ):
            raise ValueError("Use exact ASCII/Punycode hostnames or IP addresses") from None
        return host


class EgressProfile(Contract):
    """One administrator-declared HTTP(S) proxy. Credentials are environment references."""

    proxy_url: str = Field(min_length=1, max_length=2048)
    username_env: str | None = Field(default=None, pattern=_ENVIRONMENT_NAME)
    password_env: str | None = Field(default=None, pattern=_ENVIRONMENT_NAME)

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
        host = _exact_host(parsed.hostname)
        netloc = f"[{host}]" if ":" in host else host
        return f"{parsed.scheme.lower()}://{netloc}" + (f":{port}" if port else "")

    @model_validator(mode="after")
    def paired_credentials(self) -> Self:
        if self.password_env is not None and self.username_env is None:
            raise ValueError("A proxy password reference requires a username reference")
        return self


class AcquisitionPolicy(Contract):
    """An exact host allowlist asserts the caller's permission to acquire a source."""

    allowed_hosts: tuple[str, ...] = Field(min_length=1, max_length=64)
    allow_http: bool = False
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

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(_exact_host(host) for host in hosts))

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

    @model_validator(mode="after")
    def check_timeouts(self) -> Self:
        if self.request_timeout_seconds > self.operation_timeout_seconds:
            raise ValueError("Request timeout must not exceed the operation timeout")
        return self


@dataclass(frozen=True)
class PinnedTarget:
    url: str
    host: str
    port: int
    address: str


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
    headers: dict[str, str]
    body: bytes
    bytes_received: int


@dataclass(frozen=True)
class AcquisitionResult:
    url: str
    html: str | None
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


class AcquisitionError(Exception):
    """A safe failure category; never contains a raw URL or transport exception."""

    def __init__(self, code: str, outcome: Outcome) -> None:
        self.code = code
        self.outcome = outcome
        super().__init__(code)
