import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from rastro import (
    AcquisitionPolicy,
    ConfigurationError,
    Limits,
    Outcome,
    Rastro,
    Recipe,
    RecipeKey,
    RecipeNotFoundError,
    ResourceLimitError,
    SQLiteRecipeRepository,
)
from rastro.application.acquisition import WebAcquirer
from tests.integration.test_acquisition import Resolver, Transport, response

API = json.dumps(
    {
        "data": {
            "items": [
                {"name": "Notebook", "pricing": {"price": "12.50"}},
                {"name": "Pen", "pricing": {"price": "1.20"}},
            ]
        }
    }
)
RECIPE = Recipe(
    strategy="json", record_path="/data/items", fields={"name": "/name", "price": "/pricing/price"}
)


def json_response(body=API, content_type="application/json", status=200):
    data = body.encode()
    return response(status, data, **{"content-type": content_type})


def web(transport, **kwargs):
    policy = AcquisitionPolicy(
        allowed_hosts=("example.com",), min_interval_seconds=0, backoff_seconds=0.001
    )
    acquirer = WebAcquirer(policy, transport=transport, resolver=Resolver())
    return Rastro(acquisition=acquirer, policy=policy, **kwargs)


def test_supplied_json_is_validated_with_json_provenance(source, schema, caplog):
    with caplog.at_level(logging.INFO, logger="rastro"):
        result = Rastro().extract_json(API, source=source, schema=schema, recipe=RECIPE)
    assert result.status == Outcome.SUCCESS
    assert [r.data.name for r in result.records] == ["Notebook", "Pen"]
    evidence = result.records[1].evidence["price"]
    assert evidence.method == "json"
    assert evidence.locator == "json#/data/items/1/pricing/price"
    assert evidence.source_url == "https://example.com/products"
    assert result.metrics.acquisition_method == "provided_json"
    assert result.metrics.fields_validated == 4
    assert "Notebook" not in caplog.text and "private" not in caplog.text


@pytest.mark.parametrize(
    ("body", "status", "codes"),
    [
        (
            '{"data": {"items": [{"name": "A", "pricing": {"price": "1"}}, {"name": ""}]}}',
            Outcome.PARTIAL_SUCCESS,
            {"validation_error"},
        ),
        ('{"data": {"items": [{"name": ""}]}}', Outcome.INVALID_DATA, {"validation_error"}),
        ('{"data": {"items": [7]}}', Outcome.INVALID_DATA, {"invalid_record"}),
        ('{"data": {"items": []}}', Outcome.MISSING_DATA, set()),
        ('{"data": {"items": [{}]}}', Outcome.MISSING_DATA, {"missing_fields"}),
        ('{"data": {}}', Outcome.MISSING_DATA, {"record_path_not_found"}),
        ('{"data": {"items": 3}}', Outcome.INVALID_DATA, {"invalid_record_path"}),
        ('{"data": 1, "data": 2}', Outcome.INVALID_DATA, {"invalid_json"}),
        ("not json", Outcome.INVALID_DATA, {"invalid_json"}),
    ],
)
def test_json_outcomes_are_explicit(source, schema, body, status, codes):
    result = Rastro().extract_json(body, source=source, schema=schema, recipe=RECIPE)
    assert result.status == status
    assert {issue.code for issue in result.issues} == codes
    assert status != Outcome.SUCCESS or result.records


def test_required_evidence_reports_insufficient_evidence(source, schema):
    recipe = RECIPE.model_copy(update={"fields": {"name": "/name"}, "require_evidence": True})
    result = Rastro().extract_json(API, source=source, schema=schema, recipe=recipe)
    assert result.status == Outcome.INSUFFICIENT_EVIDENCE
    assert {issue.code for issue in result.issues} == {"missing_evidence"}


def test_representation_mismatch_and_limits_raise(source, schema, recipe, html):
    with pytest.raises(ConfigurationError):
        Rastro().extract_json(API, source=source, schema=schema, recipe=recipe)
    with pytest.raises(ConfigurationError):
        Rastro().extract_html(html, source=source, schema=schema, recipe=RECIPE)
    with pytest.raises(ResourceLimitError):
        Rastro(limits=Limits(max_json_bytes=10)).extract_json(
            API, source=source, schema=schema, recipe=RECIPE
        )
    with pytest.raises(ResourceLimitError):
        Rastro(limits=Limits(max_json_depth=2)).extract_json(
            API, source=source, schema=schema, recipe=RECIPE
        )


@pytest.mark.asyncio
async def test_discovery_learns_persists_and_reuses_after_restart(tmp_path, source, schema):
    path = tmp_path / "recipes.db"
    first = await Rastro(
        repository=SQLiteRecipeRepository(path), discover_recipes=True
    ).extract_json_async(API, source=source, schema=schema)
    assert first.status == Outcome.SUCCESS
    assert first.learned_recipe == RECIPE.model_copy(update={"require_evidence": True})
    assert [a.origin for a in first.recipe_attempts] == ["discovery"]
    assert first.metrics.acquisition_method == "provided_json"

    # A new repository object simulates a process restart against the same file.
    restarted = Rastro(repository=SQLiteRecipeRepository(path))
    second = restarted.extract_json(API, source=source, schema=schema)
    assert second.status == Outcome.SUCCESS and second.recipe_reused
    assert [a.origin for a in second.recipe_attempts] == ["memory"]
    assert [r.data for r in second.records] == [r.data for r in first.records]
    # JSON memory never satisfies an HTML request for the same source and schema.
    with pytest.raises(RecipeNotFoundError):
        restarted.extract_html("<p></p>", source=source, schema=schema)
    stored = SQLiteRecipeRepository(path).get(
        RecipeKey.for_source(source, schema, representation="json")
    )
    assert stored is not None and stored.recipe.strategy == "json"


class Model:
    def __init__(self):
        self.requests = []

    async def generate(self, request, policy):
        self.requests.append(request)
        raise AssertionError("JSON documents must not reach the model")


@pytest.mark.asyncio
async def test_structured_discovery_precedes_and_excludes_the_model(source, schema):
    model = Model()
    engine = Rastro(model=model)
    learned = await engine.extract_json_async(API, source=source, schema=schema)
    assert learned.status == Outcome.SUCCESS
    unmatched = await engine.extract_json_async('{"x": [{"y": 1}]}', source=source, schema=schema)
    assert unmatched.status == Outcome.INSUFFICIENT_EVIDENCE
    assert [issue.code for issue in unmatched.issues] == ["no_valid_recipe"]
    assert model.requests == [] and unmatched.metrics.inference_calls == 0


@pytest.mark.asyncio
async def test_memory_precedes_discovery(tmp_path, source, schema):
    repository = SQLiteRecipeRepository(tmp_path / "recipes.db")
    Rastro(repository=repository).extract_json(API, source=source, schema=schema, recipe=RECIPE)
    result = await Rastro(repository=repository, discover_recipes=True).extract_json_async(
        API, source=source, schema=schema
    )
    assert result.recipe_reused and result.learned_recipe is None


@pytest.mark.asyncio
async def test_http_json_acquisition_requests_json_and_validates(source, schema):
    transport = Transport([json_response(content_type="application/vnd.api+json")])
    result = await web(transport).extract(
        source=source, schema=schema, recipe=RECIPE, representation="json"
    )
    assert result.status == Outcome.SUCCESS
    assert result.metrics.acquisition_method == "http"
    assert result.records[0].evidence["name"].method == "json"
    assert transport.calls[-1][1]["headers"]["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_html_acquisition_keeps_the_html_accept_header(source, schema, recipe, html):
    transport = Transport([response(body=html.encode())])
    result = await web(transport).extract(source=source, schema=schema, recipe=recipe)
    assert result.status == Outcome.SUCCESS
    assert transport.calls[-1][1]["headers"]["Accept"] == "text/html"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page", "status", "code"),
    [
        (
            json_response(content_type="text/html"),
            Outcome.PERMANENT_FAILURE,
            "unsupported_content_type",
        ),
        (
            json_response(content_type="text/json"),
            Outcome.PERMANENT_FAILURE,
            "unsupported_content_type",
        ),
        (
            json_response(content_type="application/json; charset=latin-1"),
            Outcome.INVALID_DATA,
            "invalid_encoding",
        ),
        (json_response(body="[" * 100 + "]" * 100), Outcome.PERMANENT_FAILURE, "extraction_limit"),
    ],
)
async def test_http_json_rejections_are_explicit(source, schema, page, status, code):
    result = await web(Transport([page])).extract(
        source=source, schema=schema, recipe=RECIPE, representation="json"
    )
    assert result.status == status
    assert [issue.code for issue in result.issues] == [code]
    assert result.records == ()


@pytest.mark.asyncio
async def test_html_responses_to_json_recipes_fail_before_acquisition(source, schema, recipe):
    transport = Transport([json_response()])
    with pytest.raises(ConfigurationError):
        await web(transport).extract(
            source=source, schema=schema, recipe=recipe, representation="json"
        )
    with pytest.raises(ConfigurationError):
        await web(transport).extract_many([source], schema=schema, recipe=RECIPE)
    assert transport.calls == []


@pytest.mark.asyncio
async def test_batch_json_acquisition_with_discovery(source, schema):
    transport = Transport([json_response(), json_response()])
    results = await web(transport, discover_recipes=True).extract_many(
        [source, source], schema=schema, representation="json"
    )
    assert [r.status for r in results] == [Outcome.SUCCESS, Outcome.SUCCESS]
    assert all(r.records[0].evidence["price"].method == "json" for r in results)


def test_structured_api_example_runs_offline(tmp_path):
    example = Path(__file__).parents[2] / "examples" / "structured_api.py"
    command = [sys.executable, str(example), "--database", str(tmp_path / "recipes.db")]
    first = json.loads(subprocess.check_output(command, text=True, timeout=30))
    second = json.loads(subprocess.check_output(command, text=True, timeout=30))
    assert first["explicit"]["status"] == "success"
    assert first["explicit"]["evidence"]["name"] == "json#/data/products/0/name"
    assert first["discovered"]["recipe_reused"] is False
    assert second["discovered"]["recipe_reused"] is True
    assert first["discovered"]["records"] == second["discovered"]["records"]
    assert first["http"] == {"status": "success", "accepted": 2, "rejected_content_type": True}
