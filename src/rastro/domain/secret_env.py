"""Which environment variables a job file may make Rastro read (security audit H1).

A job file is configuration that may come from someone else. It can name the variable that
holds a proxy or model credential, but only one whose name the operator controls: the
``RASTRO_`` namespace, or a name the operator listed in ``RASTRO_ALLOWED_ENV`` or with
``--allow-env``. Otherwise a hostile job could name ``GITHUB_TOKEN`` and send it to its own
endpoint.
"""

from __future__ import annotations

import re

from rastro.domain.sources import ConfigurationError

_RASTRO_NAME = re.compile(r"RASTRO_[A-Z0-9_]{1,64}")


def check_secret_env_name(
    name: str, allowed: frozenset[str], *, location: str | None = None
) -> str:
    """Return ``name`` when the operator trusts it; the error never echoes the name."""
    if _RASTRO_NAME.fullmatch(name) or name in allowed:
        return name
    raise ConfigurationError(
        "A job file may only reference environment variables named RASTRO_* or listed by "
        "the operator in RASTRO_ALLOWED_ENV (comma-separated) or with --allow-env NAME",
        location=location,
    )
