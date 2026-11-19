"""Studio guided flow: hub-first lookup, screenshot with highlight boxes, point-and-click picks."""

import base64
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.application import hub_search
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.intelligence import InferencePolicy, ModelRequest
from rastro.domain.models import Outcome, Representation, Source
from rastro.studio import routes_pick
from rastro.studio.app import create_app
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

URL = "https://shop.example/items?page=2"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32
PAGE = """<html><body><main>
<article class="card"><h3 class="name">Lamp</h3><span class="price">10.50</span></article>
<article class="card"><h3 class="name">Desk</h3><span class="price">99.00</span></article>
<article class="card"><h3 class="name">Chair</h3><span class="price">45.00</span></article>
</main></body></html>"""
NAME_PATH = [0, 0, 0, 0]  # html > body > main > article > h3
REGISTRY = Path(__file__).parents[2] / "examples/registry"


class Browser:
    """A fake rendering acquirer that also measures element boxes."""

    def __init__(self, png: bytes | None = PNG) -> None:
        self.png = png
        self.fetched: list[str] = []

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation = "html"
    ) -> AcquisitionResult:
        self.fetched.append(source.url)
        return AcquisitionResult(
            url=source.url,
            body=PAGE,
            status=Outcome.SUCCESS,
            issue_code=None,
            http_status=200,
            requests=1,
            screenshot=self.png,
        )

    async def element_boxes(
        self, url: str, selectors: Sequence[str]
    ) -> dict[str, list[dict[str, float]]]:
        wild = {"x": 1e12, "y": float("nan"), "w": 5, "h": 5}
        return {s: [{"x": 10, "y": 20, "w": 100, "h": 30}, wild] for s in selectors}


class Namer:
    def __init__(self, answer: str) -> None:
        self.answer, self.seen = answer, []

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        self.seen.append(request.content)
        return self.answer


def started(tmp_path: Path, acquirer: Any, **options: Any) -> Iterator[TestClient]:
    app, token = create_app(tmp_path, acquisition=acquirer, **options)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        yield http


@pytest.fixture
def browser() -> Browser:
    return Browser()


@pytest.fixture
def client(tmp_path: Path, browser: Browser) -> Iterator[TestClient]:
    yield from started(tmp_path, browser)


def test_wizard_offers_hub_first(
    client: TestClient, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A domain match is offered as a candidate before any page is fetched or rendered."""
    real = hub_search.hub_candidates
    monkeypatch.setattr(routes_pick, "hub_candidates", lambda url: real(url, registry=REGISTRY))
    found = post(client, "/api/wizard/lookup", {"url": "https://listing.example/cards/x"}).json()
    assert found["ok"] and found["offers"], found
    assert {offer["state"] for offer in found["offers"]} == {"candidate"}
    assert browser.fetched == []
    empty = post(client, "/api/wizard/lookup", {"url": "https://nobody.example/"}).json()
    assert empty == {"ok": True, "offers": [], "hub": "ok"}


def test_hub_failure_does_not_block_the_flow(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rastro.domain.models import ConfigurationError

    def down(url: str) -> object:
        raise ConfigurationError("hub unreachable")

    monkeypatch.setattr(routes_pick, "hub_candidates", down)
    found = post(client, "/api/wizard/lookup", {"url": URL}).json()
    assert found["ok"] and found["offers"] == [] and found["hub"] == "unavailable"


def test_screenshot_highlight_payload(client: TestClient) -> None:
    """The PNG comes back inline; boxes are clamped to finite page coordinates."""
    out = post(
        client, "/api/wizard/page", {"url": URL, "fields": {"name": "h3.name", "price": ".price"}}
    ).json()
    assert out["ok"] and out["data_trust"] == "untrusted_source_data"
    assert base64.b64decode(out["screenshot"]) == PNG
    assert out["boxes_measured"] is True
    by_field = {h["field"]: h for h in out["highlights"]}
    assert by_field["name"]["matches"] == 3 and by_field["name"]["selector"] == "h3.name"
    assert by_field["name"]["boxes"] == [{"x": 10, "y": 20, "w": 100, "h": 30}]


def test_page_without_screenshot_says_so(tmp_path: Path) -> None:
    for http in started(tmp_path, Browser(png=None)):
        out = post(http, "/api/wizard/page", {"url": URL}).json()
        assert out["ok"] and out["screenshot"] is None and out["highlights"] == []


def test_pick_returns_stable_selector(client: TestClient) -> None:
    """A clicked element path becomes a class-based selector, not a positional one."""
    post(client, "/api/wizard/page", {"url": URL})
    out = post(client, "/api/wizard/pick", {"url": URL, "path": NAME_PATH}).json()
    assert out["ok"] and out["selector"] == "h3.name", out
    assert out["matches"] == 3 and out["sample"] == ["Lamp", "Desk", "Chair"]
    assert out["data_trust"] == "untrusted_source_data"


@pytest.mark.parametrize(
    "path", [[], [True], [-1], ["0"], [99], list(range(200)), [0, 0, 0, 0, 0, 0]]
)
def test_pick_rejects_bad_paths(client: TestClient, path: object) -> None:
    post(client, "/api/wizard/page", {"url": URL})
    out = post(client, "/api/wizard/pick", {"url": URL, "path": path})
    assert out.status_code == 400 and out.json()["ok"] is False


def test_pick_needs_the_page_first(client: TestClient) -> None:
    out = post(client, "/api/wizard/pick", {"url": URL, "path": NAME_PATH})
    assert out.status_code == 409 and out.json()["error"] == "no_page_loaded"


def test_pick_names_the_field_with_the_model(tmp_path: Path) -> None:
    namer = Namer(json.dumps({"name": "Product Name"}))
    for http in started(tmp_path, Browser(), model=namer):
        post(http, "/api/wizard/page", {"url": URL})
        out = post(
            http, "/api/wizard/pick", {"url": URL, "path": NAME_PATH, "name_with_model": True}
        ).json()
        assert out["field_name"] == "product_name" and out["remote"] is False
        assert "Lamp" in namer.seen[0]


def test_pick_model_name_is_validated_and_remote_needs_consent(tmp_path: Path) -> None:
    for http in started(tmp_path, Browser(), model=Namer('{"name": "bad name!!"}')):
        post(http, "/api/wizard/page", {"url": URL})
        body = {"url": URL, "path": NAME_PATH, "name_with_model": True}
        assert post(http, "/api/wizard/pick", body).json()["field_name"] == "h3"
    remote = Namer('{"name": "title"}')
    for http in started(tmp_path, Browser(), model=remote, model_remote=True):
        post(http, "/api/wizard/page", {"url": URL})
        body = {"url": URL, "path": NAME_PATH, "name_with_model": True}
        refused = post(http, "/api/wizard/pick", body)
        assert refused.status_code == 409 and remote.seen == []
        assert post(http, "/api/wizard/pick", {**body, "allow_remote": True}).status_code == 200


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/wizard/lookup", {"url": URL}),
        ("/api/wizard/page", {"url": URL}),
        ("/api/wizard/pick", {"url": URL, "path": NAME_PATH}),
    ],
)
def test_routes_require_token_and_origin(
    tmp_path: Path, browser: Browser, path: str, body: dict[str, object]
) -> None:
    app, token = create_app(tmp_path, acquisition=browser)
    with serving(app, "http://127.0.0.1:8765") as http:
        assert http.post(path, json=body).status_code == 403
        wrong = {"X-Studio-Token": "wrong"}
        assert http.post(path, json=body, headers=wrong).status_code == 403
        cross = {"X-Studio-Token": token, "Origin": "http://127.0.0.1:9999"}
        assert http.post(path, json=body, headers=cross).status_code == 403
        assert http.get(path, headers={"X-Studio-Token": token}).status_code == 405
    assert browser.fetched == []


def test_page_refuses_urls_the_policy_refuses(client: TestClient, browser: Browser) -> None:
    for url in ("file:///etc/passwd", "http://127.0.0.1:22/", "not a url"):
        out = post(client, "/api/wizard/page", {"url": url})
        assert out.status_code == 400 and out.json()["ok"] is False
    assert browser.fetched == []
