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


TOOLS = {
    "job_validate",
    "job_preview",
    "job_extract",
    "job_load",
    "job_run",
    "dataset_records",
    "drift_status",
    "recipe_list",
    "recipe_history",
    "recipe_repair",
    "recipe_promote",
    "recipe_apply",
    "suggest_recipe",
    "job_new",
    "scrape_file",
    "repair_context",
    "recipe_propose",
    "semantic_review",
    "suggest_context",
    "recipe_try",
}
READ_ONLY = {
    "job_validate",
    "job_preview",
    "dataset_records",
    "drift_status",
    "recipe_list",
    "recipe_history",
    "suggest_recipe",
    "scrape_file",
    "repair_context",
    "semantic_review",
    "suggest_context",
    "recipe_try",
}
DESTRUCTIVE = {
    "job_load",
    "job_run",
    "recipe_repair",
    "recipe_promote",
    "recipe_apply",
    "recipe_propose",
}
# (readOnlyHint, destructiveHint, idempotentHint, openWorldHint) per tool (AGENT-002).
HINTS = {
    "job_validate": (True, None, True, False),
    "job_preview": (True, None, True, True),
    "job_extract": (False, False, False, True),
    "job_load": (False, True, False, True),
    "job_run": (False, True, False, True),
    "dataset_records": (True, None, True, False),
    "drift_status": (True, None, True, False),
    "recipe_list": (True, None, True, False),
    "recipe_history": (True, None, True, False),
    "recipe_repair": (False, True, False, True),
    "recipe_promote": (False, True, False, True),
    "recipe_apply": (False, True, True, False),
    "suggest_recipe": (True, None, True, False),
    "job_new": (False, False, False, False),
    "scrape_file": (True, None, True, False),
    "repair_context": (True, None, True, True),
    "recipe_propose": (False, True, False, True),
    "semantic_review": (True, None, True, True),
    "suggest_context": (True, None, True, False),
    "recipe_try": (True, None, True, False),
}


@pytest.mark.asyncio
async def test_lists_every_tool_with_annotations_and_instructions(workspace: Path) -> None:
    """AGENT-002: names, hints and the server workflow text are what clients rely on."""
    async with create_connected_server_and_client_session(
        build_server(workspace)._mcp_server
    ) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        instructions = (await client.initialize()).instructions or ""
    assert set(tools) == TOOLS
    assert {n for n, t in tools.items() if t.annotations.readOnlyHint} == READ_ONLY
    assert {n for n, t in tools.items() if t.annotations.destructiveHint} == DESTRUCTIVE
    hints = {
        name: (
            t.annotations.readOnlyHint,
            t.annotations.destructiveHint,
            t.annotations.idempotentHint,
            t.annotations.openWorldHint,
        )
        for name, t in tools.items()
    }
    assert hints == HINTS
    assert tools["job_run"].description.startswith("Deprecated: use job_load")
    for tool in tools.values():
        properties = tool.inputSchema["properties"]
        assert properties.get("path", properties.get("page_path"))["maxLength"] == 1024
    order = [
        "suggest_recipe",
        "job_new",
        "job_extract",
        "job_load",
        "drift_status",
        "recipe_repair",
        "recipe_promote",
        "recipe_apply",
    ]
    positions = [instructions.index(name) for name in order]
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_deprecated_job_run_still_loads_the_dataset(workspace: Path) -> None:
    """AGENT-003: the old name keeps working for one minor release."""
    failed, loaded = await _call(workspace, "job_run", path="jobs/job.toml")
    assert not failed and loaded["sources"]
    assert (workspace / "jobs" / "agenda.sqlite").exists()


@pytest.mark.asyncio
async def test_validate_preview_run_then_read_records(workspace: Path) -> None:
    failed, valid = await _call(workspace, "job_validate", path="jobs/job.toml")
    assert not failed and valid["valid"] is True and valid["source_count"] == 3

    failed, preview = await _call(workspace, "job_preview", path="jobs/job.toml")
    assert not failed and preview["data_trust"] == "untrusted_source_data"
    assert not (workspace / "jobs" / "agenda.sqlite").exists()

    failed, loaded = await _call(workspace, "job_load", path="jobs/job.toml")
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
    assert error["error"] in {"configuration_error", "invalid_job", "validation_error"}
    assert "Traceback" not in text and str(workspace) not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/etc/passwd", "C:\\Windows\\job.toml", "C:/x.toml"])
async def test_absolute_paths_are_named_as_such(workspace: Path, path: str) -> None:
    """AGENT-002: an absolute path is refused as absolute, not as a symlink."""
    failed, text = await _call(workspace, "job_validate", path=path)
    assert failed
    error = json.loads(text[text.index("{") :])
    assert "Absolute paths are not allowed" in error["message"]


@pytest.mark.asyncio
async def test_preview_of_a_memory_job_writes_nothing(workspace: Path) -> None:
    """DATA-002, AGENT-002: job_preview works for recipe_memory jobs and creates no store."""
    job = workspace / "jobs" / "job.toml"
    job.write_text(
        job.read_text(encoding="utf-8").replace(
            "format_version = 1", 'format_version = 1\nrecipe_memory = "recipes.sqlite"', 1
        ),
        encoding="utf-8",
    )
    failed, preview = await _call(workspace, "job_preview", path="jobs/job.toml")
    assert not failed and preview["sources"]
    assert not (workspace / "jobs" / "recipes.sqlite").exists()
    assert not (workspace / "jobs" / "agenda.sqlite").exists()


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
    await _call(workspace, "job_load", path="jobs/job.toml")
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
    await _call(workspace, "job_load", path="jobs/job.toml")
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


def test_bounded_caps_dict_size_at_every_depth() -> None:
    """AGENT-002: wide objects are cut like long lists, including nested ones."""
    from rastro.mcp_tools import bounded

    wide = {f"k{i}": i for i in range(150)}
    payload = bounded({"a": [{"b": wide}]}, max_keys=100)
    assert len(payload["a"][0]["b"]) == 100 and payload["truncated"] is True
    top = bounded(dict(wide), max_keys=100)
    assert len(top) == 101 and top["truncated"] is True  # 100 keys plus the flag
    assert bounded({"x": {"y": 1}})["truncated"] is False


@pytest.mark.asyncio
async def test_dataset_records_values_are_bounded(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGENT-002: long stored values are cut and flagged."""
    await _call(workspace, "job_load", path="jobs/job.toml")
    from rastro import mcp_tools

    original = mcp_tools.bounded
    seen: list[bool] = []

    def spy(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        seen.append(True)
        return original(payload, max_chars=5, **kwargs)

    monkeypatch.setattr(mcp_tools, "bounded", spy)
    failed, data = await _call(workspace, "dataset_records", path="jobs/agenda.sqlite", limit=1)
    assert not failed and seen and data["truncated"] is True
