"""End-to-end learning through the OpenAI-compatible adapter against a mock server."""

import json

import httpx
import pytest

from rastro import Outcome, Rastro, RecipeKey, SQLiteRecipeRepository
from rastro.adapters.openai_compatible import OpenAICompatibleModel

PAGE = '<article class="product"><h2>Notebook</h2><b class="price">12.50</b></article>'
PROPOSAL = {
    "strategy": "css",
    "record_selector": ".product",
    "fields": {"name": "h2::text", "price": ".price::text"},
}


def serve(monkeypatch, handler):
    client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs),
    )
    return requests


def reply(status, body):
    # Streamed like a real transport, so the adapter can enforce its byte budget.
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return httpx.Response(status, stream=httpx.ByteStream(content))


def completion(content, finish_reason="stop"):
    message = {"role": "assistant", "content": content}
    return reply(
        200, {"choices": [{"index": 0, "finish_reason": finish_reason, "message": message}]}
    )


@pytest.mark.asyncio
async def test_learning_activates_and_reuses_without_another_call(
    monkeypatch, tmp_path, source, schema
):
    monkeypatch.setenv("RASTRO_TEST_KEY", "sk-synthetic")
    requests = serve(monkeypatch, lambda request: completion(json.dumps(PROPOSAL)))
    repo = SQLiteRecipeRepository(tmp_path / "recipes.db")
    model = OpenAICompatibleModel(
        "gpt-x",
        base_url="https://llm.example.test/v1",
        allow_remote=True,
        api_key_env="RASTRO_TEST_KEY",
    )
    result = await Rastro(model=model, repository=repo).extract_html_async(
        PAGE, source=source, schema=schema
    )
    assert result.status == Outcome.SUCCESS
    assert result.metrics.inference_calls == 1
    assert [record.data.name for record in result.records] == ["Notebook"]
    assert set(result.records[0].evidence) == {"name", "price"}
    assert result.learned_recipe is not None and result.learned_recipe.require_evidence
    assert repo.get(RecipeKey.for_source(source, schema)) is not None
    payload = json.loads(requests[0].content)
    assert "untrusted_html" in json.loads(payload["messages"][1]["content"])
    assert source.url not in requests[0].content.decode()
    reused = await Rastro(model=model, repository=repo).extract_html_async(
        PAGE, source=source, schema=schema
    )
    assert reused.recipe_reused and reused.metrics.inference_calls == 0
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_invalid_candidate_is_corrected_on_the_next_call(monkeypatch, source, schema):
    outputs = iter(['{"strategy":"css"}', json.dumps(PROPOSAL)])
    requests = serve(monkeypatch, lambda request: completion(next(outputs)))
    model = OpenAICompatibleModel("local", response_format="json_object")
    result = await Rastro(model=model).extract_html_async(PAGE, source=source, schema=schema)
    assert result.status == Outcome.SUCCESS and result.metrics.inference_calls == 2
    retry = json.loads(json.loads(requests[1].content)["messages"][1]["content"])
    assert retry["last_failure"] == "invalid_model_candidate"
    assert '{"strategy":"css"}' not in requests[1].content.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "status", "issue"),
    [
        (
            reply(503, {"error": "busy"}),
            Outcome.TRANSIENT_FAILURE,
            "model_unavailable",
        ),
        (
            completion("{}", "content_filter"),
            Outcome.INSUFFICIENT_EVIDENCE,
            "invalid_model_response",
        ),
        (
            reply(200, b"x" * 70_000),
            Outcome.INSUFFICIENT_EVIDENCE,
            "model_response_limit",
        ),
    ],
)
async def test_provider_failures_become_explicit_outcomes(
    monkeypatch, tmp_path, source, schema, response, status, issue
):
    serve(monkeypatch, lambda request: response)
    repo = SQLiteRecipeRepository(tmp_path / "recipes.db")
    result = await Rastro(model=OpenAICompatibleModel("local"), repository=repo).extract_html_async(
        PAGE, source=source, schema=schema
    )
    assert result.status == status and result.records == ()
    assert [item.code for item in result.issues] == [issue]
    assert repo.get(RecipeKey.for_source(source, schema)) is None
