"""Operator trust for models declared in job files.

A job file may declare a model, but only the operator running Rastro decides whether
page content may leave the machine or a secret may be read. ``allow_remote = true``
inside a job means "this job needs remote"; it is not consent. A job file can never make
Rastro start a program: a command model comes only from operator configuration
(``rastro.model(...)``, ``--model`` or ``RASTRO_MODEL``).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlsplit

from rastro.domain.acquisition_auth import NetworkGrants
from rastro.domain.capability_grants import (
    INPUT_ORIGINS,
    Capability,
    CapabilityGrants,
    InputOrigin,
    default_grants,
)
from rastro.domain.job_providers import CommandProvider, JobModelProvider
from rastro.domain.models import ConfigurationError
from rastro.domain.secret_env import check_secret_env_name

# Default ports of common local model servers (Ollama, LM Studio, vLLM, llama.cpp).
LOCAL_MODEL_PORTS = frozenset({11434, 1234, 8000, 8080})
_REMOTE_ENV = "RASTRO_ALLOW_REMOTE=1"
STEALTH_ALIAS: frozenset[Capability] = frozenset({"stealth", "solve_challenges"})
"""What the deprecated stealth switch grants to every untrusted origin."""


@dataclass(frozen=True)
class OperatorTrust:
    """What the operator allowed for this process or scope; never read from a job file.

    ``operator_grants`` adds grant-only capabilities to the operator's own jobs;
    ``untrusted_capabilities`` grants capabilities to one untrusted origin each.
    ``allow_stealth`` is the deprecated switch: operator jobs no longer need it, and for
    untrusted origins it grants :data:`STEALTH_ALIAS`.
    """

    allow_remote: bool = False
    allowed_env: frozenset[str] = frozenset()
    allow_stealth: bool = False
    allow_private_network: bool = False
    allowed_ports: frozenset[int] = frozenset()
    ignore_robots: frozenset[str] = frozenset()
    operator_grants: frozenset[Capability] = frozenset()
    untrusted_capabilities: Mapping[InputOrigin, frozenset[Capability]] = field(
        default_factory=lambda: MappingProxyType({}), hash=False
    )

    def __post_init__(self) -> None:
        frozen = {origin: frozenset(c) for origin, c in self.untrusted_capabilities.items()}
        object.__setattr__(self, "untrusted_capabilities", MappingProxyType(frozen))

    def network_grants(self) -> NetworkGrants:
        return NetworkGrants(self.allow_private_network, self.allowed_ports, self.ignore_robots)

    def capability_grants(self) -> CapabilityGrants:
        """The capabilities each origin may use; ``ConfigurationError`` for unknown names."""
        alias = STEALTH_ALIAS if self.allow_stealth else frozenset()
        untrusted = dict(self.untrusted_capabilities)  # an "operator" key is refused below
        for origin in INPUT_ORIGINS[1:]:
            untrusted[origin] = untrusted.get(origin, frozenset()) | alias
        return default_grants(operator_grants=self.operator_grants, untrusted=untrusted)

    def merged(self, other: OperatorTrust) -> OperatorTrust:
        origins = {*self.untrusted_capabilities, *other.untrusted_capabilities}
        return OperatorTrust(
            allow_private_network=self.allow_private_network or other.allow_private_network,
            allowed_ports=self.allowed_ports | other.allowed_ports,
            ignore_robots=self.ignore_robots | other.ignore_robots,
            allow_remote=self.allow_remote or other.allow_remote,
            allow_stealth=self.allow_stealth or other.allow_stealth,
            allowed_env=self.allowed_env | other.allowed_env,
            operator_grants=self.operator_grants | other.operator_grants,
            untrusted_capabilities={
                origin: self.untrusted_capabilities.get(origin, frozenset())
                | other.untrusted_capabilities.get(origin, frozenset())
                for origin in origins
            },
        )


def check_job_model(spec: JobModelProvider, trust: OperatorTrust) -> None:
    """Raise ``ConfigurationError`` unless the operator allows this job-declared model."""
    if isinstance(spec, CommandProvider):
        raise ConfigurationError(
            "A job file cannot make Rastro start a program; configure a command model as "
            "the operator instead",
            location="model",
            hint="Use rastro.model('command:...') in Python, --model on the CLI or the "
            "RASTRO_MODEL environment variable",
        )
    for key in ("base_url_env", "api_key_env"):
        env_name = getattr(spec, key, None)
        if env_name is not None:
            check_secret_env_name(env_name, trust.allowed_env, location=f"model.{key}")
    if trust.allow_remote:
        return
    if spec.base_url_env is not None or getattr(spec, "allow_remote", False):
        raise _refused("send page content to a remote model", "--allow-remote")
    if not _loopback(spec.base_url) or urlsplit(spec.base_url).port not in LOCAL_MODEL_PORTS:
        ports = ", ".join(str(port) for port in sorted(LOCAL_MODEL_PORTS))
        raise _refused(f"use a model endpoint other than loopback ports {ports}", "--allow-remote")


def _loopback(url: str) -> bool:
    try:
        address = ipaddress.ip_address(urlsplit(url).hostname or "")
    except ValueError:
        return False
    return address.is_loopback


def _refused(action: str, flag: str, env: str = _REMOTE_ENV) -> ConfigurationError:
    keyword = flag.removeprefix("--").replace("-", "_")
    return ConfigurationError(
        f"This job file asks Rastro to {action}; only the operator can allow that "
        f"with {flag}, {keyword}=True or {env}",
        location="model",
        hint=f"Pass {flag} to rastro job/mcp/studio, {keyword}=True to the SDK, or set {env}",
    )
