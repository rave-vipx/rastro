import pytest
from pydantic import ValidationError

from rastro import Recipe
from rastro.domain.memory import RecipeContext, RecipeKey


def test_identity_is_deterministic_and_omits_url_secrets(source, schema):
    key = RecipeKey.for_source(source, schema)
    assert key == RecipeKey.for_source(source, schema)
    assert "https" not in key.canonical() and "private" not in key.canonical()
    changed = source.model_copy(update={"url": source.url.split("#")[0] + "&other=1"})
    assert RecipeKey.for_source(changed, schema) != key
    assert RecipeKey.for_source(source.model_copy(update={"url": source.url + "#x"}), schema) == key
    assert RecipeKey.for_source(source, schema, RecipeContext(schema_revision="2")) != key


def test_fallbacks_are_bounded_flat_declarative_and_versioned(recipe):
    plan = recipe.model_copy(update={"fallbacks": (recipe,)})
    assert Recipe.model_validate_json(plan.model_dump_json()) == plan
    for fallbacks in [(plan,), (recipe,) * 4, (recipe.model_copy(update={"version": 2}),)]:
        with pytest.raises(ValidationError):
            Recipe.model_validate({**recipe.model_dump(), "fallbacks": fallbacks})
