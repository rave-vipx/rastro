"""Studio web app: a thin Starlette layer over the public job services (P-008).

Routers live in ``routes_*`` modules; ``web`` holds the shared guard (loopback Host, exact
Origin, per-run token on every POST), bounded bodies and the Content-Security-Policy.
"""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from rastro.domain.models import ConfigurationError
from rastro.ports.model import ModelPort
from rastro.studio import (
    routes_ai,
    routes_dataset,
    routes_jobs,
    routes_repair,
    routes_run,
    routes_wizard,
)
from rastro.studio.web import MAX_BODY, Studio

LOCAL_HOSTS = ("127.0.0.1", "localhost")
__all__ = ["LOCAL_HOSTS", "MAX_BODY", "create_app"]


def create_app(
    root: str | Path, *, model: ModelPort | None = None, model_remote: bool = False
) -> tuple[Starlette, str]:
    """Build the app for ``root``; returns it with this run's token (for tests and the CLI).

    ``model`` enables "Ask AI" (any ``ModelPort``); ``model_remote`` records the explicit
    ``--allow-remote`` opt-in so the page can say that content may be sent off this machine.
    """
    folder = Path(root)
    if not folder.is_dir():
        raise ConfigurationError("Studio root must be an existing directory")
    studio = Studio(folder, model=model, model_remote=model_remote)
    routes = [
        *routes_jobs.routes(studio),
        *routes_wizard.routes(studio),
        *routes_run.routes(studio),
        *routes_repair.routes(studio),
        *routes_dataset.routes(studio),
        *routes_ai.routes(studio),
    ]
    middleware = [Middleware(TrustedHostMiddleware, allowed_hosts=list(LOCAL_HOSTS))]
    return Starlette(routes=routes, middleware=middleware), studio.token
