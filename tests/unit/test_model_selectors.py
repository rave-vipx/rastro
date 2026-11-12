"""Model-declared CSS selectors become a recipe (INT-001, REC-003)."""

from typing import Annotated, ClassVar

import pytest
from pydantic import AnyUrl, BaseModel, HttpUrl

import rastro
from rastro import ConfigurationError, CssRecipe
from rastro.domain.model_selectors import WHOLE_DOCUMENT, recipe_from_model


class Offer(BaseModel):
    __records__: ClassVar[str] = "li.job"
    title: Annotated[str, rastro.css("h3::text")]
    url: Annotated[HttpUrl, rastro.css("a::attr(href)")]
    salary: Annotated[int | None, rastro.css(".salary::text")] = None


class Plain(BaseModel):
    title: str
    url: HttpUrl
    salary: int | None = None


def test_markers_build_a_css_recipe_with_the_model_record_selector():
    recipe = recipe_from_model(Offer)
    assert recipe == CssRecipe(
        record_selector="li.job",
        fields={"title": "h3::text", "url": "a::attr(href)", "salary": ".salary::text"},
    )


def test_records_argument_wins_and_default_is_the_whole_document():
    assert recipe_from_model(Offer, records="article").record_selector == "article"

    class Page(BaseModel):
        heading: Annotated[str, rastro.css("h1::text")]

    assert recipe_from_model(Page).record_selector == WHOLE_DOCUMENT


def test_fields_override_and_extend_markers():
    recipe = recipe_from_model(Offer, fields={"title": "h2::text"})
    assert recipe.fields["title"] == "h2::text"
    assert recipe_from_model(Plain, fields={"title": "h1::text"}).fields == {"title": "h1::text"}


def test_no_selectors_returns_none():
    assert recipe_from_model(Plain) is None


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"fields": {"missing": "p::text"}}, "declared model fields"),
        ({"fields": {"title": "h3"}}, "::text or ::attr"),
        ({"records": 3}, "CSS selector string"),
    ],
)
def test_invalid_declarations_raise_configuration_errors(kwargs, reason):
    with pytest.raises(ConfigurationError, match=reason):
        recipe_from_model(Offer, **kwargs)


def test_marker_rejects_blank_and_duplicate_selectors():
    with pytest.raises(ConfigurationError, match="nonempty"):
        rastro.css("  ")

    class Twice(BaseModel):
        title: Annotated[str, rastro.css("h1::text"), rastro.css("h2::text")]

    with pytest.raises(ConfigurationError, match="more than one"):
        recipe_from_model(Twice)


def test_markers_do_not_change_the_json_schema_identity():
    class Same(BaseModel):
        title: Annotated[str, rastro.css("h3::text")]
        url: Annotated[HttpUrl, rastro.css("a::attr(href)")]
        salary: Annotated[int | None, rastro.css(".salary::text")] = None

    plain = Plain.model_json_schema()
    marked = Same.model_json_schema()
    assert marked["properties"] == plain["properties"]
    assert marked["required"] == plain["required"]


def test_relative_links_resolve_only_on_url_fields():
    class Link(BaseModel):
        label: Annotated[str, rastro.css("a::text")]
        href: Annotated[HttpUrl, rastro.css("a::attr(href)")]
        other: Annotated[AnyUrl | None, rastro.css("b::text")] = None

    context = {"source_url": "https://acme.example/careers/list"}
    item = Link.model_validate(
        {"label": "/not-a-link", "href": "/jobs/1", "other": "../x"}, context=context
    )
    assert item.label == "/not-a-link"
    assert str(item.href) == "https://acme.example/jobs/1"
    assert str(item.other) == "https://acme.example/x"
