"""MCP authoring tools: suggest_recipe, job_new, scrape_file (AGENT-002, INT-001, INT-002, JOB-001).

Synthetic saved page; no tool touches the network.
"""

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp.shared.memory import create_connected_server_and_client_session

from rastro.adapters.mcp_server import build_server

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

ROW = '<li class="item"><span class="name">Widget {n}</span><b class="price">{n}.50</b></li>'
PAGE = "<html><body><ul>" + "".join(ROW.format(n=n) for n in range(1, 5)) + "</ul></body></html>"
EXAMPLES = [{"name": "Widget 1", "price": "1.50"}, {"name": "Widget 2", "price": "2.50"}]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "page.html").write_text(PAGE, encoding="utf-8")
    return tmp_path


async def call(root: Path, tool: str, **arguments: Any) -> tuple[bool, Any]:
    async with create_connected_server_and_client_session(build_server(root)._mcp_server) as client:
        result = await client.call_tool(tool, arguments)
    return result.isError, json.loads(result.content[0].text)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_suggest_recipe_returns_recipe_and_preview(workspace: Path) -> None:
    failed, found = await call(
        workspace, "suggest_recipe", page_path="work/page.html", examples=EXAMPLES
    )
    assert not failed, found
    assert found["recipe"]["strategy"] == "css" and found["records"] == 4
    assert found["preview"][3] == {"name": "Widget 4", "price": "4.50"}
    assert found["data_trust"] == "untrusted_source_data" and found["truncated"] is False


@pytest.mark.asyncio
async def test_suggest_recipe_without_a_match_is_an_error(workspace: Path) -> None:
    failed, error = await call(
        workspace, "suggest_recipe", page_path="work/page.html", examples=[{"name": "Absent"}]
    )
    assert failed and error["error"] == "configuration_error"


@pytest.mark.asyncio
async def test_job_new_writes_a_runnable_job_and_refuses_overwrite(workspace: Path) -> None:
    """JOB-001: the created job extracts the page; a second call never overwrites it."""
    arguments = {
        "path": "work/shop.toml",
        "page_path": "work/page.html",
        "examples": EXAMPLES,
        "dataset": "shop.sqlite",
        "identity": ["name"],
    }
    failed, created = await call(workspace, "job_new", **arguments)
    assert not failed and created == {"schema_version": 1, "created": "work/shop.toml"}
    before = (workspace / "work" / "shop.toml").read_text(encoding="utf-8")
    failed, extracted = await call(workspace, "job_extract", path="work/shop.toml")
    assert not failed and len(extracted["results"][0]["records"]) == 4

    failed, error = await call(workspace, "job_new", **arguments)
    assert failed and "overwrite" in error["message"]
    assert (workspace / "work" / "shop.toml").read_text(encoding="utf-8") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/tmp/shop.toml", "../shop.toml", "work/../../shop.toml"])
async def test_job_new_paths_stay_in_the_workspace(workspace: Path, path: str) -> None:
    failed, _ = await call(
        workspace, "job_new", path=path, page_path="work/page.html", examples=EXAMPLES
    )
    assert failed
    assert not (workspace.parent / "shop.toml").exists()


@pytest.mark.asyncio
async def test_scrape_file_with_fields_or_recipe(workspace: Path) -> None:
    """INT-001: one-off extraction from a workspace file, no job and no network."""
    fields = {"name": ".name::text", "price": ".price::text"}
    failed, result = await call(
        workspace, "scrape_file", page_path="work/page.html", fields=fields, records="li.item"
    )
    assert not failed and result["status"] == "success" and result["total"] == 4
    assert result["records"][0] == {"name": "Widget 1", "price": "1.50"}

    recipe = {"strategy": "css", "record_selector": "li.item", "fields": fields}
    failed, same = await call(workspace, "scrape_file", page_path="work/page.html", recipe=recipe)
    assert not failed and same["records"] == result["records"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"fields": {"name": ".name::text"}, "recipe": {"strategy": "css"}},
        {"recipe": {"strategy": "nope"}},
        {
            "recipe": {"strategy": "css", "record_selector": "li", "fields": {"a": ".a::text"}},
            "records": "li",
        },
    ],
)
async def test_scrape_file_rejects_ambiguous_or_invalid_input(
    workspace: Path, arguments: dict[str, Any]
) -> None:
    failed, error = await call(workspace, "scrape_file", page_path="work/page.html", **arguments)
    assert failed and error["error"] == "configuration_error"


@pytest.mark.asyncio
async def test_page_symlink_is_refused(workspace: Path, tmp_path_factory: Any) -> None:
    outside = tmp_path_factory.mktemp("outside") / "page.html"
    outside.write_text(PAGE, encoding="utf-8")
    (workspace / "work" / "link.html").symlink_to(outside)
    failed, _ = await call(
        workspace, "suggest_recipe", page_path="work/link.html", examples=EXAMPLES
    )
    assert failed
