"""Plain data for the Settings view: models, notification channels, schedule, proxies.

Everything here is a mapping from Rastro's own services to JSON-ready values. A secret is
never read into a reply: channels and proxies report only the environment variable names
that hold them.
"""

from __future__ import annotations

import socket
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from rastro.application.notifications import CHANNELS
from rastro.domain.capability_grants import CAPABILITIES, GRANT_ONLY, INPUT_ORIGINS
from rastro.domain.jobs import Job
from rastro.domain.schedule_words import describe_schedule, next_runs

# Required settings per channel; a secret may also live in another variable named by
# ``<NAME>_ENV`` (the operator must allow that name).
REQUIRED: dict[str, tuple[str, ...]] = {
    "email": ("RASTRO_NOTIFY_SMTP_HOST", "RASTRO_NOTIFY_EMAIL_FROM", "RASTRO_NOTIFY_EMAIL_TO"),
    "slack": ("RASTRO_NOTIFY_SLACK_WEBHOOK_URL",),
    "telegram": ("RASTRO_NOTIFY_TELEGRAM_TOKEN", "RASTRO_NOTIFY_TELEGRAM_CHAT_ID"),
    "webhook": ("RASTRO_NOTIFY_WEBHOOK_URL",),
}
PROXY_TIMEOUT = 3.0


def channel_rows(
    environ: Mapping[str, str], last: Mapping[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for channel in CHANNELS:
        names = list(REQUIRED[channel])
        missing = [n for n in names if not (environ.get(n, "").strip() or environ.get(f"{n}_ENV"))]
        rows.append(
            {
                "channel": channel,
                "configured": not missing,
                "env": names,
                "missing": missing,
                "last": last.get(channel),
            }
        )
    return rows


def schedule_row(job: Job, language: str, now: datetime | None = None) -> dict[str, Any]:
    spec = job.schedule
    if spec is None:
        return {"set": False}
    start = now or datetime.now(UTC)
    runs = next_runs(spec, start, timezone=job.region.timezone)
    return {
        "set": True,
        "words": describe_schedule(spec, language),
        "every": spec.every,
        "at": spec.at,
        "next": [run.isoformat() for run in runs],
    }


def proxy_rows(job: Job) -> list[dict[str, Any]]:
    """Egress profiles by name with host and port; credential variable names only."""
    profiles = job.acquisition.egress_profiles if job.acquisition else {}
    rows = []
    for name, profile in profiles.items():
        parts = urlsplit(profile.proxy_url)
        rows.append(
            {
                "name": name,
                "host": parts.hostname,
                "port": parts.port or (443 if parts.scheme == "https" else 80),
                "env": [e for e in (profile.username_env, profile.password_env) if e],
            }
        )
    return rows


def proxy_reachable(host: str, port: int) -> bool:
    """A plain TCP connect: no request and no credential is sent."""
    try:
        with socket.create_connection((host, port), timeout=PROXY_TIMEOUT):
            return True
    except OSError:
        return False


def switch_rows(
    granted: Mapping[Any, Collection[str]], locked: Mapping[Any, Collection[str]]
) -> list[dict[str, Any]]:
    """One row per untrusted origin: each capability with its state and whether it is locked."""
    return [
        {
            "origin": origin,
            "capabilities": [
                {
                    "name": name,
                    "on": name in granted.get(origin, ()) or name in locked.get(origin, ()),
                    "locked": name in locked.get(origin, ()),
                }
                for name in CAPABILITIES
                if name not in GRANT_ONLY
            ],
        }
        for origin in INPUT_ORIGINS[1:]
    ]
