"""The page steps a recipe replays (often recorded by the AI's exploration): list and delete.

Steps are listed per recipe (the job recipe, then each source's own) as their plain fields.
Deleting one rewrites only that recipe's tables, re-validates the job and writes it only if
the file still matches the digest the page saw. Exploring again is the guided create's
``/api/wizard/explore``; nothing here calls a model or fetches a page.
"""

from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from rastro.application.atomic_files import write_atomic
from rastro.application.recipe_toml import replace_recipe
from rastro.domain.jobs import Job, parse_job
from rastro.studio import files
from rastro.studio.web import Refused, Studio, body, integer, reply, text

__all__ = ["routes", "step_rows"]


def _recipes(job: Job) -> list[tuple[int | None, Any]]:
    declared = [(None, job.recipe), *((i, s.recipe) for i, s in enumerate(job.sources))]
    return [(index, recipe) for index, recipe in declared if getattr(recipe, "steps", ())]


def step_rows(job: Job) -> list[dict[str, Any]]:
    """One row per step: which recipe (``source`` None is the job's), its place and fields."""
    return [
        {"source": source, "index": index, **step.model_dump(mode="json", exclude_none=True)}
        for source, recipe in _recipes(job)
        for index, step in enumerate(recipe.steps)
    ]


def _without(raw: bytes, job: Job, source: int | None, index: int) -> str:
    recipe = dict(_recipes(job)).get(source)
    if recipe is None or not 0 <= index < len(recipe.steps):
        raise Refused(404, "unknown_step")
    kept = recipe.steps[:index] + recipe.steps[index + 1 :]
    smaller = type(recipe).model_validate({**recipe.model_dump(), "steps": kept})
    new_text = replace_recipe(raw.decode("utf-8"), smaller, source=source)
    parse_job(new_text, format="toml")  # the edited file must still be a valid job
    return new_text


def routes(studio: Studio) -> list[Route]:
    async def listed(request: Request) -> Response:
        _, sha, job = studio.load(text(await body(request), "file"))
        return reply(
            {"ok": True, "sha256": sha, "steps": step_rows(job), "explore": bool(job.acquisition)}
        )

    async def delete(request: Request) -> Response:
        data = await body(request)
        path, _, job = studio.load(text(data, "file"))
        if path.suffix != ".toml":
            raise Refused(400, "toml_job_required")
        studio.check_digest(path, data)
        source = None if data.get("source") is None else integer(data, "source", 0, 999)
        index = integer(data, "index", 0, 99)

        def write() -> tuple[str, list[dict[str, Any]]]:
            new_text = _without(files.read_job_bytes(path), job, source, index)
            write_atomic(path, new_text.encode("utf-8"), expected_digest=text(data, "sha256"))
            return files.digest(new_text.encode("utf-8")), step_rows(
                parse_job(new_text, format="toml")
            )

        sha, rows = await run_in_threadpool(write)
        return reply({"ok": True, "sha256": sha, "steps": rows})

    return [
        studio.route("/api/steps", listed, post=True),
        studio.route("/api/steps/delete", delete, post=True),
    ]
