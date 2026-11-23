"""Every model proposal path tells the model the source's region timezone."""

import json

import pytest
from pydantic import BaseModel

import rastro
from rastro.application.engine import Rastro
from rastro.application.model_repair import ModelAssist, assist_repair
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.models import ConfigurationError, CssRecipe, Source

PAGE = """<html><body><ul>
<li class="show"><h3>Night Run</h3><time>Sat 21:00</time></li>
<li class="show"><h3>Dawn Walk</h3><time>Sun 07:00</time></li>
</ul></body></html>"""
RECIPE = {"strategy": "css", "record_selector": "li.show", "fields": {"title": "h3::text"}}


class Show(BaseModel):
    title: str


class FakeModel:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def generate(self, request, policy):
        self.requests.append(request)
        return json.dumps(RECIPE)

    def zones(self) -> list[object]:
        return [json.loads(r.content).get("region_timezone") for r in self.requests]


@pytest.mark.asyncio
async def test_learning_sends_the_source_timezone():
    model = FakeModel()
    source = Source(url="https://shows.example/", timezone="Europe/Madrid")
    await Rastro(model=model).extract_html_async(PAGE, source=source, schema=Show)
    assert model.zones() and set(model.zones()) == {"Europe/Madrid"}


@pytest.mark.asyncio
async def test_model_repair_sends_the_source_timezone():
    model = FakeModel()
    previous = CssRecipe(record_selector="li.gone", fields={"title": "h3::text"}, version=1)

    class NoAdaptive:
        def similar_records(self, html, anchor_text, *, limits):
            return ()

        def stable_selector(self, html, locator, *, limits):
            return None

        def anchor_by_label(self, html, record_selector, label, *, limits):
            return None

    def refuse(candidate):
        raise ConfigurationError("this test only reads the prompt")

    await assist_repair(
        ModelAssist(model, InferencePolicy()),
        [],
        html=PAGE,
        schema=Show,
        previous=previous,
        gate=refuse,
        rows=lambda recipe: [],
        history=[],
        adaptive=NoAdaptive(),
        timezone="America/Montevideo",
    )
    assert model.zones() and set(model.zones()) == {"America/Montevideo"}


def test_suggest_with_a_model_sends_a_timezone(offline_cli_loop):
    model = FakeModel()
    suggestion = rastro.suggest(PAGE, Show, model=model, url="https://shows.example/")
    assert suggestion.recipe.record_selector == "li.show"
    assert set(model.zones()) == {"UTC"}
