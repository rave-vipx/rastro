"""Studio web app: a thin Starlette layer over the public job services (P-008).

Routers live in ``routes_*`` modules; ``web`` holds the shared guard (loopback Host, exact
Origin, per-run token on every POST), bounded bodies and the Content-Security-Policy.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from rastro.domain.models import ConfigurationError
from rastro.studio import routes_dataset, routes_jobs, routes_repair, routes_run, routes_wizard
from rastro.studio.web import MAX_BODY, Studio

LOCAL_HOSTS = ("127.0.0.1", "localhost")
__all__ = ["LOCAL_HOSTS", "MAX_BODY", "create_app"]


def create_app(
    root: str | Path, *, allow_egress: bool = False, allow_env: Iterable[str] = ()
) -> tuple[Starlette, str]:
    """Build the app for ``root``; returns it with this run's token (for tests and the CLI).
    ``allow_egress`` and ``allow_env`` are the operator opt-ins for job-declared proxies."""
    folder = Path(root)
    if not folder.is_dir():
        raise ConfigurationError("Studio root must be an existing directory")
    studio = Studio(folder, allow_egress=allow_egress, allow_env=allow_env)
    routes = [
        *routes_jobs.routes(studio),
        *routes_wizard.routes(studio),
        *routes_run.routes(studio),
        *routes_repair.routes(studio),
        *routes_dataset.routes(studio),
    ]
    middleware = [Middleware(TrustedHostMiddleware, allowed_hosts=list(LOCAL_HOSTS))]
    return Starlette(routes=routes, middleware=middleware), studio.token
