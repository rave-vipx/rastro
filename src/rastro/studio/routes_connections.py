"""Settings routes beyond one job's recipe: the AI model, notification channels, the
schedule, proxies, robots and per-origin capability switches.

Studio keeps no secret. Channels and proxies read their credentials from the operator's
environment; a reply names the variables and never their values. A model switch, a
capability switch and a channel test are grants or actions of this Studio process only.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.application.egress_trust import allowed_env_names
from rastro.application.model_detect import detect_local_models
from rastro.application.notifications import CHANNELS, deliver, test_message
from rastro.application.operator_trust import current_trust, set_process_trust
from rastro.cli_notify import notifiers_from_env
from rastro.domain.capability_grants import (
    CAPABILITIES,
    GRANT_ONLY,
    INPUT_ORIGINS,
    Capability,
    InputOrigin,
)
from rastro.domain.model_trust import OperatorTrust
from rastro.domain.models import ConfigurationError
from rastro.domain.schedule_words import parse_schedule_words
from rastro.model_factory import is_remote, model
from rastro.studio import settings_data as data_
from rastro.studio.settings import save_robots, save_schedule
from rastro.studio.web import Refused, Studio, body, flag, optional_text, reply, text

LANGUAGES = ("en", "es")


def routes(studio: Studio) -> list[Route]:
    last: dict[str, dict[str, Any]] = {}
    session_grants: dict[InputOrigin, set[Capability]] = {}
    base = current_trust()  # what the command line and environment already grant

    def language(data: dict[str, Any]) -> str:
        value = optional_text(data, "language") or "en"
        if value not in LANGUAGES:
            raise Refused(400, "invalid_language")
        return value

    async def models(request: Request) -> Response:
        found = await run_in_threadpool(detect_local_models)
        current = studio.model
        return reply(
            {
                "detected": [{**d.model_dump(), "cost": "free"} for d in found],
                "current": None if current is None else {"remote": studio.model_remote},
                "env_names": sorted(allowed_env_names(studio.trust["allow_env"])),
            }
        )

    async def choose_model(request: Request) -> Response:
        data = await body(request)
        remote = data.get("kind") == "remote"
        if remote:
            options = _remote_options(studio, data)
            if not flag(data, "consent"):
                raise Refused(400, "consent_required")
            built = model("http", **options, allow_remote=True)
        else:
            wanted = text(data, "spec")
            found = await run_in_threadpool(detect_local_models)
            if wanted not in {d.spec for d in found}:  # only what this machine is running
                raise Refused(400, "model_not_detected")
            built = model(wanted)
        studio.model, studio.model_remote = built, is_remote(built)
        cost = options["cost_per_call"] if remote and "cost_per_call" in options else None
        return reply({"ok": True, "remote": studio.model_remote, "cost": cost or "free"})

    async def channels(request: Request) -> Response:
        return reply({"channels": data_.channel_rows(os.environ, last)})

    async def test_channel(request: Request) -> Response:
        name = text(await body(request), "channel")
        if name not in CHANNELS:
            raise Refused(400, "invalid_channel")

        def send() -> dict[str, Any]:
            when = datetime.now(UTC).isoformat(timespec="seconds")
            try:
                failures = deliver(notifiers_from_env([name]), test_message())
            except ConfigurationError as error:
                return {"sent": False, "reason": str(error), "at": when}
            return {
                "sent": not failures,
                "reason": failures[0][1] if failures else None,
                "at": when,
            }

        last[name] = await run_in_threadpool(send)
        return reply({"channel": name, **last[name]})

    async def schedule(request: Request) -> Response:
        _, _, job = studio.load(request.query_params.get("file", ""))
        return reply(data_.schedule_row(job, request.query_params.get("language", "en")))

    async def schedule_preview(request: Request) -> Response:
        data = await body(request)
        spec = parse_schedule_words(text(data, "words"))
        _, _, job = studio.load(text(data, "file"))
        view = data_.schedule_row(job.model_copy(update={"schedule": spec}), language(data))
        return reply(view)

    async def schedule_save(request: Request) -> Response:
        data = await body(request)
        path = studio.job_path(text(data, "file"))
        studio.check_digest(path, data)
        spec = parse_schedule_words(text(data, "words"))
        sha = await run_in_threadpool(
            save_schedule, path, text(data, "sha256"), spec.every, spec.at
        )
        return reply({"ok": True, "sha256": sha})

    async def proxies(request: Request) -> Response:
        _, _, job = studio.load(request.query_params.get("file", ""))
        return reply(
            {"proxies": data_.proxy_rows(job), "allowed": bool(studio.trust["allow_egress"])}
        )

    async def test_proxy(request: Request) -> Response:
        data = await body(request)
        if not studio.trust["allow_egress"]:
            raise Refused(403, "egress_not_granted")
        _, _, job = studio.load(text(data, "file"))
        row = next((r for r in data_.proxy_rows(job) if r["name"] == text(data, "name")), None)
        if row is None:
            raise Refused(404, "unknown_proxy")
        ok = await run_in_threadpool(data_.proxy_reachable, row["host"], row["port"])
        return reply({"name": row["name"], "reachable": ok})

    async def robots(request: Request) -> Response:
        data = await body(request)
        mode = text(data, "mode")
        if mode not in {"obey", "ignore"}:
            raise Refused(400, "invalid_mode")
        path = studio.job_path(text(data, "file"))
        studio.check_digest(path, data)
        sha = await run_in_threadpool(save_robots, path, text(data, "sha256"), mode)  # type: ignore[arg-type]
        return reply({"ok": True, "sha256": sha, "robots": mode})

    def grants_view() -> dict[str, Any]:
        locked = {o: frozenset(base.untrusted_capabilities.get(o, ())) for o in INPUT_ORIGINS[1:]}
        return {
            "origins": data_.switch_rows(
                {o: frozenset(v) for o, v in session_grants.items()}, locked
            )
        }

    async def grants(request: Request) -> Response:
        return reply(grants_view())

    async def set_grant(request: Request) -> Response:
        data = await body(request)
        origin, name, on = text(data, "origin"), text(data, "capability"), flag(data, "on")
        if origin not in INPUT_ORIGINS[1:] or name not in CAPABILITIES or name in GRANT_ONLY:
            raise Refused(400, "invalid_grant")
        held = session_grants.setdefault(origin, set())
        (held.add if on else held.discard)(name)
        extra = OperatorTrust(
            untrusted_capabilities={o: frozenset(v) for o, v in session_grants.items()}
        )
        set_process_trust(base.merged(extra))
        return reply(grants_view())

    return [
        studio.route("/api/models", models),
        studio.route("/api/models", choose_model, post=True),
        studio.route("/api/channels", channels),
        studio.route("/api/channels/test", test_channel, post=True),
        studio.route("/api/schedule", schedule),
        studio.route("/api/schedule/preview", schedule_preview, post=True),
        studio.route("/api/schedule/save", schedule_save, post=True),
        studio.route("/api/proxies", proxies),
        studio.route("/api/proxies/test", test_proxy, post=True),
        studio.route("/api/robots", robots, post=True),
        studio.route("/api/grants", grants),
        studio.route("/api/grants", set_grant, post=True),
    ]


def _remote_options(studio: Studio, data: dict[str, Any]) -> dict[str, Any]:
    """A connected provider: model, https base URL, key variable the operator allowed."""
    options: dict[str, Any] = {"model": text(data, "model"), "base_url": text(data, "base_url")}
    if not options["base_url"].startswith("https://"):
        raise Refused(400, "https_required")
    key = optional_text(data, "api_key_env")
    if key is not None:
        if key not in allowed_env_names(studio.trust["allow_env"]):
            raise Refused(400, "env_name_not_allowed")
        options["api_key_env"] = key
    cost = data.get("cost_per_call")
    if cost is not None:
        if isinstance(cost, bool) or not isinstance(cost, int | float) or not 0 < cost <= 100:
            raise Refused(400, "invalid_cost_per_call")
        options["cost_per_call"] = float(cost)
    return options
