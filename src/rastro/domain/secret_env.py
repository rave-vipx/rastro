"""Which environment variables a job file may make Rastro read (SEC-004/005).

A job file is data written by anyone; it must not choose an arbitrary process secret
(say ``AWS_SECRET_ACCESS_KEY``) and send it to an endpoint. Names it references must
start with ``RASTRO_`` or be allow-listed by the operator.
"""

from __future__ import annotations

import re

from rastro.domain.models import ConfigurationError

JOB_ENV_NAME = re.compile(r"RASTRO_[A-Z0-9_]{1,64}")


def check_secret_env_name(name: str, allowed: frozenset[str]) -> str:
    """Return ``name`` if a job file may reference it, else raise ``ConfigurationError``."""
    if JOB_ENV_NAME.fullmatch(name) or name in allowed:
        return name
    raise ConfigurationError(
        f"Job files may read only RASTRO_* environment variables; {name} is not allowed",
        hint="Rename the variable to RASTRO_..., or allow it with --allow-env NAME "
        "or RASTRO_ALLOWED_ENV",
    )
