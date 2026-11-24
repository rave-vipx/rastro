"""Root-confined registry sharing; imported recipes never gain operator trust."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.operator_trust import operator_trust
from rastro.application.recipe_registry import (
    install_from_registry,
    publish_to_registry,
    search_registry,
)
from rastro.cli_hub import _parser_for
from rastro.domain.models import ConfigurationError
from rastro.studio import files
from rastro.studio.web import Refused, Studio, body, plain, reply, text


def _path(root: Path, raw: str) -> Path:
    """Check original path components before resolving, including dangling links."""
    rel = Path(raw)
    if (
        not raw
        or len(raw) > 1024
        or "\x00" in raw
        or "\\" in raw
        or rel.is_absolute()
        or ".." in rel.parts
    ):
        raise ConfigurationError("Paths must be relative to the Studio root")
    path = files._walk_no_links(root, rel)
    if not path.resolve().is_relative_to(root):
        raise ConfigurationError("Path must stay inside the Studio root")
    return path


def _registry(studio: Studio, data: dict[str, Any], *, publish: bool = False) -> Path | str:
    raw = text(data, "registry")
    if ":" in raw:
        url = urlsplit(raw)
        if (
            publish
            or url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or "?" in raw
            or "#" in raw
            or "\\" in raw
            or any(ord(char) <= 32 for char in raw)
        ):
            raise Refused(400, "invalid_registry_url")
        return raw
    path = _path(studio.root, raw)
    if (path.exists() and not path.is_dir()) or (not publish and not path.is_dir()):
        raise Refused(400, "registry_directory_required")
    return path


def _url_call(studio: Studio, operation: str, *args: Any, **kwargs: Any) -> Any:
    # The acquisition owner supplies all fetching and SSRF checks (ADR 0033).
    try:
        module = import_module("rastro.application.registry_fetch")
        with operator_trust(
            allow_private_network=studio.trust.get("allow_private_network", False),
            allow_ports=studio.trust.get("allow_ports", ()),
            ignore_robots=studio.trust.get("ignore_robots", ()),
        ):
            return getattr(module, operation)(*args, **kwargs)
    except ImportError:
        raise Refused(503, "registry_url_unavailable_install_http_extra") from None


def _install(studio: Studio, data: dict[str, Any]) -> dict[str, Any]:
    registry = _registry(studio, data)
    spec = text(data, "spec")
    job_path, _, job = studio.load(text(data, "file"))
    if job.recipe_memory is None:
        raise Refused(400, "recipe_memory_required")
    relative = job_path.parent.relative_to(studio.root) / job.recipe_memory
    memory = _path(studio.root, relative.as_posix())
    if not memory.parent.is_dir() or memory.is_dir():
        raise Refused(400, "invalid_recipe_memory")
    for suffix in ("-wal", "-shm", "-journal"):
        _path(studio.root, relative.as_posix() + suffix)
    repository = SQLiteRecipeRepository(memory)
    options: dict[str, Any] = {"at": datetime.now(UTC), "limits": job.limits}
    installed = (
        install_from_registry(registry, spec, repository, _parser_for, **options)
        if isinstance(registry, Path)
        else _url_call(
            studio, "install_from_registry_url", registry, spec, repository, _parser_for, **options
        )
    )
    stored = installed.imported.stored
    return {
        "ok": True,
        "id": installed.id,
        "version": installed.version,
        "recipe_version": stored.recipe.version,
        "state": str(stored.metadata.state),
        "reused": installed.imported.reused,
        "promoted": False,
        "trust": plain(installed.trust),
        "published_trust": plain(installed.published_trust),
    }


def routes(studio: Studio) -> list[Route]:
    async def search(request: Request) -> Response:
        data = await body(request)
        registry, query = _registry(studio, data), text(data, "query")

        def run() -> dict[str, Any]:
            entries = (
                search_registry(registry, query)
                if isinstance(registry, Path)
                else _url_call(studio, "search_registry_url", registry, query)
            )
            return {"ok": True, "entries": [plain(entry) for entry in entries]}

        return reply(await run_in_threadpool(run))

    async def install(request: Request) -> Response:
        return reply(await run_in_threadpool(_install, studio, await body(request)))

    async def publish(request: Request) -> Response:
        data = await body(request)
        registry = _registry(studio, data, publish=True)
        assert isinstance(registry, Path)
        bundle = _path(studio.root, text(data, "bundle"))
        if not bundle.is_dir():
            raise Refused(400, "bundle_directory_required")
        published = await run_in_threadpool(
            publish_to_registry, bundle, registry, _parser_for, today=datetime.now(UTC).date()
        )
        return reply({"ok": True, "entry": plain(published.entry), "pushed": False})

    return [
        studio.route("/api/registry/search", search, post=True),
        studio.route("/api/registry/install", install, post=True),
        studio.route("/api/registry/publish", publish, post=True),
    ]
