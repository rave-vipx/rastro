"""Engine browser tools for an agent building a recipe (ADR 0035, ADR 0037).

Each tool opens the job source's page in one fresh guarded browser session, replays the
caller's page steps from the closed vocabulary (click, type, select, scroll, wait, follow),
reads the page and closes it: Rastro keeps no browser between calls. Requests pass the job's
allowed hosts, pinned DNS, SSRF checks, robots and request budget. Tools run as ``mcp``
input, so steps need ``page_actions``, screenshots ``screenshots`` and the response list
``network_capture``, each granted by the operator. Page text is hardened (hidden content
removed) and instruction-like text is flagged before it reaches the agent. There is no tool
that evaluates JavaScript, and there never will be.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP, Image
from pydantic import Field

from rastro.adapters.html_hardening import harden_html
from rastro.adapters.mcp_common import EXTRACT, FieldName, Path_, SourceIndex, Url, call
from rastro.adapters.page_markdown import MarkdownCondenser
from rastro.application.job_loader import load_job
from rastro.application.live_page import LivePage, load_live_page, parse_steps
from rastro.application.page_digest import digest_page
from rastro.domain.injection import CODE, instruction_like, record_flags
from rastro.domain.models import ConfigurationError, Limits, Source
from rastro.domain.page_steps import MAX_STEPS
from rastro.mcp_tools import _existing, bounded

MAX_TEXT_CHARS = 8_000
MAX_SCREENSHOT_BYTES = 4_000_000
PREVIEW_ROWS = 20
BODY_PREVIEW_CHARS = 1_000

Steps = Annotated[
    list[dict[str, Any]],
    Field(
        max_length=MAX_STEPS,
        description="Page steps run before reading, in order: click, type, select, scroll, "
        "wait or follow objects, as in a recipe's steps. Needs the page_actions grant.",
    ),
]
RecipeJson = Annotated[dict[str, Any], Field(max_length=20, description="A Rastro recipe object")]
Fields = Annotated[list[FieldName], Field(min_length=1, max_length=100)]


def _open(
    root: Path,
    path: str,
    source: int,
    url: str | None,
    steps: Sequence[Any] | None,
    **evidence: bool,
) -> LivePage:
    job = load_job(_existing(root, path))
    return load_live_page(job, source, url=url, steps=parse_steps(steps or ()), **evidence)


def _flags(parts: Mapping[str, str]) -> list[dict[str, object]]:
    return [
        {"part": name, "code": CODE, "rules": list(rules)}
        for name, text in parts.items()
        if (rules := instruction_like(text))
    ]


def _head(page: LivePage) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "data_trust": "untrusted_source_data",
        "url": Source(url=page.url).evidence_url,  # no query: it may carry a token
    }


def snapshot(root: Path, path: str, source: int, url: str | None, steps: Any) -> dict[str, Any]:
    page = _open(root, path, source, url, steps)
    text = MarkdownCondenser().condense(page.html, max_chars=MAX_TEXT_CHARS)
    outline = digest_page(page.html, limits=Limits())
    return {
        **_head(page),
        "text": text,
        "page_outline": outline,
        "hidden_removed": harden_html(page.html).removed,
        "flags": _flags({"text": text, "page_outline": outline}),
        "next": "Write a recipe and check it with browser_extract on the same page and steps",
    }


def extract(
    root: Path,
    path: str,
    source: int,
    url: str | None,
    steps: Any,
    recipe: Mapping[str, Any],
    fields: Sequence[str] | None,
) -> dict[str, Any]:
    from rastro.mcp_agent import parse_recipe
    from rastro.mcp_authoring import _schema
    from rastro.scrape import scrape

    parsed = parse_recipe(recipe)  # the untrusted-selector policy applies
    names = list(fields) if fields else list(getattr(parsed, "fields", {}) or ())
    if not names:
        raise ConfigurationError("Name the fields to extract", location="fields")
    page = _open(root, path, source, url, steps)
    if not page.html.lstrip().startswith("<"):
        raise ConfigurationError("The page is not an HTML document", location="url")
    result = scrape(page.html, _schema(names), recipe=parsed, url=page.url, repair="off")
    rows = [record.data.model_dump(mode="json") for record in result.records]
    filled = {name: sum(row.get(name) not in (None, "") for row in rows) for name in names}
    payload = {
        **_head(page),
        "status": str(result.status),
        "total": len(rows),
        "filled": filled,
        "preview": rows[:PREVIEW_ROWS],
        "flags": record_flags(rows[:PREVIEW_ROWS]),
        "issues": [issue.model_dump(mode="json") for issue in result.issues[:20]],
        "truncated": len(rows) > PREVIEW_ROWS,
    }
    return bounded(payload, max_chars=300)


def network(root: Path, path: str, source: int, url: str | None, steps: Any) -> dict[str, Any]:
    page = _open(root, path, source, url, steps, network=True)
    responses = []
    for item in page.responses:
        preview = item.body[: BODY_PREVIEW_CHARS * 4].decode("utf-8", errors="replace")
        preview = preview[:BODY_PREVIEW_CHARS]
        responses.append(
            {
                "host": item.host,
                "path": item.path,
                "method": item.method,
                "status": item.status,
                "media_type": item.media_type,
                "bytes": len(item.body),
                "body_preview": preview,
                "flags": _flags({"body_preview": preview}),
            }
        )
    payload = {
        **_head(page),
        "responses": responses,
        "next": 'A JSON recipe with capture = "<path regex>" reads one of these responses',
    }
    return bounded(payload, max_chars=BODY_PREVIEW_CHARS)


def screenshot(root: Path, path: str, source: int, url: str | None, steps: Any) -> Image:
    page = _open(root, path, source, url, steps, screenshot=True)
    if not page.screenshot:
        raise ConfigurationError("The browser returned no screenshot", location="url")
    if len(page.screenshot) > MAX_SCREENSHOT_BYTES:
        raise ConfigurationError("The screenshot is too large to return", location="url")
    return Image(data=page.screenshot, format="png")


def register(server: FastMCP, workspace: Path) -> None:
    @server.tool(annotations=EXTRACT)
    async def browser_snapshot(
        path: Path_, source: SourceIndex = 1, url: Url | None = None, steps: Steps | None = None
    ) -> dict[str, Any]:
        """Open a job source's page in a guarded browser, run steps, and return its visible
        text and outline (hidden content removed, instruction-like text flagged). url may
        name another page on the job's allowed hosts. Untrusted page data."""
        return await call(snapshot, workspace, path, source, url, steps)  # type: ignore[no-any-return]

    @server.tool(annotations=EXTRACT)
    async def browser_extract(
        path: Path_,
        recipe: RecipeJson,
        source: SourceIndex = 1,
        url: Url | None = None,
        steps: Steps | None = None,
        fields: Fields | None = None,
    ) -> dict[str, Any]:
        """Run a recipe once on the live page after steps: status, fill counts, a bounded
        preview, flags and issues. Stores nothing."""
        return await call(  # type: ignore[no-any-return]
            extract, workspace, path, source, url, steps, recipe, fields
        )

    @server.tool(annotations=EXTRACT)
    async def browser_network(
        path: Path_, source: SourceIndex = 1, url: Url | None = None, steps: Steps | None = None
    ) -> dict[str, Any]:
        """JSON responses the page loaded from allowed hosts (path without query, status,
        size, a bounded flagged preview). Needs the network_capture grant."""
        return await call(network, workspace, path, source, url, steps)  # type: ignore[no-any-return]

    @server.tool(annotations=EXTRACT)
    async def browser_screenshot(
        path: Path_, source: SourceIndex = 1, url: Url | None = None, steps: Steps | None = None
    ) -> Image:
        """A PNG of the page's viewport after steps. Needs the screenshots grant."""
        image: Image = await call(screenshot, workspace, path, source, url, steps)  # type: ignore[arg-type]
        return image
