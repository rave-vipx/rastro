"""REC-006: recipe write-back keeps job text, and the newer recipe version is the one used."""

import tomllib
from types import SimpleNamespace

import pytest

from rastro.application.active_recipes import select_recipes
from rastro.application.recipe_toml import recipe_table, replace_recipe
from rastro.domain.jobs import parse_job
from rastro.domain.models import ConfigurationError, CssRecipe, JsonLdRecipe

TWO_SOURCES = """format_version = 1
recipe_memory = "r.sqlite"

[[sources]]
path = "a.html"

[sources.recipe]
strategy = "css"
record_selector = "li"

[sources.recipe.fields]
title = "b::text"

[[sources.recipe.fallbacks]]
strategy = "css"
record_selector = "li"

[sources.recipe.fallbacks.fields]
title = "i::text"

# Second source keeps this comment.
[[sources]]
path = "b.html"

[sources.recipe]
strategy = "css"
record_selector = "li"

[sources.recipe.fields]
title = "b::text"

[[fields]]
name = "title"
type = "string"
"""

NEW = CssRecipe(version=2, record_selector='li[data-x="q\\\\"]', fields={"title": "em::text"})


def test_second_source_recipe_is_replaced_and_everything_else_kept():
    out = replace_recipe(TWO_SOURCES, NEW, source=1)
    second = TWO_SOURCES.index("[sources.recipe]", TWO_SOURCES.index("# Second source"))
    assert out.startswith(TWO_SOURCES[:second])
    assert out.endswith(TWO_SOURCES[TWO_SOURCES.index("[[fields]]") :])
    job = parse_job(out, format="toml")
    assert job.sources[1].recipe == NEW
    assert job.sources[0].recipe == parse_job(TWO_SOURCES, format="toml").sources[0].recipe


def test_fallbacks_are_rendered_as_tables_and_round_trip():
    recipe = parse_job(TWO_SOURCES, format="toml").sources[0].recipe
    bumped = recipe.model_copy(
        update={
            "version": 3,
            "fallbacks": tuple(f.model_copy(update={"version": 3}) for f in recipe.fallbacks),
        }
    )
    out = replace_recipe(TWO_SOURCES, bumped, source=0)
    assert "# Second source keeps this comment." in out
    assert parse_job(out, format="toml").sources[0].recipe == bumped


def test_control_characters_and_pointer_lists_are_valid_toml():
    recipe = JsonLdRecipe(
        fields={"title": ["/name", "/headline"], "odd key": "/x"}, item_type="A\u007fB\tC\u00e9"
    )
    table = recipe_table(recipe)
    text = replace_recipe('[recipe]\nstrategy = "jsonld"\n', recipe, source=None)
    assert tomllib.loads(text)["recipe"] == table


def test_inline_or_split_recipes_are_refused():
    inline = 'format_version = 1\n[[sources]]\npath = "a.html"\nrecipe = { strategy = "css" }\n'
    with pytest.raises(ConfigurationError):
        replace_recipe(inline, NEW, source=0)
    split = TWO_SOURCES.replace(
        "[[sources.recipe.fallbacks]]",
        '[sources.region]\nlocale = "en"\n\n[[sources.recipe.fallbacks]]',
        1,
    )
    with pytest.raises(ConfigurationError):
        replace_recipe(split, NEW, source=0)


def stored(version):
    return SimpleNamespace(recipe=SimpleNamespace(version=version))


class Repository:
    def __init__(self, version):
        self.version = version

    def get(self, key, *, version=None):
        return stored(self.version) if self.version else None


def test_job_recipe_wins_unless_memory_has_a_higher_version():
    job = parse_job(TWO_SOURCES, format="toml")
    for version, origin in ((None, "job"), (1, "job"), (2, "memory")):
        active, selections = select_recipes(job, Repository(version))
        assert {s.recipe_origin for s in selections} == {origin}
        if origin == "job":
            assert active is job
        else:
            assert all(item.recipe is None for item in active.sources)
            assert selections[0].recipe_version == 2


def test_pinned_version_and_missing_memory_are_reported():
    job = parse_job(TWO_SOURCES.replace('recipe_memory = "r.sqlite"', ""), format="toml")
    _, selections = select_recipes(job, None)
    assert [s.recipe_version for s in selections] == [1, 1]
