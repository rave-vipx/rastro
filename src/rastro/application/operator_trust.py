"""Where operator trust comes from: environment, CLI flags, or an SDK call (SEC-004/005).

Sources combine: ``RASTRO_ALLOW_REMOTE=1`` and ``RASTRO_ALLOWED_ENV="A,B"`` in the
environment; a process-wide value set by the CLI (``--allow-remote``, ``--allow-env``); and
a scoped value set by SDK keywords such as ``run_job_file(..., allow_remote=True)``.
Nothing here lets a job file start a command (see ``rastro.domain.model_trust``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from rastro.domain.model_trust import OperatorTrust
from rastro.domain.models import ConfigurationError

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


def current_trust() -> OperatorTrust:
    from_env = OperatorTrust(
        allow_remote=os.environ.get("RASTRO_ALLOW_REMOTE", "").strip().lower() in _TRUE,
        allowed_env=env_names(os.environ.get("RASTRO_ALLOWED_ENV", "").split(",")),
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
def operator_trust(*, allow_remote: bool = False, allow_env: Iterable[str] = ()) -> Iterator[None]:
    """Scope an SDK caller's opt-in to the job functions called inside this block."""
    added = OperatorTrust(
        allow_remote=allow_remote is True,
        allowed_env=env_names(allow_env),
    )
    token = _SCOPED.set((_SCOPED.get() or OperatorTrust()).merged(added))
    try:
        yield
    finally:
        _SCOPED.reset(token)
