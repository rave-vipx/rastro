"""Declarative pagination and selector boundaries (SYNC-002, SEC-008)."""

import tomllib

import pytest
from pydantic import ValidationError

from rastro.application.recipe_toml import replace_recipe
from rastro.domain.models import ConfigurationError, Recipe
from rastro.domain.selector_policy import check_untrusted_recipe


def recipe(**pagination):
    return Recipe(
        strategy="css",
        record_selector="article",
        fields={"name": "h2::text"},
        pagination={"next": "a[rel=next]::attr(href)", **pagination},
    )


def test_pagination_defaults_and_toml_roundtrip():
    value = recipe()
    assert value.pagination.max_pages == 5
    rendered = replace_recipe('[recipe]\nstrategy = "css"\n', value, source=None)
    assert Recipe.model_validate(tomllib.loads(rendered)["recipe"]) == value


@pytest.mark.parametrize("max_pages", [0, 51, True, "5"])
def test_page_limit_is_strict_and_bounded(max_pages):
    with pytest.raises(ValidationError):
        recipe(max_pages=max_pages)


@pytest.mark.parametrize("next", ["", "a", "expand:a::attr(href)", "document:a::attr(href)"])
def test_css_next_expression_is_bounded_scalar(next):
    with pytest.raises(ValidationError):
        recipe(next=next)


def test_next_selector_cost_policy():
    with pytest.raises(ConfigurationError, match="policy"):
        check_untrusted_recipe(recipe(next="a ~ a::attr(href)"))


@pytest.mark.parametrize("strategy", ["json", "jsonld"])
def test_structured_next_is_json_pointer(strategy):
    data = {"strategy": strategy, "fields": {"name": "/name"}, "pagination": {"next": "/next"}}
    if strategy == "json":
        data["record_path"] = "/items"
    assert Recipe.model_validate(data).pagination.next == "/next"
    data["pagination"] = {"next": "a::attr(href)"}
    with pytest.raises(ValidationError):
        Recipe.model_validate(data)


@pytest.mark.parametrize(
    ("strategy", "body", "expected"),
    [
        ("json", '{"items": [], "links": {"next": "/two"}}', "/two"),
        ("json", '{"items": []}', None),
        ("jsonld", '<script type="application/ld+json">{"links":{"next":"/two"}}</script>', "/two"),
    ],
)
def test_structured_next_reads_page_pointer(strategy, body, expected):
    from rastro.adapters.scrapling import ScraplingParser
    from rastro.adapters.structured import JsonParser
    from rastro.application.pagination import next_url
    from rastro.domain.models import Limits

    data = {
        "strategy": strategy,
        "fields": {"name": "/name"},
        "pagination": {"next": "/links/next"},
    }
    if strategy == "json":
        data["record_path"] = "/items"
    value = Recipe.model_validate(data)
    parser = JsonParser() if strategy == "json" else ScraplingParser()
    assert next_url(body, value, parser, Limits()) == expected


def test_models_cannot_choose_pagination_policy():
    from rastro.domain.intelligence import RecipeProposal

    with pytest.raises(ValidationError):
        RecipeProposal(
            strategy="css",
            record_selector="article",
            fields={"name": "h2::text"},
            pagination={"next": "a::attr(href)"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["css", "jsonld"])
async def test_deterministic_repair_preserves_pagination_and_strategy(schema, source, strategy):
    from rastro.application.repair_candidates import RepairCandidateGenerator
    from rastro.domain.models import Limits

    proposed = (
        Recipe(strategy="css", record_selector="section", fields={"name": "h3::text"})
        if strategy == "css"
        else Recipe(strategy="jsonld", fields={"name": "/name"})
    )

    class Discovery:
        def discover(self, *args, **kwargs):
            return (proposed,)

    original = recipe()
    result = await RepairCandidateGenerator(discovery=Discovery()).generate(
        "<article><h2>Item</h2></article>",
        source=source,
        schema=schema,
        recipe=original,
        limits=Limits(),
    )
    if strategy == "css":
        assert result.candidates[0].recipe.pagination == original.pagination
    else:
        assert not result.candidates


@pytest.mark.parametrize("strategy", ["css", "jsonld"])
def test_model_repair_preserves_pagination_and_strategy(schema, strategy):
    from rastro.application.model_repair import _candidate

    proposed = (
        Recipe(strategy="css", record_selector="section", fields={"name": "h3::text"})
        if strategy == "css"
        else Recipe(strategy="jsonld", fields={"name": "/name"})
    )
    original = recipe()
    candidate = _candidate(proposed, schema, original)
    if strategy == "css":
        assert candidate.recipe.pagination == original.pagination
    else:
        assert candidate is None
