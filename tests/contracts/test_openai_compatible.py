import asyncio
import json
import logging

import httpx
import pytest

from rastro import ConfigurationError, InferencePolicy, ModelError, ModelRequest
from rastro.adapters.openai_compatible import OpenAICompatibleModel
from rastro.domain.intelligence import proposal_schema

REQUEST = ModelRequest(system="Trusted instructions", content='{"untrusted_html":"<h1>hi</h1>"}')
POLICY = InferencePolicy()
KEY = "sk-synthetic-SECRET-0123456789"


def completion(content="{}", *, finish_reason="stop", **message):
    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content, **message},
            }
        ]
    }


def response(value, *, status=200, headers=None):
    body = value if isinstance(value, bytes) else json.dumps(value).encode()
    return httpx.Response(status, stream=httpx.ByteStream(body), headers=headers)


def install_transport(monkeypatch, handler):
    client = httpx.AsyncClient
    configurations = []

    def create(**kwargs):
        configurations.append(kwargs)
        return client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", create)
    return configurations


@pytest.mark.asyncio
async def test_chat_completions_contract_uses_json_schema_and_no_tools(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return response(completion())

    configurations = install_transport(monkeypatch, handle)
    model = OpenAICompatibleModel("llama3.2:3b")
    assert await model.generate(REQUEST, InferencePolicy(max_output_tokens=321)) == "{}"
    request = requests[0]
    assert str(request.url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert request.method == "POST"
    assert request.headers["Accept-Encoding"] == "identity"
    assert "authorization" not in request.headers
    payload = json.loads(request.content)
    assert payload == {
        "model": "llama3.2:3b",
        "stream": False,
        "messages": [
            {"role": "system", "content": REQUEST.system},
            {"role": "user", "content": REQUEST.content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "rastro_recipe_proposal",
                "strict": False,
                "schema": proposal_schema(),
            },
        },
        "temperature": 0,
        "max_tokens": 321,
        "n": 1,
    }
    assert not {"tools", "tool_choice", "functions", "function_call"} & set(payload)
    assert configurations == [{"trust_env": False, "follow_redirects": False, "timeout": 30.0}]


@pytest.mark.asyncio
async def test_json_object_fallback_carries_schema_in_trusted_instructions(monkeypatch):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return response(completion())

    install_transport(monkeypatch, handle)
    await OpenAICompatibleModel("local", response_format="json_object").generate(REQUEST, POLICY)
    payload = requests[0]
    assert payload["response_format"] == {"type": "json_object"}
    system, user = payload["messages"]
    assert system["content"].startswith(REQUEST.system)
    assert json.dumps(proposal_schema()) in system["content"]
    assert user == {"role": "user", "content": REQUEST.content}


def test_unknown_response_format_is_rejected():
    with pytest.raises(ConfigurationError):
        OpenAICompatibleModel("local", response_format="text")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "remote", "expected"),
    [
        ("http://127.0.0.1:1234/v1", False, "http://127.0.0.1:1234/v1/chat/completions"),
        ("http://127.0.0.2:8080/", False, "http://127.0.0.2:8080/chat/completions"),
        ("http://[::1]:8000/v1/", False, "http://[::1]:8000/v1/chat/completions"),
        ("https://api.openai.com/v1", True, "https://api.openai.com/v1/chat/completions"),
        ("https://openrouter.ai/api/v1", True, "https://openrouter.ai/api/v1/chat/completions"),
        ("https://10.0.0.5:8000/v1", True, "https://10.0.0.5:8000/v1/chat/completions"),
    ],
)
async def test_supported_endpoints(monkeypatch, url, remote, expected):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return response(completion())

    install_transport(monkeypatch, handle)
    await OpenAICompatibleModel("gpt-x", base_url=url, allow_remote=remote).generate(
        REQUEST, POLICY
    )
    assert calls == [expected]


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://localhost:1234/v1",
        "http://10.0.0.1:8000/v1",
        "https://[::ffff:127.0.0.1]/v1",
    ],
)
def test_non_loopback_endpoints_require_explicit_remote_opt_in(url):
    with pytest.raises(ConfigurationError, match="allow_remote=True"):
        OpenAICompatibleModel("gpt-x", base_url=url)


@pytest.mark.parametrize("url", ["http://api.openai.com/v1", "http://10.0.0.1:8000/v1"])
def test_remote_endpoints_require_https(url):
    with pytest.raises(ConfigurationError, match="HTTPS"):
        OpenAICompatibleModel("gpt-x", base_url=url, allow_remote=True)


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/socket",
        "ftp://127.0.0.1/v1",
        "http://user:secret@127.0.0.1/v1",
        "http://127.0.0.1/v1?token=secret",
        "http://127.0.0.1/v1#fragment",
        "http://127.0.0.1:0/v1",
        "http://127.0.0.1:65536/v1",
        "http://127.0.0.1/v1/../admin",
        "http://127.0.0.1/v 1",
        "http:///v1",
    ],
)
def test_unsafe_origins_are_rejected(url):
    with pytest.raises(ConfigurationError):
        OpenAICompatibleModel("local", base_url=url, allow_remote=True)


@pytest.mark.parametrize(
    "name",
    ["", "model\nAuthorization:secret", "model name", "-flag", "x" * 201],
    ids=["empty", "header-injection", "space", "leading-dash", "too-long"],
)
def test_model_name_is_validated(name):
    with pytest.raises(ConfigurationError):
        OpenAICompatibleModel(name)


def test_cloud_routed_names_need_remote_opt_in_on_loopback():
    with pytest.raises(ConfigurationError, match="allow_remote"):
        OpenAICompatibleModel("gpt-oss:120b-cloud")
    OpenAICompatibleModel("gpt-oss:120b-cloud", allow_remote=True)


@pytest.mark.asyncio
async def test_api_key_is_read_from_environment_and_never_exposed(monkeypatch, caplog):
    monkeypatch.setenv("RASTRO_TEST_KEY", KEY)
    seen = []

    def handle(request):
        seen.append(request.headers["authorization"])
        return response({"error": {"message": KEY}}, status=401)

    install_transport(monkeypatch, handle)
    model = OpenAICompatibleModel(
        "gpt-x",
        base_url="https://api.example.test/v1",
        allow_remote=True,
        api_key_env="RASTRO_TEST_KEY",
    )
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ModelError) as raised:
        await model.generate(REQUEST, POLICY)
    assert seen == [f"Bearer {KEY}"]
    assert raised.value.code == "model_unavailable"
    for text in (repr(model), str(model), str(raised.value), repr(raised.value), caplog.text):
        assert KEY not in text and "SECRET" not in text
    assert "RASTRO_TEST_KEY" in repr(model)
    assert KEY not in json.dumps(vars(model), default=repr)


@pytest.mark.asyncio
async def test_key_rotation_is_read_per_request_and_removal_fails_safely(monkeypatch):
    monkeypatch.setenv("RASTRO_TEST_KEY", "first")
    seen = []

    def handle(request):
        seen.append(request.headers["authorization"])
        return response(completion())

    install_transport(monkeypatch, handle)
    model = OpenAICompatibleModel("gpt-x", api_key_env="RASTRO_TEST_KEY")
    monkeypatch.setenv("RASTRO_TEST_KEY", "second")
    await model.generate(REQUEST, POLICY)
    assert seen == ["Bearer second"]
    monkeypatch.delenv("RASTRO_TEST_KEY")
    with pytest.raises(ModelError, match=r"^model_unavailable$"):
        await model.generate(REQUEST, POLICY)
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("name", "value"),
    [("RASTRO_TEST_KEY", None), ("RASTRO_TEST_KEY", "bad key\r\nX: y"), ("1BAD", "x")],
    ids=["missing", "header-injection", "invalid-name"],
)
def test_api_key_reference_is_validated_without_echoing_values(monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError) as raised:
        OpenAICompatibleModel("gpt-x", api_key_env=name)
    assert value is None or value not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 307, 400, 401, 403, 404, 429, 500, 503])
async def test_http_errors_never_redirect_retry_or_leak_provider_content(monkeypatch, status):
    calls = []

    def handle(request):
        calls.append(request)
        return response(
            {"error": {"message": "SECRET"}},
            status=status,
            headers={"location": "https://evil.test"},
        )

    install_transport(monkeypatch, handle)
    with pytest.raises(ModelError, match=r"^model_unavailable$"):
        await OpenAICompatibleModel("local").generate(REQUEST, POLICY)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        b"not JSON SECRET",
        b'{"choices":[],"choices":[]}',
        b"\xff",
        b'{"choices":NaN}',
        [],
        {"error": {"message": "SECRET"}},
        {"choices": []},
        {"choices": "x"},
        {"choices": [1]},
        {"choices": completion()["choices"] * 2},
        completion(finish_reason="length"),
        completion(finish_reason="content_filter"),
        completion(finish_reason="tool_calls"),
        completion(finish_reason=None),
        completion(None, refusal="I can't help with that"),
        completion("{}", refusal="I can't help with that"),
        completion(tool_calls=[{"type": "function", "function": {"name": "fetch"}}]),
        completion(function_call={"name": "fetch", "arguments": "{}"}),
        completion(123),
        {"choices": [{"finish_reason": "stop", "message": {"role": "user", "content": "{}"}}]},
        {"choices": [{"finish_reason": "stop", "message": "{}"}]},
        {**completion(), "usage": {"completion_tokens": 2049}},
    ],
)
async def test_malformed_refused_or_incomplete_responses_are_rejected(monkeypatch, value):
    install_transport(monkeypatch, lambda request: response(value))
    with pytest.raises(ModelError, match=r"^invalid_model_response$"):
        await OpenAICompatibleModel("local").generate(REQUEST, POLICY)


@pytest.mark.asyncio
async def test_reported_usage_within_budget_is_accepted(monkeypatch):
    value = {**completion('{"strategy":"css"}'), "usage": {"completion_tokens": 12}}
    install_transport(monkeypatch, lambda request: response(value))
    assert await OpenAICompatibleModel("local").generate(REQUEST, POLICY) == '{"strategy":"css"}'


@pytest.mark.asyncio
async def test_oversized_body_closes_stream(monkeypatch):
    class Chunks(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"x" * 8
            yield b"x" * 8
            pytest.fail("Read beyond budget")

        async def aclose(self):
            self.closed = True

    stream = Chunks()
    install_transport(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    with pytest.raises(ModelError, match=r"^model_response_limit$"):
        await OpenAICompatibleModel("local").generate(
            REQUEST, InferencePolicy(max_response_bytes=10)
        )
    assert stream.closed


@pytest.mark.asyncio
async def test_compressed_response_is_rejected_before_decoding(monkeypatch):
    install_transport(
        monkeypatch, lambda request: response(b"SECRET", headers={"content-encoding": "gzip"})
    )
    with pytest.raises(ModelError, match=r"^invalid_model_response$"):
        await OpenAICompatibleModel("local").generate(REQUEST, POLICY)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError("SECRET"), httpx.ReadTimeout("SECRET")])
async def test_transport_errors_are_safe(monkeypatch, error):
    def handle(request):
        raise error

    install_transport(monkeypatch, handle)
    with pytest.raises(ModelError) as raised:
        await OpenAICompatibleModel("local").generate(REQUEST, POLICY)
    assert raised.value.code == (
        "model_timeout" if isinstance(error, httpx.ReadTimeout) else "model_unavailable"
    )
    assert "SECRET" not in str(raised.value)


@pytest.mark.asyncio
async def test_context_limit_prevents_transport_creation(monkeypatch):
    configurations = install_transport(
        monkeypatch, lambda request: pytest.fail("Network attempted")
    )
    with pytest.raises(ModelError, match=r"^model_context_limit$"):
        await OpenAICompatibleModel("local").generate(REQUEST, InferencePolicy(max_context_bytes=1))
    assert not configurations


class Waiting(httpx.AsyncByteStream):
    def __init__(self, entered):
        self.entered = entered
        self.closed = False

    async def __aiter__(self):
        self.entered.set()
        await asyncio.Event().wait()
        yield b"{}"

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_deadline_covers_a_stalled_body_and_closes_it(monkeypatch):
    stream = Waiting(asyncio.Event())
    install_transport(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    with pytest.raises(ModelError, match=r"^model_timeout$"):
        await OpenAICompatibleModel("local").generate(
            REQUEST, InferencePolicy(timeout_seconds=0.05)
        )
    assert stream.closed


@pytest.mark.asyncio
async def test_concurrency_budget_queues_calls_within_the_deadline(monkeypatch):
    entered = asyncio.Event()
    stream = Waiting(entered)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=stream)

    install_transport(monkeypatch, handle)
    model = OpenAICompatibleModel("local")
    first = asyncio.create_task(model.generate(REQUEST, InferencePolicy(max_concurrency=1)))
    await asyncio.wait_for(entered.wait(), 2)
    # The second call waits for the only slot and times out without sending a request.
    with pytest.raises(ModelError, match=r"^model_timeout$"):
        await model.generate(REQUEST, InferencePolicy(max_concurrency=1, timeout_seconds=0.05))
    assert len(requests) == 1
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert stream.closed


@pytest.mark.asyncio
async def test_cancellation_closes_response_without_swallowing_it(monkeypatch):
    entered = asyncio.Event()
    stream = Waiting(entered)
    install_transport(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    task = asyncio.create_task(OpenAICompatibleModel("local").generate(REQUEST, POLICY))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


def test_lazy_public_export_and_missing_extra_message(monkeypatch):
    import builtins

    import rastro

    assert rastro.OpenAICompatibleModel is OpenAICompatibleModel
    assert "OpenAICompatibleModel" not in rastro.__all__
    with pytest.raises(AttributeError):
        rastro.NotAProvider  # noqa: B018

    real_import = builtins.__import__

    def without_httpx(name, *args, **kwargs):
        if name == "httpx":
            raise ModuleNotFoundError("No module named 'httpx'", name="httpx")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(__import__("sys").modules, "rastro.adapters.ollama", raising=False)
    monkeypatch.setattr(builtins, "__import__", without_httpx)
    with pytest.raises(ImportError, match=r"rastro-engine\[ollama\]"):
        rastro.OllamaModel  # noqa: B018
