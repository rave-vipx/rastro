"""Advanced: the page steps a recipe replays, listed and deleted one by one."""

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.application.job_loader import load_job
from tests.integration.test_studio import post
from tests.integration.test_studio_pick import Browser, started
from tests.integration.test_studio_wizard_more import JOB

STEPS = JOB.replace(
    '[recipe.fields]\nname = "h3.name::text"',
    '[recipe.fields]\nname = "h3.name::text"\n\n'
    '[[recipe.steps]]\naction = "click"\nselector = "button.more"\n\n'
    '[[recipe.steps]]\naction = "scroll"\ntimes = 2',
).replace(
    'allowed_hosts = ["shop.example"]', 'allowed_hosts = ["shop.example"]\nrender = "browser"'
)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    (tmp_path / "shop.toml").write_text(STEPS, encoding="utf-8")
    (tmp_path / "plain.toml").write_text(JOB, encoding="utf-8")
    yield from started(tmp_path, Browser())


def test_steps_are_listed_and_one_is_deleted(client: TestClient, tmp_path: Path) -> None:
    listed = post(client, "/api/steps", {"file": "shop.toml"}).json()
    assert listed["explore"] is True
    assert listed["steps"] == [
        {"source": None, "index": 0, "action": "click", "selector": "button.more"},
        {"source": None, "index": 1, "action": "scroll", "times": 2, "settle_ms": 500},
    ]
    gone = post(
        client, "/api/steps/delete", {"file": "shop.toml", "sha256": listed["sha256"], "index": 0}
    ).json()
    assert gone["ok"] and [s["action"] for s in gone["steps"]] == ["scroll"]
    job = load_job(tmp_path / "shop.toml")
    assert [s.action for s in job.recipe.steps] == ["scroll"]  # type: ignore[union-attr]
    assert post(client, "/api/steps", {"file": "plain.toml"}).json()["steps"] == []


def test_delete_refuses_stale_digests_and_unknown_steps(client: TestClient, tmp_path: Path) -> None:
    sha = post(client, "/api/steps", {"file": "shop.toml"}).json()["sha256"]
    stale = post(client, "/api/steps/delete", {"file": "shop.toml", "sha256": "0" * 64, "index": 0})
    assert stale.status_code == 409 and stale.json()["error"] == "file_changed"
    for extra in ({"index": 5}, {"index": 0, "source": 3}):
        refused = post(client, "/api/steps/delete", {"file": "shop.toml", "sha256": sha, **extra})
        assert refused.status_code == 404 and refused.json()["error"] == "unknown_step"
    for extra in ({}, {"index": True}, {"index": -1}):
        bad = post(client, "/api/steps/delete", {"file": "shop.toml", "sha256": sha, **extra})
        assert bad.status_code == 400
    assert (tmp_path / "shop.toml").read_text(encoding="utf-8") == STEPS
    no_token = post(client, "/api/steps", {"file": "shop.toml"}, **{"X-Studio-Token": ""})
    assert no_token.status_code == 403
