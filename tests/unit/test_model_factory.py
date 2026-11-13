import json
import sys
import time

import httpx
import pytest

import rastro
from rastro import ConfigurationError, InferencePolicy, ModelError, ModelRequest
from rastro.adapters.callable_model import CallableModel
from rastro.adapters.command_model import CommandModel
from rastro.adapters.openai_compatible import OpenAICompatibleModel
from rastro.domain.model_spec import parse_model_spec

REQUEST = ModelRequest(system="Trusted", content="{}")
POLICY = InferencePolicy(timeout_seconds=5)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("RASTRO_MODEL", "RASTRO_MODEL_ALLOW_REMOTE", "RASTRO_MODEL_LOCAL"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("http:llama3.2:3b", {"provider": "http", "model": "llama3.2:3b"}),
        (
            "http:org/model@v2@https://gw.example/v1",
            {"provider": "http", "model": "org/model@v2", "base_url": "https://gw.example/v1"},
        ),
        ("http:org/model@v2", {"provider": "http", "model": "org/model@v2"}),
        ("ollama:qwen3:4b", {"provider": "http", "model": "qwen3:4b"}),
        ("command", {"provider": "command"}),
        (
            "command:claude -p --output-format json",
            {"provider": "command", "argv": ["claude", "-p", "--output-format", "json"]},
        ),
        # Shell syntax is not interpreted: it becomes literal arguments.
        (
            "command:llm 'a b' ; rm -rf / | cat $(id)",
            {
                "provider": "command",
                "argv": ["llm", "a b", ";", "rm", "-rf", "/", "|", "cat", "$(id)"],
            },
        ),
    ],
)
def test_spec_strings(spec, expected):
    assert parse_model_spec(spec) == expected


@pytest.mark.parametrize("spec", ["", "gpt-4", "magic:thing", "http:", "command:'unbalanced"])
def test_unknown_specs_list_the_valid_forms(spec):
    with pytest.raises(ConfigurationError) as error:
        rastro.model(spec)
    if spec != "command:'unbalanced":
        assert "http:<model>@<base_url>" in str(error.value)


@pytest.mark.asyncio
async def test_command_spec_needs_an_opt_in_and_never_runs_a_shell(tmp_path, monkeypatch):
    with pytest.raises(ConfigurationError, match="allow_remote"):
        rastro.model("command:claude -p")
    marker = tmp_path / "pwned"
    model = rastro.model(f"command:echo hi ; touch {marker}", local=True)
    assert isinstance(model, CommandModel)
    with pytest.raises(ModelError):  # echo prints no JSON; ';' and 'touch' are echo's arguments
        await model.generate(REQUEST, POLICY)
    assert not marker.exists()


def test_http_spec_builds_the_generic_adapter_and_keeps_remote_rules():
    model = rastro.model("ollama:llama3.2:3b")
    assert isinstance(model, OpenAICompatibleModel)
    assert "127.0.0.1:11434/v1" in repr(model)
    with pytest.raises(ConfigurationError):
        rastro.model("http:gpt-x@https://api.example.com/v1")
    remote = rastro.model("http:gpt-x@https://api.example.com/v1", allow_remote=True)
    assert "api.example.com" in repr(remote)
    named = rastro.model("http", model="m", base_url="http://127.0.0.1:1234/v1")
    assert "1234" in repr(named)
    with pytest.raises(ConfigurationError, match="Invalid model option"):
        rastro.model("http", model="m", tools=["browse"])


@pytest.mark.asyncio
async def test_http_transport_round_trip(monkeypatch):
    def handle(request):
        body = json.loads(request.content)
        assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}
        message = {"role": "assistant", "content": '{"ok": true}'}
        payload = {"choices": [{"index": 0, "finish_reason": "stop", "message": message}]}
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(payload).encode()))

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(handle), **kw),
    )
    request = REQUEST.model_copy(update={"response_schema": {"type": "object"}})
    model = rastro.model("ollama:local")
    assert await model.generate(request, POLICY) == '{"ok": true}'


def test_environment_spec_and_opt_in(monkeypatch):
    with pytest.raises(ConfigurationError, match="RASTRO_MODEL"):
        rastro.model()
    monkeypatch.setenv("RASTRO_MODEL", f"command:{sys.executable} -c pass")
    with pytest.raises(ConfigurationError):
        rastro.model()
    monkeypatch.setenv("RASTRO_MODEL_LOCAL", "1")
    assert isinstance(rastro.model(), CommandModel)
    monkeypatch.setenv("RASTRO_MODEL", "http:gpt-x@https://api.example.com/v1")
    with pytest.raises(ConfigurationError):
        rastro.model()
    monkeypatch.setenv("RASTRO_MODEL_ALLOW_REMOTE", "true")
    assert isinstance(rastro.model(), OpenAICompatibleModel)


def test_model_ports_pass_through_and_functions_are_wrapped():
    class Port:
        async def generate(self, request, policy):
            return "{}"

    port = Port()
    assert rastro.model(port) is port
    assert isinstance(rastro.model(lambda request: "{}"), CallableModel)
    with pytest.raises(ConfigurationError):
        rastro.model(port, allow_remote=True)
    with pytest.raises(ConfigurationError):
        rastro.model(42)


@pytest.mark.asyncio
async def test_callable_transport_budgets():
    async def answer(request):
        assert request.system == "Trusted"
        return '{"a": 1}'

    assert await CallableModel(answer).generate(REQUEST, POLICY) == '{"a": 1}'
    assert await CallableModel(lambda r: "{}").generate(REQUEST, POLICY) == "{}"

    def slow(request):
        time.sleep(1)
        return "{}"

    cases = [
        (slow, InferencePolicy(timeout_seconds=0.1), "model_timeout"),
        (lambda r: {"not": "text"}, POLICY, "invalid_model_response"),
        (lambda r: "x" * 100, InferencePolicy(max_response_bytes=10), "model_response_limit"),
        (lambda r: "{}", InferencePolicy(max_context_bytes=1), "model_context_limit"),
    ]
    for function, policy, code in cases:
        with pytest.raises(ModelError) as error:
            await CallableModel(function).generate(REQUEST, policy)
        assert error.value.code == code
    with pytest.raises(ConfigurationError):
        CallableModel("not callable")  # type: ignore[arg-type]
