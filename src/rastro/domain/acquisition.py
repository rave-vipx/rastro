"""Trusted acquisition policy and transport-independent network contracts."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Self

from pydantic import Field, field_validator, model_validator

from rastro.domain.models import Contract, Outcome


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

    @field_validator("allowed_hosts")
    @classmethod
    def normalize_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for host in hosts:
            host = host.lower().rstrip(".")
            try:
                host = str(ipaddress.ip_address(host))
            except ValueError:
                if len(host) > 253 or not all(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in host.split(".")
                ):
                    raise ValueError("Use exact ASCII/Punycode hostnames or IP addresses") from None
            normalized.append(host)
        return tuple(dict.fromkeys(normalized))

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


class AcquisitionError(Exception):
    """A safe failure category; never contains a raw URL or transport exception."""

    def __init__(self, code: str, outcome: Outcome) -> None:
        self.code = code
        self.outcome = outcome
        super().__init__(code)
