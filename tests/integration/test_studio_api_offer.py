"""Guided create's "Use the API": the written job reads the JSON response the page loads."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.application.job_from_url import prepare_url_policy
from rastro.application.job_loader import load_job
from rastro.application.network_recipes import acquire_captured, record_captured
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.models import Outcome, Representation, Source
from rastro.ports.engine import CapturedResponse
from tests.integration.test_studio_pick import PAGE, URL, started
from tests.integration.test_studio_run_live import wait_done

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

ITEMS = {"data": {"items": [{"name": "Lamp", "price": 10}, {"name": "Desk", "price": 99}]}}
OFFER = {"url": URL, "host": "shop.example", "path": "/api/items", "fields": ["name", "price"]}


class CapturingBrowser:
    """A fake browser: each render "loads" two JSON responses, as a real page would."""

    def __init__(self) -> None:
        self.renders = 0
        _, policy = prepare_url_policy(URL)
        browser = policy.browser.model_copy(update={"capture_network": (r"^/",)})
        self.policy = policy.model_copy(update={"browser": browser})

    def captured(self) -> tuple[CapturedResponse, ...]:
        items = json.dumps(ITEMS).encode()
        return (
            CapturedResponse("shop.example", "/api/menu", "GET", 200, "application/json", b"{}"),
            CapturedResponse("shop.example", "/api/items", "GET", 200, "application/json", items),
        )

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation = "html"
    ) -> AcquisitionResult:
        if representation == "json":  # as the browser acquirer: the captured response
            return await acquire_captured(
                self.policy, lambda: self.acquire(source, max_bytes=max_bytes)
            )
        self.renders += 1
        record_captured(self)
        return AcquisitionResult(
            url=source.url, body=PAGE, status=Outcome.SUCCESS, issue_code=None,
            http_status=200, requests=1,
        )  # fmt: skip


@pytest.fixture
def browser() -> CapturingBrowser:
    return CapturingBrowser()


@pytest.fixture
def client(tmp_path: Path, browser: CapturingBrowser) -> Iterator[TestClient]:
    yield from started(tmp_path, browser)


def test_use_the_api_writes_a_job_that_reads_the_captured_response(
    client: TestClient, browser: CapturingBrowser, tmp_path: Path
) -> None:
    offers = client.post("/api/wizard/offers", json={"url": URL, "fields": ["name", "price"]})
    assert offers.json()["offers"][0]["path"] == "/api/items"
    made = client.post("/api/wizard/offers/use", json={**OFFER, "file": "shop.toml"})
    assert made.status_code == 200, made.text
    assert made.json()["file"] == "shop.toml" and len(made.json()["sha256"]) == 64
    job = load_job(tmp_path / "shop.toml")
    assert job.acquisition is not None and job.acquisition.render == "browser"
    assert job.acquisition.browser.capture_network == ("^/api/items$",)
    assert job.sources[0].representation == "json"  # type: ignore[union-attr]
    assert "Lamp" not in (tmp_path / "shop.toml").read_text()  # captured data is not written
    assert client.post("/api/run/start", json={"file": "shop.toml"}).status_code == 200
    snap = wait_done(client)
    assert snap["state"] == "done" and snap["records"] == 2, repr(snap)
    assert snap["sample"][0] == {"name": "Lamp", "price": "10"}  # type: ignore[index]


def test_use_the_api_refuses_what_it_cannot_read_and_writes_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    for over, code in (
        ({"path": "/api/gone"}, "offer_not_seen"),
        ({"path": "/api/menu"}, "offer_not_readable"),
    ):
        refused = client.post("/api/wizard/offers/use", json={**OFFER, **over, "file": "x.toml"})
        assert refused.status_code == 409 and refused.json()["error"] == code
    for over in ({"file": "../x.toml"}, {"url": "file:///etc/passwd"}, {"identity": "name"}):
        bad = client.post("/api/wizard/offers/use", json={**OFFER, "file": "x.toml", **over})
        assert bad.status_code in {400, 403} and bad.json()["ok"] is False
    assert not (tmp_path / "x.toml").exists()


def test_use_the_api_requires_the_token(client: TestClient) -> None:
    refused = client.post(
        "/api/wizard/offers/use", json={**OFFER, "file": "x.toml"}, headers={"X-Studio-Token": ""}
    )
    assert refused.status_code == 403
