"""MCP lifecycle tools through the real protocol (AGENT-002, AGENT-003, REPAIR-002-005, REC-006).

A synthetic job board: the job's title selector breaks, then the agent repairs, reviews,
promotes and applies the recipe using MCP tools only.
"""

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp.shared.memory import create_connected_server_and_client_session

from rastro.adapters.mcp_server import build_server

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

CARD = '<article class="card" data-id="job-{n}"><h2 class="{cls}">Synthetic role {n}</h2></article>'
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


def break_title(root: Path) -> None:
    (root / "jobs" / "board.html").write_text(board("new-title", wrap=True), encoding="utf-8")


@pytest.mark.asyncio
async def test_drift_status_before_any_load_creates_nothing(workspace: Path) -> None:
    """AGENT-002: drift_status is read-only and says when there is no dataset yet."""
    failed, status = await call(workspace, "drift_status", path="jobs/job.toml")
    assert not failed and status["sources"] == [] and "note" in status
    assert not (workspace / "jobs" / "jobs.sqlite").exists()


@pytest.mark.asyncio
async def test_extract_writes_no_dataset_and_load_does(workspace: Path) -> None:
    """AGENT-003: job_extract is `rastro job run`; job_load is `rastro job load`."""
    failed, extracted = await call(workspace, "job_extract", path="jobs/job.toml")
    assert not failed and extracted["job"]["status"] == "success"
    assert extracted["data_trust"] == "untrusted_source_data"
    assert len(extracted["results"][0]["records"]) == 5
    assert not (workspace / "jobs" / "jobs.sqlite").exists()

    failed, loaded = await call(workspace, "job_load", path="jobs/job.toml", drift=True)
    assert not failed and loaded["sources"][0]["recipe_origin"] == "job"
    assert loaded["drift"] is not None and loaded["truncated"] is False
    assert (workspace / "jobs" / "jobs.sqlite").is_file()

    failed, status = await call(workspace, "drift_status", path="jobs/job.toml")
    assert not failed and status["dataset"] == "jobs"
    assert [s["stale"] for s in status["sources"]] == [False]


@pytest.mark.asyncio
async def test_repair_proposes_then_promote_and_apply_through_mcp(workspace: Path) -> None:
    """REPAIR-002/005, REC-006: repair never promotes on its own; promote needs a reason."""
    assert not (await call(workspace, "job_load", path="jobs/job.toml"))[0]
    break_title(workspace)
    failed, run = await call(workspace, "job_extract", path="jobs/job.toml")
    assert not failed and run["job"]["status"] == "failed"

    failed, report = await call(workspace, "recipe_repair", path="jobs/job.toml", source=1)
    assert not failed, report
    (source,) = report["sources"]
    assert source["promoted_version"] is None
    version = source["comparisons"][0]["candidate_version"]

    failed, history = await call(workspace, "recipe_history", path="jobs/job.toml", source=1)
    assert not failed and history["serving_version"] == 1
    assert version in [v["version"] for v in history["versions"]]

    failed, promoted = await call(
        workspace,
        "recipe_promote",
        path="jobs/job.toml",
        source=1,
        version=version,
        reason="Checked the five cards",
    )
    assert not failed and promoted["sources"][0]["promoted_version"] == version

    failed, applied = await call(workspace, "recipe_apply", path="jobs/job.toml", source=1)
    assert not failed and applied["changed"] is True and applied["path"] == "jobs/job.toml"
    assert f"version = {version}" in (workspace / "jobs" / "job.toml").read_text(encoding="utf-8")
    failed, again = await call(workspace, "recipe_apply", path="jobs/job.toml", source=1)
    assert not failed and again["changed"] is False


@pytest.mark.asyncio
async def test_repair_promotes_only_when_the_job_opts_in(workspace: Path) -> None:
    """REPAIR-004: auto promotion comes from the job's own [repair] block."""
    job = workspace / "jobs" / "job.toml"
    job.write_text(JOB + "\n[repair]\nauto_promote = true\n", encoding="utf-8")
    await call(workspace, "job_load", path="jobs/job.toml")
    break_title(workspace)
    failed, report = await call(workspace, "recipe_repair", path="jobs/job.toml", source=1)
    assert not failed and report["sources"][0]["promoted_version"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["bad\x1b[31m", "  ", "rtl‮"])
async def test_promote_rejects_unprintable_reasons_with_the_envelope(
    workspace: Path, reason: str
) -> None:
    """AGENT-003: audit text is checked before any store is touched."""
    failed, error = await call(
        workspace, "recipe_promote", path="jobs/job.toml", source=1, version=2, reason=reason
    )
    assert failed and set(error) == ENVELOPE
    assert not (workspace / "jobs" / "recipes.sqlite").exists()


@pytest.mark.asyncio
async def test_history_without_memory_and_source_out_of_range(workspace: Path) -> None:
    failed, history = await call(workspace, "recipe_history", path="jobs/job.toml", source=1)
    assert not failed and history["versions"] == [] and "note" in history
    failed, error = await call(workspace, "recipe_history", path="jobs/job.toml", source=9)
    assert failed and error["error"] == "configuration_error"
    assert not (workspace / "jobs" / "recipes.sqlite").exists()


@pytest.mark.asyncio
async def test_argument_bounds_use_the_envelope_without_echoing_input(workspace: Path) -> None:
    """AGENT-002: schema violations are reported like tool errors, never with the input."""
    failed, error = await call(workspace, "recipe_history", path="jobs/job.toml", source=0)
    assert failed and set(error) == ENVELOPE and error["error"] == "validation_error"
    secret = "SECRET-" + "x" * 2000
    failed, error = await call(workspace, "job_validate", path=secret)
    assert failed and error["location"] == "path" and "SECRET" not in json.dumps(error)


def test_bounded_caps_lists_and_strings() -> None:
    """AGENT-002: every growing output is cut and flagged."""
    from rastro.mcp_tools import bounded

    payload = bounded({"rows": list(range(60)), "nested": {"text": "y" * 600}}, max_items=50)
    assert len(payload["rows"]) == 50 and len(payload["nested"]["text"]) == 501
    assert payload["truncated"] is True
    assert bounded({"rows": [1]})["truncated"] is False


@pytest.mark.asyncio
async def test_drift_status_reports_open_drift_and_only_current_sources(workspace: Path) -> None:
    """DRIFT-005, AGENT-002: open drift is reported; removed sources are listed as orphaned."""
    assert not (await call(workspace, "job_load", path="jobs/job.toml", drift=True))[0]
    one = CARD.format(n=1, cls="title")
    (workspace / "jobs" / "board.html").write_text(f"<html><body>{one}</body></html>")
    failed, loaded = await call(workspace, "job_load", path="jobs/job.toml", drift=True)
    assert not failed and loaded["status"] == "degraded"
    failed, status = await call(workspace, "drift_status", path="jobs/job.toml")
    assert not failed
    assert [d["source_url"] for d in status["open_drift"]] == ["https://example.test/jobs"]
    job = workspace / "jobs" / "job.toml"
    job.write_text(job.read_text().replace("example.test/jobs", "example.test/board"))
    failed, status = await call(workspace, "drift_status", path="jobs/job.toml")
    assert not failed and status["sources"] == [] and status["open_drift"] == []
    assert status["orphaned_sources"] == ["https://example.test/jobs"]
