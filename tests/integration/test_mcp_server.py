"""MCP tools run the offline v0.3 job through the real protocol (AGENT-002, AGENT-003, P-008)."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp.shared.memory import create_connected_server_and_client_session

from rastro.adapters.mcp_server import build_server

V03 = Path(__file__).resolve().parents[2] / "examples" / "uy-dance-agenda" / "sample" / "v03"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "jobs").mkdir()
    for item in V03.iterdir():
        shutil.copyfile(item, tmp_path / "jobs" / item.name)
    return tmp_path


async def _call(root: Path, tool: str, **arguments: Any) -> tuple[bool, Any]:
    async with create_connected_server_and_client_session(build_server(root)._mcp_server) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text  # type: ignore[union-attr]
    return result.isError, json.loads(text) if not result.isError else text


@pytest.mark.asyncio
async def test_lists_the_five_tools(workspace: Path) -> None:
    async with create_connected_server_and_client_session(
        build_server(workspace)._mcp_server
    ) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert names == {"job_validate", "job_preview", "job_run", "dataset_records", "recipe_list"}


@pytest.mark.asyncio
async def test_validate_preview_run_then_read_records(workspace: Path) -> None:
    failed, valid = await _call(workspace, "job_validate", path="jobs/job.toml")
    assert not failed and valid["valid"] is True and valid["source_count"] == 3

    failed, preview = await _call(workspace, "job_preview", path="jobs/job.toml")
    assert not failed and preview["data_trust"] == "untrusted_source_data"
    assert not (workspace / "jobs" / "agenda.sqlite").exists()

    failed, loaded = await _call(workspace, "job_run", path="jobs/job.toml")
    assert not failed and loaded["sources"]

    failed, data = await _call(workspace, "dataset_records", path="jobs/agenda.sqlite", limit=2)
    assert not failed
    assert data["data_trust"] == "untrusted_source_data"
    assert data["total"] > 2 and data["truncated"] is True and len(data["records"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.toml", "jobs/../../x.toml", ""])
async def test_paths_outside_the_workspace_are_rejected(workspace: Path, path: str) -> None:
    failed, text = await _call(workspace, "job_validate", path=path)
    assert failed
    error = json.loads(text[text.index("{") :])
    assert error["error"] in {"configuration_error", "invalid_job"}
    assert "Traceback" not in text and str(workspace) not in text


@pytest.mark.asyncio
async def test_symlink_escape_is_rejected(workspace: Path, tmp_path_factory: Any) -> None:
    outside = tmp_path_factory.mktemp("outside") / "job.toml"
    shutil.copyfile(workspace / "jobs" / "job.toml", outside)
    (workspace / "link.toml").symlink_to(outside)
    failed, _ = await _call(workspace, "job_validate", path="link.toml")
    assert failed


@pytest.mark.asyncio
async def test_errors_carry_no_source_content(workspace: Path) -> None:
    (workspace / "bad.toml").write_text("format_version = 1\nSECRET-VALUE = [", encoding="utf-8")
    failed, text = await _call(workspace, "job_validate", path="bad.toml")
    assert failed
    assert json.loads(text[text.index("{") :])["error"] == "syntax_error"
    assert "SECRET-VALUE" not in text


@pytest.mark.asyncio
async def test_dataset_limit_is_bounded_and_missing_file_is_an_error(workspace: Path) -> None:
    failed, _ = await _call(workspace, "dataset_records", path="jobs/nope.sqlite")
    assert failed
    await _call(workspace, "job_run", path="jobs/job.toml")
    failed, text = await _call(workspace, "dataset_records", path="jobs/agenda.sqlite", limit=999)
    assert failed and "limit" in text


@pytest.mark.asyncio
async def test_recipe_list_reports_versions_after_a_run(workspace: Path) -> None:
    job = workspace / "jobs" / "job.toml"
    job.write_text(
        job.read_text(encoding="utf-8").replace(
            "format_version = 1", 'format_version = 1\nrecipe_memory = "memory.sqlite"', 1
        ),
        encoding="utf-8",
    )
    failed, empty = await _call(workspace, "recipe_list", path="jobs/job.toml")
    assert not failed and empty["sources"] == []
    assert not (workspace / "jobs" / "memory.sqlite").exists()
    await _call(workspace, "job_run", path="jobs/job.toml")
    failed, listed = await _call(workspace, "recipe_list", path="jobs/job.toml")
    assert not failed and len(listed["sources"]) == 3
    assert any(item["serving_version"] for item in listed["sources"])


_JOB_SUMMARY = {
    "schema_fingerprint": "0" * 64,
    "source_count": 1,
    "succeeded": 1,
    "partial": 0,
    "failed": 0,
    "records_accepted": 60,
    "records_rejected": 0,
    "status": "success",
}


def test_bounded_preview_caps_records_and_long_values() -> None:
    """AGENT-002: agent and Studio previews are bounded and say when they were cut."""
    from rastro.application.job_preview import bounded_preview
    from rastro.domain.job_dataset import JobPreview

    field = {
        "name": "title",
        "value": "x" * 900,
        "validation": "valid",
        "identity": True,
        "loaded": True,
    }
    record = {"index": 0, "status": "accepted", "fields": [field]}
    preview = JobPreview.model_validate(
        {
            "dataset": {
                "name": "d",
                "path": "d.sqlite",
                "identity": ["title"],
                "approve": ["title"],
            },
            "job": _JOB_SUMMARY,
            "sources": [
                {
                    "source_url": "https://example.test/",
                    "status": "success",
                    "records": [dict(record, index=i) for i in range(60)],
                }
            ],
        }
    )
    payload = bounded_preview(preview)
    assert payload["truncated"] is True
    assert len(payload["sources"][0]["records"]) == 50
    assert len(payload["sources"][0]["records"][0]["fields"][0]["value"]) == 501
