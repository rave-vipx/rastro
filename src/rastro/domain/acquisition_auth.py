"""Operator grants and the job-declared auth header of an acquisition policy (SEC-001..004)."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field, field_validator

from rastro.domain.acquisition_base import ENVIRONMENT_NAME, RedactedContract, exact_host

_HTTP_TOKEN = r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$"
# Never configurable: Rastro owns them, or they would let a header redirect the request.
_RESERVED_HEADERS = frozenset(
    {
        "host",
        "cookie",
        "set-cookie",
        "content-length",
        "transfer-encoding",
        "connection",
        "proxy-authorization",
        "proxy-connection",
        "keep-alive",
        "te",
        "trailer",
        "upgrade",
        "user-agent",
        "accept",
        "accept-language",
        "if-none-match",
        "if-modified-since",
    }
)


@dataclass(frozen=True)
class NetworkGrants:
    """Operator-only widenings of the policy (SEC-001/002/003). Never read from a job file."""

    private_network: bool = False
    ports: frozenset[int] = frozenset()
    ignore_robots: frozenset[str] = frozenset()


class AcquisitionAuth(RedactedContract):
    """Declares one request header whose value is read from an environment variable.

    The job names the variable; the secret itself is never part of the policy.
    """

    host: str
    header: str = Field(default="Authorization", pattern=_HTTP_TOKEN)
    value_env: str = Field(pattern=ENVIRONMENT_NAME)
    scheme: str | None = Field(default=None, pattern=_HTTP_TOKEN)

    @field_validator("host")
    @classmethod
    def normalize_host(cls, host: str) -> str:
        return exact_host(host)

    @field_validator("header")
    @classmethod
    def refuse_reserved(cls, header: str) -> str:
        if header.lower() in _RESERVED_HEADERS:
            raise ValueError("This header is set by Rastro and cannot carry credentials")
        return header
