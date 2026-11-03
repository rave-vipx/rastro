"""Opt-in live OpenAI-compatible evidence against a trusted local Ollama ``/v1`` endpoint.

Skipped unless RASTRO_LIVE_OPENAI_MODEL names an installed model. Run:
RASTRO_LIVE_OPENAI_MODEL=llama3.2:3b uv run --extra openai pytest tests/live --no-cov -s
Set RASTRO_LIVE_OPENAI_BASE_URL to target another loopback server (default Ollama).
Model quality is not asserted; the adapter must reach the server and return parseable
proposals, and a successful learn must be reused without another call.
"""

import asyncio
import os

import pytest
from pydantic import BaseModel

from rastro import InferencePolicy, Outcome, Rastro, RecipeKey, Source, SQLiteRecipeRepository
from rastro.adapters.openai_compatible import OpenAICompatibleModel

MODEL = os.environ.get("RASTRO_LIVE_OPENAI_MODEL")
BASE_URL = os.environ.get("RASTRO_LIVE_OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
pytestmark = [
    pytest.mark.skipif(not MODEL, reason="Set RASTRO_LIVE_OPENAI_MODEL to run live inference"),
    pytest.mark.enable_socket,
]
SOURCE = Source(url="https://example.com/live-openai/notebook", locale="en-US")
# No JSON-LD or microdata, so only the model path can succeed.
HTML = """<!doctype html><html><body><main>
  <article class="product-card">
    <h2 class="title">Field Notebook</h2>
    <span class="amount">12.50</span>
  </article>
</main></body></html>"""


class Product(BaseModel):
    name: str
    price: str


@pytest.mark.parametrize("response_format", ["json_schema", "json_object"])
def test_live_learning_through_chat_completions(tmp_path, response_format):
    assert MODEL is not None
    repo = SQLiteRecipeRepository(tmp_path / "recipes.db")
    model = OpenAICompatibleModel(MODEL, base_url=BASE_URL, response_format=response_format)
    policy = InferencePolicy(max_calls=2, max_repair_attempts=1, timeout_seconds=120)

    async def run():
        return await Rastro(
            model=model, repository=repo, inference_policy=policy
        ).extract_html_async(HTML, source=SOURCE, schema=Product)

    learned = asyncio.run(run())
    codes = [issue.code for issue in learned.issues]
    print(
        f"\n{MODEL} {response_format}: {learned.status} calls={learned.metrics.inference_calls} "
        f"issues={codes} recipe={learned.learned_recipe}"
    )
    assert learned.status != Outcome.TRANSIENT_FAILURE, codes
    assert "invalid_model_response" not in codes
    assert learned.metrics.inference_calls >= 1
    if learned.status == Outcome.SUCCESS:
        assert [record.data.model_dump() for record in learned.records] == [
            {"name": "Field Notebook", "price": "12.50"}
        ]
        assert repo.get(RecipeKey.for_source(SOURCE, Product)) is not None
        reused = asyncio.run(run())
        assert reused.recipe_reused and reused.metrics.inference_calls == 0
    else:
        assert learned.records == () and repo.get(RecipeKey.for_source(SOURCE, Product)) is None
