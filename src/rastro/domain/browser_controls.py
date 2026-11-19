"""Job-file acquisition settings: browser controls, HTTP options and per-host limits.

Each setting that needs a capability says so through ``requested_capabilities()``; the
capability gate decides whether the job's origin may use it. Settings never hold secrets:
a remote browser endpoint is named by an environment variable, and a persistent profile
by a short name the operator maps to a directory.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator

from rastro.domain.acquisition_base import ENVIRONMENT_NAME, exact_host
from rastro.domain.capability_grants import Capability
from rastro.domain.job_contract import RedactedContract
from rastro.domain.page_steps import WaitState
from rastro.domain.source_notes import BrowserFamily, HttpVersion

__all__ = ["BrowserControls", "HostLimits", "HttpOptions"]

_PROFILE_NAME = r"^[a-z][a-z0-9_-]{0,63}$"


class BrowserControls(RedactedContract):
    """How a browser source loads and observes its page."""

    wait_selector: StrictStr | None = Field(default=None, min_length=1, max_length=1024)
    wait_state: WaitState = "attached"
    network_idle: StrictBool = False
    disable_resources: StrictBool = True
    block_ads: StrictBool = False
    blocked_domains: tuple[str, ...] = Field(default=(), max_length=64)
    pierce_shadow: StrictBool = False
    screenshot: StrictBool = False
    capture_network: tuple[StrictStr, ...] = Field(default=(), max_length=8)
    """Path regular expressions; matching responses are captured."""
    session_profile: StrictStr | None = Field(default=None, pattern=_PROFILE_NAME)
    real_chrome: StrictBool = False
    cdp_url_env: StrictStr | None = Field(default=None, pattern=ENVIRONMENT_NAME)

    @field_validator("blocked_domains")
    @classmethod
    def _exact_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(exact_host(host) for host in value)

    @field_validator("capture_network")
    @classmethod
    def _compiled(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            if not 1 <= len(pattern) <= 256:
                raise ValueError("capture patterns must be 1 to 256 characters")
            try:
                re.compile(pattern)
            except re.error:
                raise ValueError("capture patterns must be valid regular expressions") from None
        return value

    def requested_capabilities(self) -> frozenset[Capability]:
        """The capabilities these controls need."""
        wanted: dict[Capability, bool] = {
            "screenshots": self.screenshot,
            "network_capture": bool(self.capture_network),
            "persistent_session": self.session_profile is not None,
            "real_chrome": self.real_chrome,
            "cdp": self.cdp_url_env is not None,
        }
        return frozenset(name for name, on in wanted.items() if on)


class HttpOptions(RedactedContract):
    """How the HTTP transport presents itself: browser impersonation and protocol version."""

    impersonate: Literal["off"] | BrowserFamily = "off"
    http_version: HttpVersion = "1.1"

    def requested_capabilities(self) -> frozenset[Capability]:
        """The capabilities these options need."""
        wanted: dict[Capability, bool] = {
            "impersonation": self.impersonate != "off",
            "http3": self.http_version == "3",
        }
        return frozenset(name for name, on in wanted.items() if on)


class HostLimits(RedactedContract):
    """Politeness for one host: spacing, concurrency and adaptive throttling."""

    min_interval_seconds: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=60)
    max_concurrency: StrictInt | None = Field(default=None, ge=1, le=32)
    autothrottle: StrictBool = False
