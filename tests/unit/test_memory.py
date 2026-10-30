import json

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


def test_regional_identity_is_ordered_and_default_key_remains_legacy_compatible(source, schema):
    key = RecipeKey.for_source(source, schema)
    legacy = {
        "context": key.context.model_dump(),
        "locale": key.locale,
        "schema_fingerprint": key.schema_fingerprint,
        "source_fingerprint": key.source_fingerprint,
        "timezone": key.timezone,
    }
    assert key.canonical() == json.dumps(legacy, sort_keys=True, separators=(",", ":"))
    assert RecipeKey.model_validate(legacy) == key
    assert set(json.loads(key.canonical())) == {
        "context",
        "locale",
        "schema_fingerprint",
        "source_fingerprint",
        "timezone",
    }
    first = source.model_copy(update={"languages": ("es-UY", "en"), "currency": "UYU"})
    second = first.model_copy(update={"languages": ("en", "es-UY")})
    assert RecipeKey.for_source(first, schema) != key
    assert RecipeKey.for_source(second, schema) != RecipeKey.for_source(first, schema)


def test_fallbacks_are_bounded_flat_declarative_and_versioned(recipe):
    plan = recipe.model_copy(update={"fallbacks": (recipe,)})
    assert Recipe.model_validate_json(plan.model_dump_json()) == plan
    for fallbacks in [(plan,), (recipe,) * 4, (recipe.model_copy(update={"version": 2}),)]:
        with pytest.raises(ValidationError):
            Recipe.model_validate({**recipe.model_dump(), "fallbacks": fallbacks})
