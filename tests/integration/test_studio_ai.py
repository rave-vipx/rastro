"""Studio "Ask AI" with a fake ModelPort: verified proposals only, guarded endpoints, remote
notice (INT-001, REPAIR-001-005, P-008).

The fake model returns canned proposal JSON; no provider is involved, and none is
special-cased: anything implementing ``ModelPort.generate`` works.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.domain.intelligence import InferencePolicy, ModelRequest
from rastro.studio.app import create_app
from tests.integration.test_job_api import JOB, board
from tests.integration.test_studio import post, serving

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
BASE = "http://127.0.0.1:8765"
FIELDS = {"job_id": "::attr(data-id)", "title": "h2::text", "salary": "span.salary::text"}
RIGHT = {"strategy": "css", "record_selector": "article.card", "fields": FIELDS}
MISSING = {**RIGHT, "record_selector": "li.nothing"}
PAGE = {**RIGHT, "fields": {"job_id": "::attr(data-id)", "title": "h2::text"}}


class FakeModel:
    """Any ModelPort: returns the queued proposals in order and keeps the requests."""

    def __init__(self, *answers: dict[str, object]) -> None:
        self.answers = [json.dumps(a) for a in answers]
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        self.requests.append(request)
        return self.answers.pop(0)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "board.html").write_text(board(), encoding="utf-8")
    (tmp_path / "job.toml").write_text(JOB, encoding="utf-8")
    return tmp_path


def client_for(root: Path, model: FakeModel | None, remote: bool = False) -> Iterator[TestClient]:
    app, token = create_app(root, model=model, model_remote=remote)
    with serving(app, BASE) as http:
        http.headers["X-Studio-Token"] = token
        yield http


def ask(root: Path, model: FakeModel | None, path: str, body: dict[str, object], **kw: bool):  # type: ignore[no-untyped-def]
    for http in client_for(root, model, **kw):
        return post(http, path, body)


SUGGEST = {"page": "board.html", "fields": ["job_id", "title"]}


def test_without_a_model_ask_ai_is_off(root: Path) -> None:
    for http in client_for(root, None):
        assert http.get("/api/ai").json() == {"enabled": False, "remote": False}
        reply = post(http, "/api/ai/suggest", SUGGEST)
        assert reply.status_code == 409 and reply.json()["error"] == "no_model_configured"


def test_ask_ai_needs_the_token_and_the_exact_origin(root: Path) -> None:
    """P-008: same guard as every Studio API call."""
    model = FakeModel(RIGHT)
    for http in client_for(root, model):
        assert post(http, "/api/ai/suggest", SUGGEST, **{"X-Studio-Token": "x"}).status_code == 403
        evil = post(http, "/api/ai/suggest", SUGGEST, Origin="http://evil.test")
        assert evil.status_code == 403
        del http.headers["X-Studio-Token"]
        assert http.get("/api/ai").status_code == 403
    assert model.requests == []


def test_suggest_keeps_only_a_verified_recipe_and_sends_the_page(root: Path) -> None:
    """INT-001: the first proposal matches nothing and is retried; the second is verified."""
    model = FakeModel(MISSING, PAGE)
    data = ask(root, model, "/api/ai/suggest", SUGGEST).json()
    assert data["found"] is True and data["calls"] == 2 and data["records"] == 5
    assert data["recipe"]["fields"]["title"] == "h2::text"
    assert data["preview"][0]["title"] == "Synthetic role 1" and data["remote"] is False
    content = json.loads(model.requests[0].content)
    # A page within the context budget is sent as is; a larger one as a digest.
    assert "<article" in content["untrusted_html"]
    assert content["fields"] == ["job_id", "title"]


def test_suggest_reports_no_recipe_when_nothing_verifies(root: Path) -> None:
    data = ask(root, FakeModel(MISSING, MISSING), "/api/ai/suggest", SUGGEST).json()
    assert data["ok"] is True and data["found"] is False and data["calls"] == 2
    assert "recipe" not in data


def test_suggest_refuses_bad_fields_and_pages(root: Path) -> None:
    model = FakeModel(RIGHT)
    bad = ask(root, model, "/api/ai/suggest", {"page": "board.html", "fields": []})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_fields"
    outside = ask(root, model, "/api/ai/suggest", {**SUGGEST, "page": "../board.html"})
    assert outside.status_code == 400
    assert model.requests == []


REWRITTEN = (
    '<div class="row" data-id="job-{n}"><p class="role">Synthetic role {n}</p>{salary}</div>'
)
REPAIR = {
    "strategy": "css",
    "record_selector": "div.row",
    "fields": {"job_id": "::attr(data-id)", "title": "p.role::text", "salary": "b::text"},
}


class JudgingModel:
    """Proposes ``REPAIR`` and answers the semantic judge with ``verdict``."""

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        self.requests.append(request)
        if request.response_schema is None or "untrusted_html" in request.content:
            return json.dumps(REPAIR)
        names = [item["name"] for item in json.loads(request.content)["fields"]]
        return json.dumps({n: {"verdict": self.verdict, "confidence": 1.0} for n in names})


def rewritten() -> str:
    cards = "".join(
        REWRITTEN.format(n=n, salary="" if n == 3 else f"<b>{n}000</b>") for n in range(1, 6)
    )
    return f"<!doctype html><html><body><main>{cards}</main></body></html>"


@pytest.mark.parametrize(
    ("repair", "verdict", "promoted"),
    [
        ("", "match", False),
        ("auto_promote = true", "mismatch", False),
        ("auto_promote = true", "match", True),
    ],
)
def test_ai_repair_uses_repair_with_model_and_the_judge(
    root: Path, repair: str, verdict: str, promoted: bool
) -> None:
    """REPAIR-001/005, QUAL-006: relocation finds nothing on rewritten markup, so the model
    proposes; Studio promotes only when the job opts in and the judge agrees."""
    (root / "job.toml").write_text(JOB + f"\n[repair]\n{repair}\n", encoding="utf-8")
    model = JudgingModel(verdict)
    for http in client_for(root, model, remote=True):
        assert post(http, "/api/load", {"file": "job.toml"}).status_code == 200
        (root / "board.html").write_text(rewritten(), encoding="utf-8")
        data = post(http, "/api/ai/repair", {"file": "job.toml", "source": 1}).json()
        assert http.get("/api/ai").json() == {"enabled": True, "remote": True}
    assert data["ok"] is True and data["remote"] is True, data
    (source,) = data["report"]["sources"]
    assert source["inference_calls"] >= 1
    assert (source["promoted_version"] is not None) is promoted
    diff = {f["field"]: f for f in source["comparisons"][-1]["diff"]["fields"]}
    assert diff["title"]["new"].endswith("p.role::text") and diff["title"]["changed"] is True
