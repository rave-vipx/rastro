"""Operator opt-in for job-declared egress proxies (security audit H1).

A job file is configuration, not a trust decision. A proxy it declares sees every request
and receives the proxy credentials, so it is used only when the operator opts in
(``allow_egress=True``, ``--allow-egress`` or ``RASTRO_ALLOW_EGRESS=1``), and its credential
references must pass :func:`check_secret_env_name`.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

from rastro.application.operator_trust import EGRESS_ENV, env_flag, granted_env
from rastro.domain.jobs import Job
from rastro.domain.models import ConfigurationError
from rastro.domain.secret_env import check_secret_env_name


def allowed_env_names(
    allow_env: Iterable[str] = (), environ: Mapping[str, str] | None = None
) -> frozenset[str]:
    """The operator allow-list: ``allow_env``, the comma list in ``RASTRO_ALLOWED_ENV`` and
    names granted by ``--allow-env`` or an SDK ``operator_trust`` scope."""
    env = os.environ if environ is None else environ
    listed = (name.strip() for name in env.get("RASTRO_ALLOWED_ENV", "").split(","))
    return frozenset(name for name in (*allow_env, *listed) if name) | granted_env()


def check_job_egress(
    job: Job,
    *,
    allow_egress: bool = False,
    allow_env: Iterable[str] = (),
    environ: Mapping[str, str] | None = None,
) -> None:
    """Refuse a job-declared proxy without operator opt-in, or one naming an untrusted secret."""
    profiles = job.acquisition.egress_profiles if job.acquisition is not None else {}
    if not profiles:
        return
    env = os.environ if environ is None else environ
    if not (allow_egress or env_flag(EGRESS_ENV, env)):
        raise ConfigurationError(
            "This job declares egress proxies, which need operator opt-in: if you trust "
            "the job, pass --allow-egress (allow_egress=True in Python) or set "
            "RASTRO_ALLOW_EGRESS=1",
            location="acquisition.egress_profiles",
        )
    allowed = allowed_env_names(allow_env, env)
    for name, profile in sorted(profiles.items()):
        for key in ("username_env", "password_env"):
            value = getattr(profile, key)
            if value is not None:
                check_secret_env_name(
                    value, allowed, location=f"acquisition.egress_profiles.{name}.{key}"
                )
