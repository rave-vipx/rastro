import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro import (
    ConfigurationError,
    InferencePolicy,
    JobExitCode,
    ModelRequest,
    Outcome,
    parse_job,
    run_job_async,
    run_job_file,
)
from rastro.application.acquisition import WebAcquirer
from rastro.application.job_loader import _build_model_provider, prepare_job
from rastro.cli import main
from rastro.domain.acquisition import HttpResponse
from rastro.domain.job_providers import OllamaProvider, OpenAICompatibleProvider


class FakeResolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return ("8.8.8.8",)


class FakeTransport:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.headers_seen: list[dict[str, str]] = []

    async def get(self, target, **kwargs) -> HttpResponse:
        self.headers_seen.append(kwargs["headers"])
        if urlsplit(target.url).path == "/robots.txt":
            return HttpResponse(404, {"content-type": "text/plain"}, b"", 0)
        return HttpResponse(200, {"content-type": "text/html"}, self.body, len(self.body))


class FakeModel:
    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        return json.dumps(
            {"strategy": "css", "record_selector": "article", "fields": {"name": "h2::text"}}
        )


def test_discovery_subprocess_memory_reuse_and_no_secret_leakage(
    tmp_path: Path, monkeypatch, capsys
):
    (tmp_path / "doc.json").write_text('{"items":[{"name":"Lamp","priceCurrency":"USD"}]}')
    learn_job = tmp_path / "learn.toml"
    learn_job.write_text(
        """
format_version = 1
discover_recipes = true
recipe_memory = "memory.db"
discovery_aliases = { currency = ["priceCurrency"] }

[[sources]]
path = "doc.json"
representation = "json"
url = "https://example.com/api"

[[fields]]
name = "name"
type = "string"

[[fields]]
name = "currency"
type = "string"
"""
    )
    proc = subprocess.run(
        [sys.executable, "-m", "rastro.cli", "job", "run", str(learn_job)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["results"][0]["learned_recipe"] is not None

    reuse_job = tmp_path / "reuse.toml"
    reuse_job.write_text(learn_job.read_text().replace("discover_recipes = true\n", ""))
    second = run_job_file(reuse_job)
    assert second.results[0].recipe_reused is True

    secret = "sk-ENV-SECRET-NEVER-PRINT-0001"
    monkeypatch.setenv("RASTRO_SECRET_KEY", secret)
    monkeypatch.setenv("RASTRO_MODEL_URL", "http://127.0.0.1:11434/v1")
    provider = _build_model_provider(
        OpenAICompatibleProvider(
            model="local-m", base_url_env="RASTRO_MODEL_URL", api_key_env="RASTRO_SECRET_KEY"
        )
    )
    assert secret not in repr(provider)
    assert _build_model_provider(OllamaProvider(model="qwen3:4b")) is not None
    monkeypatch.delenv("RASTRO_MODEL_URL")
    with pytest.raises(ConfigurationError):
        _build_model_provider(OpenAICompatibleProvider(model="m", base_url_env="RASTRO_MODEL_URL"))

    import rastro as rastro_pkg

    def raise_missing_extra(_name: str) -> object:
        raise ImportError("OllamaModel requires: pip install 'rastro-engine[ollama]'")

    monkeypatch.setattr(rastro_pkg, "__getattr__", raise_missing_extra)
    monkeypatch.delitem(rastro_pkg.__dict__, "OllamaModel", raising=False)
    with pytest.raises(ConfigurationError, match="rastro-engine"):
        _build_model_provider(OllamaProvider(model="qwen3:4b"))

    assert main(["--verbose", "job", "run", str(reuse_job)]) == 0
    logged = capsys.readouterr()
    assert secret not in logged.out and secret not in logged.err


@pytest.mark.asyncio
async def test_http_and_mixed_jobs_run_offline(tmp_path: Path):
    html_bytes = b'<script type="application/ld+json">{"@type":"Product","name":"Bag"}</script>'
    batch_job = parse_job(
        """
format_version = 1

[region]
locale = "es-UY"

[acquisition]
allowed_hosts = ["example.com"]
min_interval_seconds = 0

[recipe]
strategy = "jsonld"
item_type = "Product"
fields = { name = "/name" }

[[sources]]
url = "https://example.com/a"

[[sources]]
url = "https://example.com/b"

[[fields]]
name = "name"
type = "string"
""",
        format="toml",
    )
    assert batch_job.acquisition is not None
    prepared_web = prepare_job(
        batch_job, base_dir=tmp_path, allow_absolute_paths=False, acquisition=None, model=None
    )
    assert prepared_web.client is not None

    transport = FakeTransport(html_bytes)
    acquirer = WebAcquirer(batch_job.acquisition, transport=transport, resolver=FakeResolver())
    batch_report = await run_job_async(batch_job, acquisition=acquirer)
    assert batch_report.exit_code == JobExitCode.SUCCESS and len(batch_report.results) == 2
    assert any(h.get("Accept-Language") == "es-UY" for h in transport.headers_seen)

    (tmp_path / "local.html").write_text("<article><h2>Hat</h2></article>")
    mixed_job = parse_job(
        """
format_version = 1
discover_recipes = true

[acquisition]
allowed_hosts = ["example.com"]
min_interval_seconds = 0

[[sources]]
path = "local.html"

[[sources]]
url = "https://example.com/a"

[sources.recipe]
strategy = "jsonld"
item_type = "Product"
fields = { name = "/name" }

[[fields]]
name = "name"
type = "string"
""",
        format="toml",
    )
    mixed_report = await run_job_async(
        mixed_job, base_dir=tmp_path, acquisition=acquirer, model=FakeModel()
    )
    assert [r.status for r in mixed_report.results] == [Outcome.SUCCESS, Outcome.SUCCESS]
    assert [r.records[0].data.name for r in mixed_report.results] == ["Hat", "Bag"]
