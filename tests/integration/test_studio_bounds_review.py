"""N3/N9 SEC-008: regression probes from the cross-model Studio review."""

import json
import tomllib
from pathlib import Path

import pytest

from rastro.studio.app import create_app
from rastro.studio.editing import edit_policy_table
from tests.integration.test_studio import post, serving
from tests.integration.test_studio_ops import EXAMPLES, PAGE, URL, FakeAcquirer

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


@pytest.mark.parametrize(("override", "disabled"), [("", False), ('render = "http"', True)])
def test_scroll_inherits_job_browser_render(tmp_path: Path, override: str, disabled: bool) -> None:
    """N9: absent source render inherits acquisition; explicit HTTP wins."""
    path = tmp_path / "job.toml"
    path.write_text(f"""
format_version = 1
recipe_memory = "recipes.sqlite"
[[fields]]
name = "title"
type = "string"
[acquisition]
allowed_hosts = ["example.test"]
render = "browser"
[[sources]]
kind = "url"
url = "https://example.test/items"
{override}
""")
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        response = post(client, "/api/validate", {"file": "job.toml"})
        assert response.status_code == 200, response.text
        assert response.json()["bounds"]["scroll_disabled"] is disabled


@pytest.mark.parametrize("suffix", ["toml", "json"])
def test_non_bmp_policy_round_trip(tmp_path: Path, suffix: str) -> None:
    """N3: Unicode path prefixes survive initial write and subsequent replacement."""
    app, token = create_app(tmp_path, acquisition=FakeAcquirer({URL: PAGE}))
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        assert (
            post(
                client,
                "/api/create",
                {
                    "file": "job.toml",
                    "url": URL,
                    "examples": EXAMPLES,
                },
            ).status_code
            == 200
        )
        path = tmp_path / f"job.{suffix}"
        if suffix == "json":
            path.write_text(
                json.dumps(tomllib.loads((tmp_path / "job.toml").read_text(encoding="utf-8")))
            )
        for prefix in ("/😀", "/😀/more"):
            opened = post(client, "/api/validate", {"file": path.name}).json()
            saved = post(
                client,
                "/api/bounds",
                {
                    "file": path.name,
                    "sha256": opened["sha256"],
                    "kind": "spider",
                    "policy": {"mode": "spider", "links": "a::attr(href)", "allow": [prefix]},
                },
            )
            assert saved.status_code == 200, saved.text
            assert prefix in path.read_text(encoding="utf-8")


def test_policy_write_does_not_add_trailing_spaces() -> None:
    """N9: unchanged layout around a scalar is preserved."""
    assert edit_policy_table("[scroll]\nmax_steps = 2\n", "scroll", {"max_steps": 3}) == (
        "[scroll]\nmax_steps = 3\n"
    )


def test_preview_refetches_when_operator_grants_change(tmp_path: Path) -> None:
    """N1/SEC-008: a preview never carries old grants into a later create."""
    from rastro.application.operator_trust import operator_trust

    acquirer = FakeAcquirer({URL: PAGE})
    app, token = create_app(tmp_path, acquisition=acquirer)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        data = {"file": "job.toml", "url": URL, "examples": EXAMPLES}
        with operator_trust(allow_private_network=True):
            assert post(client, "/api/suggest", data).status_code == 200
        assert post(client, "/api/create", data).status_code == 200
    assert acquirer.fetched == [URL, URL]
