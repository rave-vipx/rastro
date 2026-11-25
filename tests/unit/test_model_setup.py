"""Model setup: loopback detection, the remote opt-in, cost per call, and no-AI runs."""

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest
from pydantic import BaseModel

import rastro
from rastro import InferencePolicy, ModelRequest
from rastro.application.model_detect import DetectedModel, detect_local_models
from rastro.application.run_report import report_for_run
from rastro.cli import main
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import ConfigurationError, ExtractionResult, Metrics, Outcome

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

TAGS = {"models": [{"name": "llama3.2:3b"}, {"name": "qwen3:4b"}]}
LISTING = {"data": [{"id": "local-gguf"}]}


class Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        type(self).seen.append(self.path)
        body = {"/api/tags": TAGS, "/v1/models": LISTING}.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body or {}).encode())

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server():
    Handler.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.enable_socket
def test_detect_local_ollama_fake_server(server):
    found = detect_local_models(probes=(("ollama", server),), timeout=1)
    assert found == (
        DetectedModel(
            provider="ollama",
            base_url=f"http://127.0.0.1:{server}",
            models=("llama3.2:3b", "qwen3:4b"),
            spec=f"http:llama3.2:3b@http://127.0.0.1:{server}/v1",
        ),
    )
    assert Handler.seen == ["/api/tags"]  # a listing only: no prompt, no page data


@pytest.mark.enable_socket
def test_detect_openai_compatible_and_closed_ports(server):
    found = detect_local_models(probes=(("openai_compatible", server), ("ollama", 1)), timeout=0.5)
    assert [d.provider for d in found] == ["openai_compatible"]
    assert found[0].spec == f"http:local-gguf@http://127.0.0.1:{server}/v1"


@pytest.mark.enable_socket
def test_detect_default_ollama_port_uses_the_short_spec(monkeypatch):
    from rastro.application import model_detect

    monkeypatch.setattr(
        model_detect, "_get", lambda port, path, timeout: TAGS if path == "/api/tags" else None
    )
    (found,) = detect_local_models(probes=(("ollama", 11434),))
    assert found.spec == "ollama:llama3.2:3b"


def test_detect_probes_loopback_ports_only():
    from rastro.application import model_detect

    assert {kind for kind, _ in model_detect.DEFAULT_PROBES} == {"ollama", "openai_compatible"}
    assert [p for _, p in model_detect.DEFAULT_PROBES] == [11434, 1234, 8000, 8080]
    assert model_detect.HOST == "127.0.0.1"


def test_remote_still_needs_allow_remote(monkeypatch, capsys):
    monkeypatch.delenv("RASTRO_ALLOW_REMOTE", raising=False)
    spec = "http:gpt-x@https://api.provider.example/v1"
    with pytest.raises(ConfigurationError, match="allow_remote"):
        rastro.model(spec)
    assert main(["model", "test", "--model", spec]) != 0
    assert "allow_remote" in capsys.readouterr().err


class FakeModel:
    remote = False

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        return "{}"


def test_model_test_reports_cost_per_call(monkeypatch, capsys):
    monkeypatch.setattr(rastro, "model", lambda spec, **options: FakeModel(), raising=False)
    code = main(["model", "test", "--model", "x:y", "--model-cost-per-call", "0.002"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["ok"] is True and out["remote"] is False
    assert out["cost_per_call"] == 0.002 and out["calls"] == 1


class Item(BaseModel):
    name: str


def result(calls: int) -> JobRunResult[Item]:
    now = datetime.now(UTC)
    item = ExtractionResult[Item](
        job_id="j",
        status=Outcome.SUCCESS,
        source_url="https://h.example/list",
        locale="en-US",
        timezone="UTC",
        schema_fingerprint="0" * 64,
        started_at=now,
        completed_at=now,
        records=(),
        issues=(),
        metrics=Metrics(
            input_bytes=1,
            candidates=0,
            accepted=0,
            rejected=0,
            extraction_seconds=0,
            validation_seconds=0,
            total_seconds=0,
            inference_calls=calls,
        ),
    )
    return JobRunResult.from_results(schema_fingerprint="0" * 64, results=[item])


def test_cost_per_call_in_report():
    totals = report_for_run("job.toml", result(3), cost_per_call=0.002).totals
    assert (totals.model_calls, totals.model_cost_per_call) == (3, 0.002)
    assert totals.model_cost == pytest.approx(0.006)
    unpriced = report_for_run("job.toml", result(3)).totals
    assert unpriced.model_cost is None and unpriced.model_cost_per_call is None


def test_no_model_runs_still_work(monkeypatch):
    monkeypatch.delenv("RASTRO_MODEL", raising=False)
    totals = report_for_run("job.toml", result(0)).totals
    assert (totals.model_calls, totals.model_cost) == (0, None)
    from rastro.application.model_choice import resolve_model

    assert resolve_model(None) is None
