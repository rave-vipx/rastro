"""Where operator trust comes from: environment, CLI flags, or an SDK call (SEC-004/005).

Sources combine: ``RASTRO_ALLOW_REMOTE=1`` (``1``, ``true`` or ``yes``, as every
``RASTRO_ALLOW_*`` flag; see :func:`env_flag`) and ``RASTRO_ALLOWED_ENV="A,B"`` in the
environment; a process-wide value set by the CLI (``--allow-remote``, ``--allow-env``); and
a scoped value set by SDK keywords such as ``run_job_file(..., allow_remote=True)``.
The network widenings (``--allow-private-network``, ``--allow-port``, ``--ignore-robots`` and
``RASTRO_ALLOW_PRIVATE_NETWORK``, ``RASTRO_ALLOWED_PORTS``, ``RASTRO_IGNORE_ROBOTS``) come the
same three ways and can never be set from a job file (SEC-001/002/003).
Nothing here lets a job file start a command (see ``rastro.domain.model_trust``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from rastro.domain.acquisition_base import exact_host
from rastro.domain.model_trust import OperatorTrust
from rastro.domain.models import ConfigurationError

REMOTE_ENV = "RASTRO_ALLOW_REMOTE"
EGRESS_ENV = "RASTRO_ALLOW_EGRESS"
PRIVATE_NETWORK_ENV = "RASTRO_ALLOW_PRIVATE_NETWORK"
_TRUE = frozenset({"1", "true", "yes"})
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_SCOPED: ContextVar[OperatorTrust | None] = ContextVar("operator_trust", default=None)
_process = OperatorTrust()


def env_names(names: Iterable[str]) -> frozenset[str]:
    if isinstance(names, str):
        raise ConfigurationError("Pass allowed environment names as a list")
    cleaned = frozenset(name.strip() for name in names if name.strip())
    if not all(_ENV_NAME.fullmatch(name) for name in cleaned):
        raise ConfigurationError("Allowed environment names must be variable names")
    return cleaned


def env_ports(values: Iterable[int | str]) -> frozenset[int]:
    """Extra ports the operator grants: integers 1..65535, otherwise ``ConfigurationError``."""
    if isinstance(values, str):
        raise ConfigurationError("Pass allowed ports as a list")
    ports: set[int] = set()
    for value in values:
        text = value.strip() if isinstance(value, str) else value
        if text == "":
            continue
        if isinstance(text, bool) or not isinstance(text, int | str):
            raise ConfigurationError("Allowed ports must be integers from 1 to 65535")
        if isinstance(text, str):
            text = int(text) if text.isascii() and text.isdigit() else 0
        if not 1 <= text <= 65_535:
            raise ConfigurationError("Allowed ports must be integers from 1 to 65535")
        ports.add(text)
    return frozenset(ports)


def env_hosts(values: Iterable[str]) -> frozenset[str]:
    """Exact hostnames (no wildcards) for which robots.txt is not enforced."""
    if isinstance(values, str):
        raise ConfigurationError("Pass hostnames as a list")
    try:
        return frozenset(exact_host(host.strip()) for host in values if host.strip())
    except ValueError:
        raise ConfigurationError("Robots overrides must be exact hostnames, not patterns") from None


def env_flag(name: str, environ: Mapping[str, str] | None = None) -> bool:
    """One reading for every boolean opt-in such as ``RASTRO_ALLOW_REMOTE``: 1, true or yes
    (any case, surrounding spaces ignored); anything else is off."""
    env = os.environ if environ is None else environ
    return env.get(name, "").strip().lower() in _TRUE


def current_trust() -> OperatorTrust:
    from_env = OperatorTrust(
        allow_remote=env_flag(REMOTE_ENV),
        allow_stealth=env_flag("RASTRO_ALLOW_STEALTH"),
        allowed_env=env_names(os.environ.get("RASTRO_ALLOWED_ENV", "").split(",")),
        allow_private_network=env_flag(PRIVATE_NETWORK_ENV),
        allowed_ports=env_ports(os.environ.get("RASTRO_ALLOWED_PORTS", "").split(",")),
        ignore_robots=env_hosts(os.environ.get("RASTRO_IGNORE_ROBOTS", "").split(",")),
    )
    return from_env.merged(_process).merged(_SCOPED.get() or OperatorTrust())


def granted_env() -> frozenset[str]:
    """Secret names the operator allowed through the CLI or an SDK scope (not the env)."""
    return _process.allowed_env | (_SCOPED.get() or OperatorTrust()).allowed_env


def set_process_trust(trust: OperatorTrust) -> OperatorTrust:
    """Set the CLI-level trust; returns the previous value so callers can restore it."""
    global _process
    previous, _process = _process, trust
    return previous


@contextmanager
def operator_trust(
    *,
    allow_remote: bool = False,
    allow_stealth: bool = False,
    allow_env: Iterable[str] = (),
    allow_private_network: bool = False,
    allow_ports: Iterable[int] = (),
    ignore_robots: Iterable[str] = (),
) -> Iterator[None]:
    """Scope an SDK caller's opt-in to the job functions called inside this block."""
    added = OperatorTrust(
        allow_remote=allow_remote is True,
        allow_stealth=allow_stealth is True,
        allowed_env=env_names(allow_env),
        allow_private_network=allow_private_network is True,
        allowed_ports=env_ports(allow_ports),
        ignore_robots=env_hosts(ignore_robots),
    )
    token = _SCOPED.set((_SCOPED.get() or OperatorTrust()).merged(added))
    try:
        yield
    finally:
        _SCOPED.reset(token)
