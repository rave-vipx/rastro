"""Operator trust for models declared in job files (SEC-004/005/011, P-001).

A job file may declare a model, but only the operator running Rastro decides whether
page content may leave the machine or a secret may be read. ``allow_remote = true``
inside a job means "this job needs remote"; it is not consent. A job file can never make
Rastro start a program: a command model comes only from operator configuration
(``rastro.model(...)``, ``--model`` or ``RASTRO_MODEL``).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from rastro.domain.job_providers import CommandProvider, JobModelProvider
from rastro.domain.models import ConfigurationError
from rastro.domain.secret_env import check_secret_env_name

# Default ports of common local model servers (Ollama, LM Studio, vLLM, llama.cpp).
LOCAL_MODEL_PORTS = frozenset({11434, 1234, 8000, 8080})
_REMOTE_ENV = "RASTRO_ALLOW_REMOTE=1"


@dataclass(frozen=True)
class OperatorTrust:
    allow_remote: bool = False
    allowed_env: frozenset[str] = frozenset()

    def merged(self, other: OperatorTrust) -> OperatorTrust:
        return OperatorTrust(
            allow_remote=self.allow_remote or other.allow_remote,
            allowed_env=self.allowed_env | other.allowed_env,
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
