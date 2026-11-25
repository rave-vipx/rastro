"""Guided create's "Use the API": a job that reads the JSON response the page loads.

The page is rendered once more in a capturing browser; the response named by host and path
becomes a JSON recipe with an anchored ``capture`` pattern, and the job renders the page in a
browser that captures only that path. The job URL stays the page, so robots, pacing and
provenance are the page's. Captured bodies are untrusted and never written to the job; a
response that no recipe can read is a refusal, never an empty job.
"""

from __future__ import annotations

import json

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.application.job_from_url import prepare_url_policy
from rastro.application.job_new import write_validated_job
from rastro.application.network_recipes import recipe_from_capture
from rastro.application.recipe_toml import replace_recipe
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.json import MISSING, resolve_pointer, strict_json
from rastro.domain.models import Limits
from rastro.domain.recipes import JsonRecipe
from rastro.ports.engine import CapturedResponse
from rastro.studio import files, wizard_more
from rastro.studio.web import Refused, Studio, beside, body, optional_text, reply, text

__all__ = ["field_types", "job_text", "routes"]


def _q(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def field_types(body: bytes, recipe: JsonRecipe) -> dict[str, str]:
    """A job field type per recipe field, from the values the captured records carry."""
    records = resolve_pointer(strict_json(body), recipe.record_path)
    rows = [r for r in records if isinstance(r, dict)] if isinstance(records, list) else []
    types = {}
    for name, pointer in recipe.fields.items():
        seen = [resolve_pointer(row, pointer) for row in rows] if isinstance(pointer, str) else []
        kinds = {type(v) for v in seen if v is not None and v is not MISSING}
        if kinds == {bool}:
            types[name] = "boolean"
        elif kinds == {int}:
            types[name] = "integer"
        elif kinds and kinds <= {int, float}:
            types[name] = "number"
        else:
            types[name] = "string"
    return types


def job_text(
    url: str,
    policy: AcquisitionPolicy,
    recipe: JsonRecipe,
    types: dict[str, str],
    *,
    dataset: str | None = None,
    identity: tuple[str, ...] = (),
    recipe_memory: str | None = None,
) -> str:
    """TOML for a job that renders ``url`` and reads the captured response ``recipe`` names."""
    assert recipe.capture is not None
    lines = [
        "# Created by Rastro Studio from the data this page loads. Preview it with",
        "# `rastro job run <this file>` (prints records, no writes).",
        "format_version = 1",
        *([f"recipe_memory = {_q(recipe_memory)}"] if recipe_memory else []),
        "",
        "[acquisition]",
        f"allowed_hosts = [{', '.join(_q(h) for h in policy.allowed_hosts)}]",
        *(["allow_http = true"] if policy.allow_http else []),
        'render = "browser"',
        "",
        "[acquisition.browser]",
        f"capture_network = [{_q(recipe.capture)}]",
        "",
        "[recipe]",
        'strategy = "json"',
        "",
    ]
    if dataset is not None:
        lines += ["[dataset]", 'name = "records"', f"path = {_q(dataset)}"]
        lines += [f"identity = [{', '.join(_q(n) for n in identity)}]", ""]
    lines += ["[[sources]]", 'kind = "url"', f"url = {_q(url)}", 'representation = "json"']
    for name in recipe.fields:
        lines += ["", "[[fields]]", f"name = {_q(name)}", f"type = {_q(types[name])}"]
    return replace_recipe("\n".join(lines) + "\n", recipe, source=None)


def _response(url: str, studio: Studio, host: str, path: str) -> CapturedResponse:
    for response in wizard_more._captured(url, studio):
        if response.host == host and response.path == path:
            return response
    raise Refused(409, "offer_not_seen")


def _recipe(response: CapturedResponse, names: tuple[str, ...]) -> JsonRecipe:
    from rastro.adapters.structured import JsonDiscovery

    found = recipe_from_capture(response, names, discovery=JsonDiscovery(), limits=Limits())
    if not found or not found[0].fields:
        raise Refused(409, "offer_not_readable")
    return found[0]


def routes(studio: Studio) -> list[Route]:
    async def use(request: Request) -> Response:
        data = await body(request)
        target = files.new_job_path(studio.root, text(data, "file"))
        url, host, path = text(data, "url"), text(data, "host"), text(data, "path")
        names = wizard_more.field_names(data)
        raw = data.get("identity", [])
        if not isinstance(raw, list) or not all(isinstance(n, str) for n in raw):
            raise Refused(400, "invalid_identity")
        dataset = beside(target, optional_text(data, "dataset"), "dataset")
        memory = beside(target, optional_text(data, "recipe_memory"), "recipe_memory")
        canonical, policy = prepare_url_policy(url)  # a bad URL fails before any render

        def write() -> str:
            response = _response(canonical, studio, host, path)
            recipe = _recipe(response, names)
            toml = job_text(
                canonical,
                policy,
                recipe,
                field_types(response.body, recipe),
                dataset=dataset,
                identity=tuple(n for n in raw if n in recipe.fields),
                recipe_memory=memory,
            )
            write_validated_job(target, toml)  # parsed first; never overwrites a file
            return files.digest(files.read_job_bytes(target))

        sha = await run_in_threadpool(write)
        rel = target.relative_to(studio.root).as_posix()
        return reply({"ok": True, "file": rel, "sha256": sha, "data_trust": wizard_more.UNTRUSTED})

    return [studio.route("/api/wizard/offers/use", use, post=True)]
