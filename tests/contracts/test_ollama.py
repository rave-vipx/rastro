import asyncio
import json

import httpx
import pytest

from rastro import ConfigurationError, InferencePolicy, ModelError, ModelRequest
from rastro.adapters.ollama import OllamaModel

REQUEST = ModelRequest(system="Trusted instructions", content='{"untrusted_html":"<h1>hi</h1>"}')
POLICY = InferencePolicy()


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
async def test_structured_chat_contract_and_no_ambient_network_options(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return response({"done": True, "message": {"role": "assistant", "content": "{}"}})

    configurations = install_transport(monkeypatch, handle)
    assert await OllamaModel("local-model:1").generate(REQUEST, POLICY) == "{}"
    request = requests[0]
    assert str(request.url) == "http://127.0.0.1:11434/api/chat"
    assert request.method == "POST" and request.headers["Accept-Encoding"] == "identity"
    payload = json.loads(request.content)
    assert payload["model"] == "local-model:1" and payload["stream"] is False
    assert payload["messages"] == [
        {"role": "system", "content": REQUEST.system},
        {"role": "user", "content": REQUEST.content},
    ]
    assert payload["format"]["additionalProperties"] is False
    assert set(payload["format"]["properties"]) == {
        "strategy",
        "fields",
        "item_type",
        "record_selector",
    }
    assert payload["options"] == {"temperature": 0, "num_predict": 2048, "num_ctx": 8192}
    assert "tools" not in payload
    assert configurations == [{"trust_env": False, "follow_redirects": False, "timeout": 30.0}]


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "https://example.com",
        "http://169.254.169.254",
        "http://10.0.0.1:11434",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "file:///tmp/socket",
        "http://user:secret@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1?token=secret",
        "http://127.0.0.1#fragment",
        "http://[::ffff:127.0.0.1]",
    ],
)
def test_endpoint_rejects_nonliteral_nonloopback_and_unsafe_origins(url):
    with pytest.raises(ConfigurationError):
        OllamaModel("local", base_url=url)


@pytest.mark.parametrize(
    "name", ["", "model\nAuthorization:secret", "model-cloud", "foo:cloud", "x" * 201]
)
def test_model_name_is_bounded_and_local(name):
    with pytest.raises(ConfigurationError):
        OllamaModel(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://[::1]:11434/", "https://127.0.0.1"])
async def test_loopback_origins_are_supported(monkeypatch, url):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return response({"done": True, "message": {"role": "assistant", "content": "{}"}})

    install_transport(monkeypatch, handle)
    await OllamaModel("local", base_url=url).generate(REQUEST, POLICY)
    assert calls == [url.rstrip("/") + "/api/chat"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 400, 404, 429, 500])
async def test_http_errors_never_redirect_retry_or_leak_provider_content(monkeypatch, status):
    calls = []

    def handle(request):
        calls.append(request)
        return response(
            {"error": "SECRET"}, status=status, headers={"location": "https://evil.test"}
        )

    install_transport(monkeypatch, handle)
    with pytest.raises(ModelError, match=r"^model_unavailable$"):
        await OllamaModel("local").generate(REQUEST, POLICY)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        b"not JSON SECRET",
        b'{"done":true,"done":false}',
        b"\xff",
        b'{"done":NaN}',
        [],
        {"done": False},
        {"done": True, "error": "SECRET"},
        {"done": True, "done_reason": "length"},
        {"done": True, "message": {"role": "user", "content": "{}"}},
        {"done": True, "message": {"role": "assistant", "content": 123}},
        {
            "done": True,
            "message": {"role": "assistant", "content": "{}", "tool_calls": [{"name": "fetch"}]},
        },
    ],
)
async def test_untrusted_or_incomplete_response_is_rejected(monkeypatch, value):
    install_transport(monkeypatch, lambda request: response(value))
    with pytest.raises(ModelError, match=r"^invalid_model_response$"):
        await OllamaModel("local").generate(REQUEST, POLICY)


@pytest.mark.asyncio
async def test_raw_body_limit_closes_stream(monkeypatch):
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
        await OllamaModel("local").generate(REQUEST, InferencePolicy(max_response_bytes=10))
    assert stream.closed


@pytest.mark.asyncio
async def test_compressed_response_is_rejected_before_decoding(monkeypatch):
    install_transport(
        monkeypatch, lambda request: response(b"SECRET", headers={"content-encoding": "gzip"})
    )
    with pytest.raises(ModelError, match=r"^invalid_model_response$"):
        await OllamaModel("local").generate(REQUEST, POLICY)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError("SECRET"), httpx.ReadTimeout("SECRET")])
async def test_transport_errors_are_safe(monkeypatch, error):
    def handle(request):
        raise error

    install_transport(monkeypatch, handle)
    with pytest.raises(ModelError) as raised:
        await OllamaModel("local").generate(REQUEST, POLICY)
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
        await OllamaModel("local").generate(REQUEST, InferencePolicy(max_context_bytes=1))
    assert not configurations


@pytest.mark.asyncio
async def test_cancellation_closes_response_without_swallowing_it(monkeypatch):
    entered = asyncio.Event()

    class Waiting(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"{}"

        async def aclose(self):
            self.closed = True

    stream = Waiting()
    install_transport(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    task = asyncio.create_task(OllamaModel("local").generate(REQUEST, POLICY))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed
