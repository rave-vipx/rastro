"""Agent-in-the-loop MCP tools through the real protocol (AGENT-002, AGENT-003, REPAIR-001-005,
INT-001, QUAL-006).

The MCP client's own model plays the repair proposer: a synthetic job board breaks, the agent
reads repair_context, proposes recipes and judges meaning; Rastro gates and stores them.
No model is called here; the "agent" is the test itself.
"""

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp.shared.memory import create_connected_server_and_client_session

from rastro.adapters.mcp_server import build_server

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

CARD = (
    '<article class="card" data-id="job-{n}"><h2 class="{cls}">Synthetic role {n}</h2>'
    '<span class="city">Synthetic city {n}</span></article>'
)
JOB = """format_version = 1
recipe_memory = "recipes.sqlite"

[dataset]
name = "jobs"
path = "jobs.sqlite"
identity = ["job_id"]

[[sources]]
kind = "file"
path = "board.html"
url = "https://example.test/jobs"

[sources.recipe]
version = 1
strategy = "css"
record_selector = "article.card"

[sources.recipe.fields]
job_id = "::attr(data-id)"
title = "h2.title::text"

[[fields]]
name = "job_id"
type = "string"

[[fields]]
name = "title"
type = "string"
"""
ENVELOPE = {"error", "message", "location", "hint"}
RIGHT = {
    "strategy": "css",
    "record_selector": "article.card",
    "fields": {"job_id": "::attr(data-id)", "title": "h2::text"},
}
WRONG = {**RIGHT, "fields": {"job_id": "::attr(data-id)", "title": "span.city::text"}}


def board(title_class: str = "title", wrap: bool = False) -> str:
    cards = "".join(CARD.format(n=n, cls=title_class) for n in range(1, 6))
    body = f"<section>{cards}</section>" if wrap else cards
    return f"<!doctype html><html><body><main>{body}</main></body></html>"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "board.html").write_text(board(), encoding="utf-8")
    (tmp_path / "jobs" / "job.toml").write_text(JOB, encoding="utf-8")
    return tmp_path


async def call(root: Path, tool: str, **arguments: Any) -> tuple[bool, Any]:
    async with create_connected_server_and_client_session(build_server(root)._mcp_server) as client:
        result = await client.call_tool(tool, arguments)
    return result.isError, json.loads(result.content[0].text)  # type: ignore[union-attr]


async def broken(root: Path) -> None:
    assert not (await call(root, "job_load", path="jobs/job.toml"))[0]
    (root / "jobs" / "board.html").write_text(board("new-title", wrap=True), encoding="utf-8")


@pytest.mark.asyncio
async def test_repair_context_gives_a_model_what_it_needs(workspace: Path) -> None:
    """AGENT-002: failing recipe, fields, last success values, current issues, outline."""
    await broken(workspace)
    memory = workspace / "jobs" / "recipes.sqlite"
    before = memory.stat().st_mtime_ns
    failed, context = await call(workspace, "repair_context", path="jobs/job.toml", source=1)
    assert not failed, context
    assert context["data_trust"] == "untrusted_source_data"
    assert context["recipe"]["fields"]["title"] == "h2.title::text"
    assert [f["name"] for f in context["fields"]] == ["job_id", "title"]
    assert context["current"]["status"] != "success" and context["current"]["issues"]
    assert context["last_success_sample"][0]["title"] == "Synthetic role 1"
    outline = context["page_outline"]
    assert outline.startswith("UNTRUSTED PAGE OUTLINE")
    assert "article.card x5" in outline and "h2.new-title" in outline
    assert memory.stat().st_mtime_ns == before


@pytest.mark.asyncio
async def test_repair_context_before_any_load_creates_no_store(workspace: Path) -> None:
    failed, error = await call(workspace, "repair_context", path="jobs/job.toml", source=1)
    assert failed and set(error) == ENVELOPE and error["error"] == "configuration_error"
    assert not (workspace / "jobs" / "recipes.sqlite").exists()


@pytest.mark.asyncio
async def test_a_correct_proposal_is_stored_reviewed_and_promoted(workspace: Path) -> None:
    """REPAIR-002/003/005: the agent's recipe passes the gates, waits for promotion, is reused
    when proposed again, and its meaning can be checked side by side before promotion."""
    await broken(workspace)
    failed, report = await call(
        workspace,
        "recipe_propose",
        path="jobs/job.toml",
        source=1,
        recipe=RIGHT,
        reason="Titles moved to h2.new-title",
    )
    assert not failed, report
    assert report["decision"] == "quarantine"
    (comparison,) = report["sources"][0]["comparisons"]
    assert comparison["evaluation"]["reasons"] == ["automatic_promotion_disabled"]
    assert report["sources"][0]["promoted_version"] is None
    assert comparison["diff"]["fields"][1] == {
        "field": "title",
        "old": "h2.title::text",
        "new": "h2::text",
        "changed": True,
    }
    version = report["candidate_version"]

    failed, again = await call(
        workspace, "recipe_propose", path="jobs/job.toml", source=1, recipe=RIGHT, reason="Retry"
    )
    assert not failed and again["candidate_version"] == version
    assert again["sources"][0]["comparisons"][0]["reused"] is True

    failed, review = await call(
        workspace, "semantic_review", path="jobs/job.toml", source=1, version=version
    )
    assert not failed, review
    (title,) = review["fields"]
    assert title["field"] == "title" and title["old"] == title["new"]
    assert title["new"][0] == "Synthetic role 1" and review["state"] == "candidate"

    failed, promoted = await call(
        workspace,
        "recipe_promote",
        path="jobs/job.toml",
        source=1,
        version=version,
        reason="Semantic review: same titles",
    )
    assert not failed and promoted["sources"][0]["promoted_version"] == version


@pytest.mark.asyncio
async def test_a_wrong_proposal_title_to_city_is_rejected_and_retired(workspace: Path) -> None:
    """REPAIR-003, QUAL-006: a structurally valid but semantically wrong mapping fails the
    historical replay; it is retired and the serving recipe is unchanged."""
    await broken(workspace)
    failed, report = await call(
        workspace,
        "recipe_propose",
        path="jobs/job.toml",
        source=1,
        recipe=WRONG,
        reason="Try the span",
    )
    assert not failed, report
    assert report["decision"] == "reject" and report["sources"][0]["promoted_version"] is None
    assert "Rejected" in report["next"]
    version = report["candidate_version"]
    failed, history = await call(workspace, "recipe_history", path="jobs/job.toml", source=1)
    assert history["serving_version"] == 1
    states = {v["version"]: v["state"] for v in history["versions"]}
    assert states[version] == "retired"

    failed, review = await call(
        workspace, "semantic_review", path="jobs/job.toml", source=1, version=version
    )
    (title,) = review["fields"]
    assert title["old"][0] == "Synthetic role 1" and title["new"][0] == "Synthetic city 1"


@pytest.mark.asyncio
async def test_proposal_promotes_only_when_the_job_opts_in(workspace: Path) -> None:
    """REPAIR-004: the same [repair] auto_promote rule as recipe_repair."""
    job = workspace / "jobs" / "job.toml"
    job.write_text(JOB + "\n[repair]\nauto_promote = true\n", encoding="utf-8")
    await broken(workspace)
    failed, report = await call(
        workspace, "recipe_propose", path="jobs/job.toml", source=1, recipe=RIGHT, reason="Fix"
    )
    assert not failed and report["decision"] == "promote"
    assert report["sources"][0]["promoted_version"] == report["candidate_version"]
    assert "recipe_apply" in report["next"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recipe",
    [
        {**RIGHT, "fields": {"salary": "p::text"}},  # undeclared field
        {**RIGHT, "evil": "x"},  # unknown key
        {"strategy": "css", "record_selector": "article.card", "fields": "h2"},
        {**RIGHT, "fields": {"job_id": "::attr(data-id)", "title": "h2.title::text"}},  # no-op
    ],
)
async def test_invalid_or_noop_proposals_use_the_envelope(
    workspace: Path, recipe: dict[str, Any]
) -> None:
    await broken(workspace)
    failed, error = await call(
        workspace, "recipe_propose", path="jobs/job.toml", source=1, recipe=recipe, reason="x"
    )
    assert failed and set(error) == ENVELOPE
    assert "evil" not in json.dumps(error)
    failed, history = await call(workspace, "recipe_history", path="jobs/job.toml", source=1)
    assert [v["version"] for v in history["versions"]] == [1]


@pytest.mark.asyncio
async def test_propose_refuses_unprintable_reasons(workspace: Path) -> None:
    failed, error = await call(
        workspace, "recipe_propose", path="jobs/job.toml", source=1, recipe=RIGHT, reason="a\x1b"
    )
    assert failed and error["error"] == "configuration_error"
    assert not (workspace / "jobs" / "recipes.sqlite").exists()


@pytest.mark.asyncio
async def test_author_a_recipe_for_a_new_page_with_context_and_try(workspace: Path) -> None:
    """INT-001: suggest_context then recipe_try lets an agent iterate without examples."""
    failed, context = await call(
        workspace, "suggest_context", page_path="jobs/board.html", fields=["job_id", "title"]
    )
    assert not failed and "article.card x5" in context["page_outline"]
    assert context["fields"] == ["job_id", "title"] and "css" in context["recipe_format"]

    failed, tried = await call(workspace, "recipe_try", page_path="jobs/board.html", recipe=RIGHT)
    assert not failed, tried
    assert tried["status"] == "success" and tried["total"] == 5
    assert tried["filled"] == {"job_id": 5, "title": 5}
    assert tried["preview"][0] == {"job_id": "job-1", "title": "Synthetic role 1"}

    partial = {**RIGHT, "fields": {"job_id": "::attr(data-id)"}}
    failed, tried = await call(
        workspace,
        "recipe_try",
        page_path="jobs/board.html",
        recipe=partial,
        fields=["job_id", "title"],
    )
    assert not failed and tried["status"] != "success" and tried["issues"]


@pytest.mark.asyncio
@pytest.mark.parametrize("page", ["../outside.html", "/etc/hosts", "jobs/missing.html"])
async def test_agent_tools_stay_inside_the_workspace(workspace: Path, page: str) -> None:
    (workspace.parent / "outside.html").write_text(board(), encoding="utf-8")
    calls: tuple[tuple[str, dict[str, Any]], ...] = (
        ("suggest_context", {"fields": ["title"]}),
        ("recipe_try", {"recipe": RIGHT}),
    )
    for tool, extra in calls:
        failed, error = await call(workspace, tool, page_path=page, **extra)
        assert failed and set(error) == ENVELOPE


@pytest.mark.asyncio
async def test_outline_is_bounded_on_a_huge_page(workspace: Path) -> None:
    """AGENT-002: the outline has its own size limit; other values are cut and flagged."""
    rows = "".join(f'<div class="row r{n}"><p>{"word " * 50}</p></div>' for n in range(3000))
    (workspace / "jobs" / "big.html").write_text(f"<html><body>{rows}</body></html>")
    failed, context = await call(
        workspace, "suggest_context", page_path="jobs/big.html", fields=["title"]
    )
    assert not failed and len(context["page_outline"]) <= 6000
    assert context["page_outline"].endswith("(outline truncated)")
